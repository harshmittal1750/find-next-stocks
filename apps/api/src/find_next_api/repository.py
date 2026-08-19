from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from find_next_pipeline.legacy import clean_legacy_stock
from find_next_pipeline.paths import SNAPSHOT_DIR
from psycopg.rows import dict_row

SOURCE_PATHS = {
    "ranking": "data/exports/latest-ranking.csv",
    "fundamentals": (
        "legacy/2026-05-31-undervalued-fundamentally-strong-stocks/data/raw_fundamentals.csv"
    ),
    "screen": ("legacy/2026-05-31-undervalued-fundamentally-strong-stocks/data/screen_results.csv"),
    "shareholding": (
        "legacy/2026-05-31-undervalued-fundamentally-strong-stocks/data/shareholding_layer.csv"
    ),
    "rank_history": (
        "legacy/2026-05-31-undervalued-fundamentally-strong-stocks/data/rank_tracker.csv"
    ),
}

NUMERIC_FIELDS = {
    "rank",
    "mcap_cr",
    "currentPrice",
    "pct_below_52w_high",
    "pct_above_52w_low",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "pegRatio",
    "roe_pct",
    "returnOnEquity",
    "returnOnAssets",
    "debtToEquity",
    "earningsGrowth",
    "earningsQuarterlyGrowth",
    "revenueGrowth",
    "profitMargins",
    "operatingMargins",
    "grossMargins",
    "ebitdaMargins",
    "margin_pct",
    "dividendYield",
    "marketCap",
    "currentRatio",
    "quickRatio",
    "freeCashflow",
    "totalCashPerShare",
    "bookValue",
    "targetMeanPrice",
    "targetHighPrice",
    "targetLowPrice",
    "recommendationMean",
    "numberOfAnalystOpinions",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "twoHundredDayAverage",
    "fiftyDayAverage",
    "fiftyTwoWeekChangePercent",
    "beta",
    "enterpriseToEbitda",
    "enterpriseValue",
    "trailingEps",
    "forwardEps",
    "heldPercentInsiders",
    "heldPercentInstitutions",
    "promoter_pct",
    "institutional_pct",
    "institutions_count",
    "avg_delivery_pct",
    "delivery_trend",
    "n_inst_buy",
    "n_inst_sell",
    "ownership_score",
    "shareholding_score",
    "upside_pct",
    "data_cov",
    "quality_cov",
    "valuation_cov",
    "g_quality",
    "g_smart_money",
    "g_valuation",
    "g_growth",
    "g_price_setup",
    "g_analyst",
    "g_momentum",
    "model_score",
    "final_score",
    "current_rank",
    "current_score",
    "staged_rank",
    "staged_score",
    "pushed_rank",
    "pushed_score",
    "rank_vs_staged",
    "score_vs_staged",
    "staged_rank_vs_pushed",
    "staged_score_vs_pushed",
    "rank_vs_pushed",
    "score_vs_pushed",
    "rank_chg",
    "price_chg_pct",
}

BOOLEAN_FIELDS = {"pat_accum", "pat_trap", "pat_near_low"}

LATEST_SOURCE_SQL = """
WITH latest_files AS (
    SELECT DISTINCT ON (source_path)
        source_path,
        content_sha256,
        source_modified_at,
        imported_at,
        row_count
    FROM archive.csv_files
    WHERE source_path = ANY(%s)
    ORDER BY source_path, source_modified_at DESC, imported_at DESC
)
SELECT
    latest.source_path,
    latest.content_sha256,
    latest.source_modified_at,
    latest.imported_at,
    latest.row_count,
    rows.row_number,
    rows.record
FROM latest_files AS latest
JOIN archive.csv_rows AS rows
    ON rows.source_path = latest.source_path
    AND rows.content_sha256 = latest.content_sha256
ORDER BY latest.source_path, rows.row_number
"""

