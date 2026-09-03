
import pytest
from find_next_api.repository import (
    DashboardRepository,
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


def test_load_raises_instead_of_serving_a_stale_fallback() -> None:
    """Regression: a database failure must not look like a successful response.

    A tracked dashboard-data.json used to sit behind `load()` under a bare except. On
    2026-09-04 a broken column reference sent it down that path and the API returned
    200 OK with six-week-old data for all 1,353 stocks; only `data_status: "fallback"`
    said otherwise, and nothing watched it. There is no fallback now, so the absence of
    a usable database has to raise.
    """
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        DashboardRepository(database_url=None).load()
