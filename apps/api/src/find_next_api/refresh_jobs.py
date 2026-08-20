from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from find_next_pipeline.diagnostics import FailureTally
from find_next_pipeline.models import ProviderResult
from find_next_pipeline.normalization import select_canonical_metrics
from find_next_pipeline.postgres_store import PostgresObservationWarehouse
from find_next_pipeline.providers import (
    AlphaVantageProvider,
    NseValuationProvider,
    UpstoxQuoteProvider,
    YahooChartProvider,
)
from find_next_pipeline.providers.base import MarketDataProvider
from find_next_pipeline.providers.http import ArchivedHttpClient
from find_next_pipeline.raw_store import RawJsonStore

from find_next_api.config import Settings

TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed"}


class ObservationWarehouse(Protocol):
    def ensure_instruments(self, stocks: list[dict[str, Any]]) -> int: ...

    def last_refreshes(self, providers: list[str]) -> dict[str, str]: ...

    def write(self, saved_envelopes, observations): ...


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    label: str
    factory: Callable[[ArchivedHttpClient], MarketDataProvider] | None
    batch_size: int | None = None
    skip_reason: str | None = None


def provider_specs(settings: Settings) -> list[ProviderSpec]:
    upstox_token = settings.upstox_token
    alpha_key = settings.alpha_vantage_api_key.get_secret_value()
    return [
        ProviderSpec(
            provider="nse",
            label="NSE daily valuation",
            factory=lambda client: NseValuationProvider(client),
        ),
        ProviderSpec(
            provider="upstox",
            label="Upstox market quotes",
            factory=(lambda client: UpstoxQuoteProvider(client, upstox_token))
            if upstox_token
            else None,
            skip_reason=None
            if upstox_token
            else "Add UPSTOX_ANALYTICS_TOKEN or UPSTOX_ACCESS_TOKEN to .env",
        ),
        ProviderSpec(
            provider="yahoo",
            label="Yahoo market history",
            factory=lambda client: YahooChartProvider(client),
            batch_size=max(1, settings.refresh_yahoo_batch_size),
        ),
        ProviderSpec(
            provider="alpha_vantage",
            label="Alpha Vantage fundamentals",
            factory=(lambda client: AlphaVantageProvider(client, alpha_key))
            if alpha_key
            else None,
            batch_size=max(1, settings.refresh_alpha_vantage_batch_size),
            skip_reason=None if alpha_key else "Add ALPHA_VANTAGE_API_KEY to .env",
        ),
        ProviderSpec(
            provider="bse",
            label="BSE fundamentals",
            factory=None,
            skip_reason=(
                "No supported official BSE fundamentals API is configured; "
                "the preserved private endpoint is intentionally not called"
            ),
        ),
        ProviderSpec(
            provider="fmp",
            label="Financial Modeling Prep",
            factory=None,
            skip_reason="Provider adapter has not been migrated to the new pipeline",
        ),
    ]


