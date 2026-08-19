from datetime import datetime

from find_next_api.repository import (
    apply_live_metrics,
    merge_stock_sources,
    normalize_database_url,
    parse_database_record,
)


def test_database_url_is_compatible_with_psycopg() -> None:
    assert (
        normalize_database_url("postgresql+asyncpg://user:pass@localhost:5434/db")
        == "postgresql://user:pass@localhost:5434/db"
    )


def test_database_record_parses_numbers_booleans_and_quality() -> None:
    parsed = parse_database_record(
        {
            "ticker": "GALLANTT",
            "currentPrice": "620.15",
            "pat_accum": "True",
            "data_quality__status": "valid",
            "data_quality__issues": "[]",
        }
    )

    assert parsed["ticker"] == "GALLANTT"
    assert parsed["currentPrice"] == 620.15
    assert parsed["pat_accum"] is True
    assert parsed["data_quality"] == {"status": "valid", "issues": []}


def test_ranking_values_win_and_supporting_fields_fill_gaps() -> None:
    stocks = merge_stock_sources(
        [{"ticker": "GALLANTT", "currentPrice": "620.15", "promoter_pct": "70"}],
        [[{"ticker": "GALLANTT", "currentPrice": "610", "industry": "Steel"}]],
    )

    assert stocks[0]["currentPrice"] == 620.15
    assert stocks[0]["industry"] == "Steel"
    assert stocks[0]["promoter_pct"] == 70


def test_live_metrics_override_archived_values_and_recalculate_coverage() -> None:
    stocks = [
        {
            "ticker": "GALLANTT",
            "sector": "Basic Materials",
            "currentPrice": 600,
            "fiftyTwoWeekHigh": 900,
            "fiftyTwoWeekLow": 500,
            "trailingPE": 30,
            "roe_pct": 18,
            "returnOnAssets": 0.1,
            "profitMargins": 0.1,
            "debtToEquity": 20,
            "currentRatio": 2,
        }
    ]
    rows = [
        {
            "ticker": "GALLANTT",
            "field": "current_price",
            "numeric_value": 630,
            "text_value": None,
            "provider": "upstox",
            "observed_at": datetime.fromisoformat("2026-07-23T10:00:00+00:00"),
        }
    ]

    updated = apply_live_metrics(stocks, rows)[0]

    assert updated["currentPrice"] == 630
    assert updated["pct_below_52w_high"] == 30
    assert updated["pct_above_52w_low"] == 26
    assert updated["live_fields"]["currentPrice"]["provider"] == "upstox"
    assert updated["data_cov"] > 0