LATEST_LIVE_METRICS_SQL = """
SELECT DISTINCT ON (instruments.ticker, observations.field)
    instruments.ticker,
    observations.field,
    observations.numeric_value,
    observations.text_value,
    observations.unit,
    observations.provider,
    observations.observed_at
FROM metric_observations AS observations
JOIN instruments ON instruments.id = observations.instrument_id
WHERE observations.is_valid
ORDER BY
    instruments.ticker,
    observations.field,
    CASE observations.provider
        WHEN 'nse' THEN 10
        WHEN 'bse' THEN 10
        WHEN 'upstox' THEN 30
        WHEN 'alpha_vantage' THEN 40
        WHEN 'fmp' THEN 50
        WHEN 'yahoo' THEN 80
        ELSE 1000
    END,
    observations.observed_at DESC,
    observations.ingested_at DESC
"""

LIVE_SOURCE_SQL = """
SELECT
    provider,
    max(observed_at) AS source_modified_at,
    max(ingested_at) AS database_imported_at,
    count(*) FILTER (WHERE is_valid) AS row_count
FROM metric_observations
GROUP BY provider
ORDER BY provider
"""

LIVE_FIELD_MAP = {
    "current_price": "currentPrice",
    "previous_close": "previousClose",
    "fifty_two_week_high": "fiftyTwoWeekHigh",
    "fifty_two_week_low": "fiftyTwoWeekLow",
    "day_open": "dayOpen",
    "day_high": "dayHigh",
    "day_low": "dayLow",
    "day_volume": "dayVolume",
    "market_cap": "marketCap",
    "trailing_pe": "trailingPE",
    "price_to_book": "priceToBook",
    "roe_pct": "roe_pct",
    "profit_margin_pct": "profitMargins",
    "sector": "sector",
}

COVERAGE_GROUPS = {
    "quality": (
        "roe_pct",
        "returnOnAssets",
        "ebitdaMargins",
        "debtToEquity",
        "currentRatio",
    ),
    "smart_money": (
        "institutional_pct",
        "institutions_count",
        "promoter_pct",
        "delivery_trend",
        "avg_delivery_pct",
    ),
    "valuation": (
        "trailingPE",
        "priceToBook",
        "pegRatio",
        "enterpriseToEbitda",
        "dividendYield",
    ),
    "growth": (
        "earningsGrowth",
        "revenueGrowth",
        "earningsQuarterlyGrowth",
        "fwd_eps_growth",
    ),
    "price_setup": ("pct_below_52w_high", "px_vs_50dma"),
    "analyst": ("upside_pct", "recommendationMean", "numberOfAnalystOpinions"),
    "momentum": ("fiftyTwoWeekChangePercent", "px_vs_200dma"),
}

COVERAGE_WEIGHTS = {
    "quality": 2.5,
    "smart_money": 2.0,
    "valuation": 2.0,
    "growth": 1.75,
    "price_setup": 1.5,
    "analyst": 1.25,
    "momentum": 0.75,
}

