from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from find_next_pipeline.models import MetricObservation, RawEnvelope
from find_next_pipeline.paths import ROOT


@dataclass(frozen=True)
class PersistSummary:
    raw_responses: int = 0
    observations: int = 0


class PostgresObservationWarehouse:
    """Append-only provider observations for the shared PostgreSQL store."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )

    def ensure_instruments(self, stocks: Iterable[dict[str, Any]]) -> int:
        rows = [
            (
                str(stock.get("ticker") or "").strip().upper(),
                str(stock.get("shortName") or stock.get("ticker") or "").strip(),
                stock.get("sector"),
            )
            for stock in stocks
            if stock.get("ticker")
        ]
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO instruments (ticker, exchange, company_name, sector)
                    VALUES (%s, 'NSE', %s, %s)
                    ON CONFLICT (ticker, exchange) DO UPDATE SET
                        company_name = COALESCE(EXCLUDED.company_name, instruments.company_name),
                        sector = COALESCE(EXCLUDED.sector, instruments.sector),
                        updated_at = now()
                    """,
                    rows,
                )
        return len(rows)

    def last_refreshes(self, providers: Iterable[str]) -> dict[str, str]:
        names = sorted(set(providers))
        if not names:
            return {}
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT provider, max(received_at)
                    FROM raw_api_responses
                    WHERE provider = ANY(%s)
                      AND status_code BETWEEN 200 AND 299
                    GROUP BY provider
                    """,
                    (names,),
                )
                return {
                    provider: refreshed_at.isoformat()
                    for provider, refreshed_at in cursor
                    if refreshed_at is not None
                }

    def write(
        self,
        saved_envelopes: Iterable[tuple[RawEnvelope, Path]],
        observations: Iterable[MetricObservation],
    ) -> PersistSummary:
        envelopes = list(saved_envelopes)
        candidates = list(observations)
        request_ids: dict[UUID, UUID] = {}
        raw_count = 0
        observation_count = 0
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                for envelope, path in envelopes:
                    cursor.execute(
                        """
                        INSERT INTO raw_api_responses (
                            request_id, provider, endpoint, requested_at, received_at,
                            status_code, content_sha256, storage_path, request_params
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (provider, content_sha256) DO UPDATE SET
                            provider = EXCLUDED.provider
                        RETURNING request_id
                        """,
                        (
                            envelope.request_id,
                            envelope.provider,
                            envelope.endpoint,
                            envelope.requested_at,
                            envelope.received_at,
                            envelope.status_code,
                            envelope.content_sha256,
                            self._storage_path(path),
                            Jsonb(envelope.request_params),
                        ),
                    )
                    stored_id = cursor.fetchone()[0]
                    request_ids[envelope.request_id] = stored_id
                    raw_count += 1

                tickers = sorted({item.ticker for item in candidates})
                instrument_ids: dict[str, int] = {}
                if tickers:
                    cursor.execute(
                        """
                        SELECT ticker, id
                        FROM instruments
                        WHERE exchange = 'NSE' AND ticker = ANY(%s)
                        """,
                        (tickers,),
                    )
                    instrument_ids = {ticker: instrument_id for ticker, instrument_id in cursor}

                for item in candidates:
                    instrument_id = instrument_ids.get(item.ticker)
                    if instrument_id is None:
                        continue
                    numeric_value, text_value = self._split_value(item.value)
                    cursor.execute(
                        """
                        INSERT INTO metric_observations (
                            instrument_id, field, numeric_value, text_value, unit,
                            provider, endpoint, observed_at, raw_request_id,
                            is_valid, validation_issues
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (instrument_id, field, provider, observed_at) DO NOTHING
                        """,
                        (
                            instrument_id,
                            item.field,
                            numeric_value,
                            text_value,
                            item.unit,
                            item.provider,
                            item.endpoint,
                            item.observed_at,
                            request_ids.get(item.raw_request_id),
                            item.is_valid,
                            Jsonb([issue.model_dump(mode="json") for issue in item.issues]),
                        ),
                    )
                    observation_count += max(cursor.rowcount, 0)
        return PersistSummary(raw_responses=raw_count, observations=observation_count)

    @staticmethod
    def _storage_path(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT))
        except ValueError:
            return str(path.resolve())

    @staticmethod
    def _split_value(value: float | int | str | None) -> tuple[float | int | None, str | None]:
        if isinstance(value, bool):
            return None, str(value).lower()
        if isinstance(value, (int, float)):
            return value, None
        if value is None:
            return None, None
        return None, str(value)
