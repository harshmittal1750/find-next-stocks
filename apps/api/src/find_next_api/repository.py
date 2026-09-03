from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import psycopg
from find_next_pipeline.legacy import clean_legacy_stock
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

LATEST_RANKING_RUN_SQL = """
SELECT id FROM ranking_runs ORDER BY created_at DESC LIMIT 1
"""

RANKED_STOCKS_SQL = """
SELECT
    instruments.ticker,
    ranked_stocks.rank,
    ranked_stocks.score,
    ranked_stocks.score_status,
    ranked_stocks.factors,
    ranked_stocks.data_coverage
FROM ranked_stocks
JOIN instruments ON instruments.id = ranked_stocks.instrument_id
WHERE ranked_stocks.run_id = %s
"""

# factors JSONB keys carried straight onto the stock dict under the same name.
RANKED_STOCKS_FACTOR_FIELDS = (
    "g_quality",
    "g_smart_money",
    "g_valuation",
    "g_growth",
    "g_price_setup",
    "g_analyst",
    "g_momentum",
    "model_score",
    "rank_vs_staged",
    "score_vs_staged",
    "movement_vs_staged",
)

# Renames only. A field absent from this map passes through under its own name —
# it is NOT filtered out.
#
# This used to be an allow-list, and every observation field missing from it was
# silently dropped. Four providers' worth of new fields (delivery, 52-week distance,
# moving averages) reached Postgres and never reached the scorer, which went on ranking
# from stale archived CSV values while fresh ones sat unused one table away. A
# hand-maintained list of "fields we accept" fails open the wrong way: forgetting an
# entry loses data quietly instead of erroring.

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
    "momentum": ("fiftyTwoWeekChangePercent", "px_vs_200dma", "rsi14"),
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


CURRENT_METRICS_SQL = """
SELECT stock_instruments.ticker,
       current_metrics.field,
       current_metrics.numeric_value,
       current_metrics.text_value,
       current_metrics.origin
FROM current_metrics
JOIN stock_instruments ON stock_instruments.id = current_metrics.instrument_id
"""


FIELD_TIMESTAMPS_SQL = """
SELECT current_metrics.field, current_metrics.observed_at
FROM current_metrics
JOIN stock_instruments ON stock_instruments.id = current_metrics.instrument_id
WHERE stock_instruments.ticker = %s
"""


def stocks_from_current_metrics(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pivot the current_metrics view into one dict per stock.

    Replaces a three-stage Python merge (archive CSVs, then live observations, then the
    ranking run). The view already resolves precedence in SQL, and doing it once there
    rather than once here means the scoring job and the API cannot disagree about what a
    stock's ROE is — which they did: the view said 0.0748 while this module served
    0.0893 from the archive.

    `clean_legacy_stock` still runs per stock: the ownership sanity rule (promoter plus
    institutional cannot exceed 100%) is a property of the record, not of the source.
    """
    stocks: dict[str, dict[str, Any]] = {}
    origins: dict[str, dict[str, str]] = {}
    for row in rows:
        ticker = str(row["ticker"]).upper()
        stock = stocks.setdefault(ticker, {"ticker": ticker})
        value = row["numeric_value"] if row["numeric_value"] is not None else row["text_value"]
        if value is None:
            continue
        stock[row["field"]] = float(value) if row["numeric_value"] is not None else value
        origins.setdefault(ticker, {})[row["field"]] = row["origin"]
    for ticker, stock in stocks.items():
        # Kept so the dashboard can still show where each value came from.
        stock["field_origins"] = origins.get(ticker, {})
    return [clean_legacy_stock(stock) for stock in stocks.values()]


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
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = normalize_database_url(database_url) if database_url else None

    def load(self) -> dict[str, Any]:
        """Read the dashboard from TimescaleDB. Raises if it cannot.

        This used to fall back to a tracked `dashboard-data.json` whenever the query
        raised. That fallback was worse than an outage: on 2026-09-04 a broken column
        reference in CURRENT_METRICS_SQL sent it down this path and the API answered
        200 OK with six-week-old data for every one of 1,353 stocks. The only tell was
        `data_status: "fallback"`, which nothing alerted on. A failure that looks like
        success does not get fixed.
        """
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured; the API serves only from TimescaleDB"
            )
        return self._load_database()

    def _load_database(self) -> dict[str, Any]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=3,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CURRENT_METRICS_SQL)
                current_rows = cursor.fetchall()
                # Still queried, but only to describe provenance and freshness in the
                # payload — no longer a source of stock values.
                cursor.execute(LATEST_SOURCE_SQL, (list(SOURCE_PATHS.values()),))
                rows = cursor.fetchall()
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

        if not current_rows:
            raise ValueError("current_metrics returned no rows")
        stocks = stocks_from_current_metrics(current_rows)
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
            # Named after what actually resolved the values, so a payload that quietly
            # fell back to the archive says so.
            "served_from": "TimescaleDB · current_metrics ("
            + ", ".join(sorted({row["origin"] for row in current_rows}))
            + ")",
        }

    def get_stock(self, ticker: str) -> dict[str, Any] | None:
        """One stock, with a per-field `observed_at`.

        The timestamps live here rather than on /api/v1/dashboard on purpose. Every cell
        in `current_metrics` carries an `observed_at`, but shipping 86 of them for all
        1,353 stocks would roughly double a payload that is already 6 MB, to send bytes
        that are only ever read for the one stock someone expanded. Placement follows the
        access pattern: if the table itself ever shows an "updated" column for every row,
        this belongs in the bulk payload instead.
        """
        ticker = ticker.upper()
        stock = next(
            (stock for stock in self.load()["stocks"] if stock.get("ticker", "").upper() == ticker),
            None,
        )
        if stock is None:
            return None
        with psycopg.connect(
            self.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(FIELD_TIMESTAMPS_SQL, (ticker,))
                rows = cursor.fetchall()
        # Keyed by the same field names as `field_origins`, so the two line up.
        stock["field_updated_at"] = {
            row["field"]: row["observed_at"].isoformat()
            for row in rows
            if row["observed_at"] is not None
        }
        return stock