FINANCIAL_NOT_APPLICABLE = {
    "ebitdaMargins",
    "debtToEquity",
    "currentRatio",
    "enterpriseToEbitda",
}


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_database_record(record: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for field, value in record.items():
        if value == "":
            parsed[field] = None
        elif field in NUMERIC_FIELDS:
            parsed[field] = _numeric(value)
        elif field in BOOLEAN_FIELDS:
            parsed[field] = str(value).casefold() == "true"
        else:
            parsed[field] = value

    quality_status = parsed.pop("data_quality__status", None)
    raw_issues = parsed.pop("data_quality__issues", None)
    issues: list[dict[str, Any]] = []
    if isinstance(raw_issues, str) and raw_issues:
        try:
            loaded = json.loads(raw_issues)
            if isinstance(loaded, list):
                issues = loaded
        except json.JSONDecodeError:
            issues = []
    parsed["data_quality"] = {
        "status": quality_status or ("review" if issues else "valid"),
        "issues": issues,
    }
    return parsed


def merge_stock_sources(
    ranking: Iterable[dict[str, Any]],
    supporting: Iterable[Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    stocks = {
        str(record.get("ticker", "")).upper(): parse_database_record(record)
        for record in ranking
        if record.get("ticker")
    }
    for source_records in supporting:
        for raw_record in source_records:
            ticker = str(raw_record.get("ticker", "")).upper()
            if not ticker or ticker not in stocks:
                continue
            supplemental = parse_database_record(raw_record)
            for field, value in supplemental.items():
                if field == "data_quality":
                    continue
                if field not in stocks[ticker] or stocks[ticker][field] is None:
                    stocks[ticker][field] = value
    return [clean_legacy_stock(stock) for stock in stocks.values()]


def apply_live_metrics(
    stocks: list[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_ticker = {str(stock.get("ticker", "")).upper(): stock for stock in stocks}
    for row in rows:
        stock = by_ticker.get(str(row["ticker"]).upper())
        target = LIVE_FIELD_MAP.get(row["field"])
        if stock is None or target is None:
            continue
        value = row["numeric_value"] if row["numeric_value"] is not None else row["text_value"]
        if value is None:
            continue
        numeric = _numeric(value)
        if row["field"] == "profit_margin_pct" and numeric is not None:
            value = numeric / 100
        elif numeric is not None and row["text_value"] is None:
            value = numeric
        stock[target] = value
        stock.setdefault("live_fields", {})[target] = {
            "provider": row["provider"],
            "observed_at": row["observed_at"].isoformat(),
        }

    for stock in stocks:
        price = _numeric(stock.get("currentPrice"))
        high = _numeric(stock.get("fiftyTwoWeekHigh"))
        low = _numeric(stock.get("fiftyTwoWeekLow"))
        market_cap = _numeric(stock.get("marketCap"))
        if price is not None and high and high > 0:
            stock["pct_below_52w_high"] = round((high - price) / high * 100, 2)
        if price is not None and low and low > 0:
            stock["pct_above_52w_low"] = round((price - low) / low * 100, 2)
        if market_cap is not None:
            stock["mcap_cr"] = round(market_cap / 10_000_000, 2)
        _recalculate_coverage(stock)
    return stocks


def _has_metric(stock: dict[str, Any], metric: str) -> bool:
    if metric == "ebitdaMargins":
        return _numeric(stock.get("ebitdaMargins")) is not None or _numeric(
            stock.get("profitMargins")
        ) is not None
    if metric == "fwd_eps_growth":
        trailing = _numeric(stock.get("trailingEps"))
        return (
            trailing is not None
            and trailing > 0
            and _numeric(stock.get("forwardEps")) is not None
        )
    if metric == "px_vs_50dma":
        return _numeric(stock.get("currentPrice")) is not None and _numeric(
            stock.get("fiftyDayAverage")
        ) is not None
    if metric == "px_vs_200dma":
        return _numeric(stock.get("currentPrice")) is not None and _numeric(
            stock.get("twoHundredDayAverage")
        ) is not None
    if metric == "upside_pct":
        return _numeric(stock.get("targetMeanPrice")) is not None and _numeric(
            stock.get("currentPrice")
        ) is not None
    return _numeric(stock.get(metric)) is not None


def _recalculate_coverage(stock: dict[str, Any]) -> None:
    is_financial = str(stock.get("sector") or "").casefold() == "financial services"
    group_coverage: dict[str, float] = {}
    for group, metrics in COVERAGE_GROUPS.items():
        applicable = [
            metric
            for metric in metrics
            if not (is_financial and metric in FINANCIAL_NOT_APPLICABLE)
        ]
        present = sum(_has_metric(stock, metric) for metric in applicable)
        group_coverage[group] = present / len(applicable) * 100 if applicable else 0
    total_weight = sum(COVERAGE_WEIGHTS.values())
    stock["data_cov"] = round(
        sum(COVERAGE_WEIGHTS[group] * group_coverage[group] for group in COVERAGE_GROUPS)
        / total_weight,
        1,
    )
    stock["quality_cov"] = round(group_coverage["quality"], 1)
    stock["valuation_cov"] = round(group_coverage["valuation"], 1)


class DashboardRepository:
    def __init__(
        self,
        snapshot_dir: Path = SNAPSHOT_DIR,
        database_url: str | None = None,
    ) -> None:
        self.snapshot_path = snapshot_dir / "dashboard-data.json"
        self.database_url = normalize_database_url(database_url) if database_url else None

    def load(self) -> dict[str, Any]:
        if self.database_url:
            try:
                return self._load_database()
            except (psycopg.Error, KeyError, TypeError, ValueError):
                return self._load_snapshot(fallback=True)
        return self._load_snapshot(fallback=False)

    def _load_database(self) -> dict[str, Any]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=3,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(LATEST_SOURCE_SQL, (list(SOURCE_PATHS.values()),))
                rows = cursor.fetchall()
                cursor.execute(LATEST_LIVE_METRICS_SQL)
                live_rows = cursor.fetchall()
                cursor.execute(LIVE_SOURCE_SQL)
                live_source_rows = cursor.fetchall()

        records: dict[str, list[dict[str, Any]]] = {key: [] for key in SOURCE_PATHS}
        metadata: dict[str, dict[str, Any]] = {}
        key_by_path = {path: key for key, path in SOURCE_PATHS.items()}
        for row in rows:
            source_path = row["source_path"]
            source_key = key_by_path[source_path]
            records[source_key].append(row["record"])
            metadata[source_key] = {
                "name": source_key,
                "source_path": source_path,
                "source_modified_at": row["source_modified_at"].isoformat(),
                "database_imported_at": row["imported_at"].isoformat(),
                "row_count": row["row_count"],
                "sha256": row["content_sha256"],
            }

        if not records["ranking"]:
            raise ValueError("Latest ranking source is missing from PostgreSQL")
        stocks = merge_stock_sources(
            records["ranking"],
            (
                records["fundamentals"],
                records["screen"],
                records["shareholding"],
                records["rank_history"],
            ),
        )
        stocks = apply_live_metrics(stocks, live_rows)
        sources = [metadata[key] for key in SOURCE_PATHS if key in metadata]
        sources.extend(
            {
                "name": row["provider"],
                "source_path": "metric_observations",
                "source_modified_at": row["source_modified_at"].isoformat(),
                "database_imported_at": row["database_imported_at"].isoformat(),
                "row_count": row["row_count"],
                "sha256": "",
            }
            for row in live_source_rows
            if row["source_modified_at"] is not None
        )
        latest_source_at = max(
            datetime.fromisoformat(source["source_modified_at"]) for source in sources
        )
        field_count = len({field for stock in stocks for field in stock})
        return {
            "schema_version": 3,
            "generated_at": latest_source_at.astimezone(UTC).isoformat(),
            "refreshed": latest_source_at.astimezone(UTC).strftime("%d %b %Y %H:%M UTC"),
            "record_count": len(stocks),
            "field_count": field_count,
            "stocks": stocks,
            "sources": sources,
            "freshness": {
                "latest_source_at": latest_source_at.astimezone(UTC).isoformat(),
                "checked_at": datetime.now(UTC).isoformat(),
                "scope": "latest available in TimescaleDB",
            },
            "data_status": "ready",
            "storage_backend": "postgres",
            "served_from": (
                "TimescaleDB · archive + live observations"
                if live_rows
                else "TimescaleDB · archive schema"
            ),
        }

    def _load_snapshot(self, *, fallback: bool) -> dict[str, Any]:
        if not self.snapshot_path.exists():
            return {
                "schema_version": 3,
                "record_count": 0,
                "generated_at": None,
                "stocks": [],
                "sources": [],
                "data_status": "empty",
                "storage_backend": "none",
                "served_from": "No data source",
            }
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        stocks = [clean_legacy_stock(stock) for stock in payload.get("stocks", [])]
        payload["stocks"] = stocks
        payload["record_count"] = len(stocks)
        payload["data_status"] = "fallback" if fallback else "ready"
        payload["storage_backend"] = "json"
        payload["served_from"] = "JSON fallback" if fallback else self.snapshot_path.name
        payload.setdefault("sources", [])
        return payload

    def get_stock(self, ticker: str) -> dict[str, Any] | None:
        ticker = ticker.upper()
        return next(
            (stock for stock in self.load()["stocks"] if stock.get("ticker", "").upper() == ticker),
            None,
        )
