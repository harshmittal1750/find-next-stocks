from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from find_next_api.refresh_jobs import ProviderSpec, RefreshJobManager
from find_next_pipeline.models import MetricObservation, ProviderResult


class FakeProvider:
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        return ProviderResult(
            provider="fake",
            observations=[
                MetricObservation(
                    ticker=ticker,
                    field="current_price",
                    value=100 + index,
                    unit="INR",
                    provider="fake",
                )
                for index, ticker in enumerate(tickers)
            ],
        )


class FakeWarehouse:
    def __init__(self) -> None:
        self.prepared = 0
        self.written = 0
        self.refreshes = {"fake": "2026-07-23T10:00:00+00:00"}

    def ensure_instruments(self, stocks):
        self.prepared = len(stocks)
        return self.prepared

    def last_refreshes(self, providers):
        return {
            provider: refreshed_at
            for provider, refreshed_at in self.refreshes.items()
            if provider in providers
        }

    def write(self, saved_envelopes, observations):
        count = len(list(observations))
        self.written += count
        return SimpleNamespace(raw_responses=len(list(saved_envelopes)), observations=count)


def wait_for_terminal(manager: RefreshJobManager, job_id: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job and job["status"] in {"completed", "completed_with_warnings", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("refresh job did not finish")


def test_refresh_job_tracks_provider_progress_and_persistence() -> None:
    warehouse = FakeWarehouse()
    manager = RefreshJobManager(
        "postgresql://unused",
        [
            ProviderSpec(
                provider="fake",
                label="Fake provider",
                factory=lambda client: FakeProvider(),
                batch_size=2,
            ),
            ProviderSpec(
                provider="missing",
                label="Missing provider",
                factory=None,
                skip_reason="Not configured",
            ),
        ],
        warehouse=warehouse,
    )
    stocks = [
        {"ticker": "AAA", "shortName": "Alpha"},
        {"ticker": "BBB", "shortName": "Beta"},
        {"ticker": "CCC", "shortName": "Gamma"},
    ]

    manifest = {item["provider"]: item for item in manager.manifest()}

    started, already_running = manager.start(stocks)
    job = wait_for_terminal(manager, started["job_id"])
    manager.shutdown()

    assert already_running is False
    assert job["status"] == "completed_with_warnings"
    assert job["progress"] == 100
    assert job["observations_written"] == 3
    assert warehouse.prepared == 3
    assert warehouse.written == 3
    assert manifest["fake"]["last_refresh_at"] == "2026-07-23T10:00:00+00:00"
    assert manifest["missing"]["last_refresh_at"] is None
    stages = {stage["stage_id"]: stage for stage in job["stages"]}
    assert stages["fake"]["status"] == "completed"
    assert stages["fake"]["processed"] == 3
    assert stages["missing"]["status"] == "skipped"


def test_refresh_job_runs_only_selected_providers() -> None:
    manager = RefreshJobManager(
        "postgresql://unused",
        [
            ProviderSpec(
                provider="fake",
                label="Fake provider",
                factory=lambda client: FakeProvider(),
            ),
            ProviderSpec(
                provider="other",
                label="Other provider",
                factory=lambda client: FakeProvider(),
            ),
        ],
        warehouse=FakeWarehouse(),
    )

    started, already_running = manager.start(
        [{"ticker": "AAA"}],
        {"fake"},
    )
    job = wait_for_terminal(manager, started["job_id"])
    manager.shutdown()

    assert already_running is False
    assert job["status"] == "completed"
    assert job["selected_providers"] == ["fake"]
    assert {stage["stage_id"] for stage in job["stages"]} == {
        "prepare",
        "fake",
        "publish",
    }


def test_second_start_reuses_the_active_job() -> None:
    manager = RefreshJobManager(
        "postgresql://unused",
        [
            ProviderSpec(
                provider="fake",
                label="Fake",
                factory=lambda client: FakeProvider(delay=0.05),
            )
        ],
        warehouse=FakeWarehouse(),
    )
    stocks = [{"ticker": "AAA"}]

    first, _ = manager.start(stocks)
    second, already_running = manager.start(stocks)
    wait_for_terminal(manager, first["job_id"])
    manager.shutdown()

    assert already_running is True
    assert second["job_id"] == first["job_id"]