class RefreshJobManager:
    def __init__(
        self,
        database_url: str,
        specs: list[ProviderSpec],
        *,
        warehouse: ObservationWarehouse | None = None,
        raw_store_factory: Callable[[], RawJsonStore] = RawJsonStore,
    ) -> None:
        self.database_url = database_url
        self.specs = specs
        self.warehouse = warehouse or PostgresObservationWarehouse(database_url)
        self.raw_store_factory = raw_store_factory
        self._jobs: dict[str, dict[str, Any]] = {}
        self._latest_job_id: str | None = None
        self._active_job_id: str | None = None
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-refresh")

    def manifest(self) -> list[dict[str, Any]]:
        try:
            last_refreshes = self.warehouse.last_refreshes(
                [spec.provider for spec in self.specs]
            )
        except Exception:
            last_refreshes = {}
        return [
            {
                "provider": spec.provider,
                "label": spec.label,
                "available": spec.factory is not None,
                "reason": spec.skip_reason,
                "last_refresh_at": last_refreshes.get(spec.provider),
            }
            for spec in self.specs
        ]

    def start(
        self,
        stocks: list[dict[str, Any]],
        selected_providers: set[str] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if self._active_job_id:
                active = self._jobs[self._active_job_id]
                if active["status"] not in TERMINAL_STATUSES:
                    return deepcopy(active), True

            selected_specs = [
                spec
                for spec in self.specs
                if selected_providers is None or spec.provider in selected_providers
            ]
            if not selected_specs:
                raise ValueError("Select at least one available provider")

            job_id = str(uuid4())
            now = datetime.now(UTC).isoformat()
            stages = [self._stage("prepare", "Prepare database", provider=None)]
            stages.extend(
                self._stage(spec.provider, spec.label, provider=spec.provider)
                for spec in selected_specs
            )
            stages.append(self._stage("publish", "Publish refreshed dashboard", provider=None))
            job = {
                "job_id": job_id,
                "status": "queued",
                "progress": 0.0,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "total_stocks": len(stocks),
                "selected_providers": [spec.provider for spec in selected_specs],
                "observations_written": 0,
                "raw_responses_archived": 0,
                "message": "Refresh queued",
                "stages": stages,
            }
            self._jobs[job_id] = job
            self._latest_job_id = job_id
            self._active_job_id = job_id
            self._executor.submit(
                self._run,
                job_id,
                deepcopy(stocks),
                selected_specs,
            )
            return deepcopy(job), False

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_job_id is None:
                return None
            return deepcopy(self._jobs[self._latest_job_id])

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(
        self,
        job_id: str,
        stocks: list[dict[str, Any]],
        specs: list[ProviderSpec],
    ) -> None:
        warnings = False
        try:
            self._update_job(
                job_id,
                status="running",
                started_at=datetime.now(UTC).isoformat(),
                message="Preparing the stock universe",
            )
            self._start_stage(job_id, "prepare", len(stocks))
            prepared = self.warehouse.ensure_instruments(stocks)
            self._finish_stage(
                job_id,
                "prepare",
                status="completed",
                processed=prepared,
                observations=0,
                message=f"Prepared {prepared:,} NSE instruments",
            )

            tickers = [str(stock["ticker"]).upper() for stock in stocks if stock.get("ticker")]
            for spec in specs:
                if spec.factory is None:
                    warnings = True
                    self._finish_stage(
                        job_id,
                        spec.provider,
                        status="skipped",
                        processed=0,
                        observations=0,
                        message=spec.skip_reason or "Provider unavailable",
                    )
                    continue
                stage_warning = self._run_provider(job_id, spec, tickers)
                warnings = warnings or stage_warning

            self._start_stage(job_id, "publish", len(stocks))
            self._finish_stage(
                job_id,
                "publish",
                status="completed",
                processed=len(stocks),
                observations=0,
                message="Latest valid observations are now visible to the dashboard",
            )
            final_status = "completed_with_warnings" if warnings else "completed"
            self._update_job(
                job_id,
                status=final_status,
                progress=100.0,
                completed_at=datetime.now(UTC).isoformat(),
                message=(
                    "Refresh finished; review skipped or partial providers"
                    if warnings
                    else "All configured providers refreshed"
                ),
            )
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                completed_at=datetime.now(UTC).isoformat(),
                message=f"Refresh failed: {exc}",
            )
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def _run_provider(
        self,
        job_id: str,
        spec: ProviderSpec,
        tickers: list[str],
    ) -> bool:
        store = self.raw_store_factory()
        client = ArchivedHttpClient(store)
        provider = spec.factory(client) if spec.factory else None
        if provider is None:
            return True
        batches = (
            [tickers]
            if spec.batch_size is None
            else [
                tickers[index : index + spec.batch_size]
                for index in range(0, len(tickers), spec.batch_size)
            ]
        )
        self._start_stage(job_id, spec.provider, len(tickers))
        processed = 0
        observations = 0
        issues = 0
        # Failures are tallied by kind rather than overwriting one another, so a rare
        # schema error is not buried under twenty identical rate-limit failures.
        failures = FailureTally()
        for batch in batches:
            try:
                result: ProviderResult = asyncio.run(provider.fetch(batch))
                _, normalized = select_canonical_metrics(result.observations)
                persisted = self.warehouse.write(store.drain_saved(), normalized)
                observations += persisted.observations
                issues += len(result.issues)
                # Providers report most failures as issues rather than raising, so this
                # is the path that actually carries a rate limit or a schema change.
                for issue in result.issues:
                    # raw_value carries the ticker; `field` is the literal "ticker".
                    subject = issue.raw_value if isinstance(issue.raw_value, str) else None
                    failures.record_issue(issue.code, issue.message, subject=subject)
                self._increment_totals(
                    job_id,
                    observations=persisted.observations,
                    raw_responses=persisted.raw_responses,
                )
            except Exception as exc:
                issues += 1
                saved = store.drain_saved()
                if saved:
                    persisted = self.warehouse.write(saved, [])
                    self._increment_totals(
                        job_id,
                        observations=0,
                        raw_responses=persisted.raw_responses,
                    )
                failures.record(exc, subject=batch[0] if batch else None)
                self._set_stage_message(job_id, spec.provider, failures.summary())
            processed += len(batch)
            self._progress_stage(job_id, spec.provider, processed, observations)

        breakdown = f" — {failures.summary()}" if failures else ""
        if observations == 0 and issues:
            status = "failed"
            message = f"No usable observations; {issues} provider issue(s){breakdown}"
        elif issues:
            status = "completed"
            message = (
                f"Stored {observations:,} observations with "
                f"{issues} provider issue(s){breakdown}"
            )
        else:
            status = "completed"
            message = f"Stored {observations:,} observations"
        self._finish_stage(
            job_id,
            spec.provider,
            status=status,
            processed=processed,
            observations=observations,
            message=message,
        )
        return bool(issues)

    @staticmethod
    def _stage(stage_id: str, label: str, provider: str | None) -> dict[str, Any]:
        return {
            "stage_id": stage_id,
            "provider": provider,
            "label": label,
            "status": "pending",
            "progress": 0.0,
            "processed": 0,
            "total": 0,
            "observations_written": 0,
            "message": "Waiting",
            "started_at": None,
            "completed_at": None,
        }

    def _start_stage(self, job_id: str, stage_id: str, total: int) -> None:
        with self._lock:
            stage = self._find_stage(job_id, stage_id)
            stage.update(
                status="running",
                total=total,
                started_at=datetime.now(UTC).isoformat(),
                message="Fetching and validating provider data",
            )
            self._recalculate(job_id)

    def _progress_stage(
        self,
        job_id: str,
        stage_id: str,
        processed: int,
        observations: int,
    ) -> None:
        with self._lock:
            stage = self._find_stage(job_id, stage_id)
            total = max(stage["total"], 1)
            stage.update(
                processed=processed,
                observations_written=observations,
                progress=round(min(100, processed / total * 100), 1),
                message=f"Processed {processed:,} of {stage['total']:,} stocks",
            )
            self._recalculate(job_id)

    def _finish_stage(
        self,
        job_id: str,
        stage_id: str,
        *,
        status: str,
        processed: int,
        observations: int,
        message: str,
    ) -> None:
        with self._lock:
            stage = self._find_stage(job_id, stage_id)
            stage.update(
                status=status,
                progress=100.0,
                processed=processed,
                observations_written=observations,
                message=message,
                completed_at=datetime.now(UTC).isoformat(),
            )
            self._recalculate(job_id)

    def _set_stage_message(self, job_id: str, stage_id: str, message: str) -> None:
        with self._lock:
            self._find_stage(job_id, stage_id)["message"] = message

    def _increment_totals(self, job_id: str, *, observations: int, raw_responses: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["observations_written"] += observations
            job["raw_responses_archived"] += raw_responses

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(updates)

    def _find_stage(self, job_id: str, stage_id: str) -> dict[str, Any]:
        return next(
            stage for stage in self._jobs[job_id]["stages"] if stage["stage_id"] == stage_id
        )

    def _recalculate(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job["progress"] = round(
            sum(stage["progress"] for stage in job["stages"]) / len(job["stages"]), 1
        )
