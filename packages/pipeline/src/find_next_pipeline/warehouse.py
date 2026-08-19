from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import duckdb
import pandas as pd

from find_next_pipeline.models import MetricObservation
from find_next_pipeline.paths import WAREHOUSE_DIR


class DuckDbWarehouse:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or WAREHOUSE_DIR / "find_next_stocks.duckdb"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_observations (
                    observation_id UUID PRIMARY KEY,
                    ticker VARCHAR NOT NULL,
                    field VARCHAR NOT NULL,
                    value_json JSON,
                    unit VARCHAR,
                    provider VARCHAR NOT NULL,
                    endpoint VARCHAR,
                    observed_at TIMESTAMPTZ NOT NULL,
                    raw_request_id UUID,
                    is_valid BOOLEAN NOT NULL,
                    issues JSON NOT NULL
                );
                CREATE TABLE IF NOT EXISTS legacy_rankings AS
                    SELECT * FROM (SELECT NULL::INTEGER AS rank) WHERE FALSE;
                """
            )

    def write_observations(self, observations: Iterable[MetricObservation]) -> int:
        rows = [
            {
                "observation_id": str(item.observation_id),
                "ticker": item.ticker,
                "field": item.field,
                "value_json": json.dumps(item.value),
                "unit": item.unit,
                "provider": item.provider,
                "endpoint": item.endpoint,
                "observed_at": item.observed_at,
                "raw_request_id": str(item.raw_request_id) if item.raw_request_id else None,
                "is_valid": item.is_valid,
                "issues": json.dumps([issue.model_dump(mode="json") for issue in item.issues]),
            }
            for item in observations
        ]
        if not rows:
            return 0
        frame = pd.DataFrame(rows)
        with duckdb.connect(str(self.path)) as connection:
            connection.register("incoming_observations", frame)
            connection.execute(
                """
                INSERT OR REPLACE INTO metric_observations
                SELECT * FROM incoming_observations
                """
            )
        return len(rows)

    def replace_legacy_rankings(self, frame: pd.DataFrame) -> None:
        with duckdb.connect(str(self.path)) as connection:
            connection.register("legacy_import", frame)
            connection.execute("DROP TABLE IF EXISTS legacy_rankings")
            connection.execute("CREATE TABLE legacy_rankings AS SELECT * FROM legacy_import")
