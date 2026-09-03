from find_next_pipeline.scoring import score_universe

FULL_METRICS = {
    "returnOnEquity": 0.20,
    "returnOnAssets": 0.10,
    "ebitdaMargins": 0.30,
    "profitMargins": 0.15,
    "debtToEquity": 40.0,
    "currentRatio": 1.5,
    "institutional_pct": 40.0,
    "institutions_count": 200,
    "promoter_pct": 50.0,
    "delivery_trend": 5.0,
    "avg_delivery_pct": 55.0,
    "trailingPE": 15.0,
    "priceToBook": 2.0,
    "pegRatio": 1.0,
    "enterpriseToEbitda": 8.0,
    "dividendYield": 1.5,
    "earningsGrowth": 0.15,
    "revenueGrowth": 0.10,
    "earningsQuarterlyGrowth": 0.12,
    "trailingEps": 10.0,
    "forwardEps": 12.0,
    "targetMeanPrice": 120.0,
    "recommendationMean": 2.0,
    "numberOfAnalystOpinions": 15,
    "fiftyTwoWeekChangePercent": 5.0,
    "fiftyTwoWeekHigh": 150.0,
    "fiftyDayAverage": 105.0,
    "twoHundredDayAverage": 100.0,
    "currentPrice": 100.0,
    "sector": "Industrials",
}


def _stock(ticker: str, **overrides) -> dict:
    row = {"ticker": ticker, "shortName": ticker, **FULL_METRICS}
    row.update(overrides)
    return row


def test_higher_roe_and_cheaper_valuation_score_better() -> None:
    universe = [
        _stock("STRONG", returnOnEquity=0.35, trailingPE=8.0),
        _stock("WEAK", returnOnEquity=0.05, trailingPE=60.0),
    ]

    scored = {row["ticker"]: row for row in score_universe(universe)}

    assert scored["STRONG"]["g_quality"] > scored["WEAK"]["g_quality"]
    assert scored["STRONG"]["g_valuation"] > scored["WEAK"]["g_valuation"]
    assert scored["STRONG"]["final_score"] > scored["WEAK"]["final_score"]
    assert scored["STRONG"]["rank"] < scored["WEAK"]["rank"]


def test_missing_metrics_fill_neutral_not_zero() -> None:
    """A single missing metric shouldn't tank a group score to 0."""
    universe = [
        _stock("COMPLETE"),
        _stock("PARTIAL", returnOnEquity=None, debtToEquity=None),
    ]

    scored = {row["ticker"]: row for row in score_universe(universe)}

    # Missing values neutral-fill to 0.5 per metric, not 0 — a group score near the
    # complete stock's, not collapsed.
    assert scored["PARTIAL"]["g_quality"] > 20.0


def test_low_coverage_stock_stays_unranked_not_dropped() -> None:
    thin = {"ticker": "THIN", "shortName": "Thin Co", "currentPrice": 50.0, "sector": "Industrials"}
    universe = [_stock("RICH"), thin]

    scored = {row["ticker"]: row for row in score_universe(universe)}

    assert "THIN" in scored  # present, not silently dropped
    assert scored["THIN"]["final_score"] is None
    assert scored["THIN"]["rank"] is None
    assert scored["THIN"]["score_status"] != "ranked"
    assert scored["RICH"]["score_status"] == "ranked"
    assert scored["RICH"]["rank"] == 1


def test_ranks_are_sequential_starting_at_one() -> None:
    universe = [_stock(f"T{i}", trailingPE=10.0 + i) for i in range(5)]

    scored = sorted(score_universe(universe), key=lambda row: row["rank"])

    assert [row["rank"] for row in scored] == [1, 2, 3, 4, 5]


def test_financial_services_skips_bank_inapplicable_metrics() -> None:
    """EV/EBITDA, debt/equity etc. aren't comparable for banks — coverage excludes them."""
    universe = [
        _stock("BANK", sector="Financial Services", debtToEquity=None, enterpriseToEbitda=None),
        _stock("INDUSTRIAL"),
    ]

    scored = {row["ticker"]: row for row in score_universe(universe)}

    # The bank isn't penalized in coverage for lacking metrics that don't apply to it.
    assert scored["BANK"]["quality_cov"] > 0
    assert scored["BANK"]["score_status"] == "ranked"
