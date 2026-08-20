from __future__ import annotations

import json

from find_next_pipeline.coverage import (
    GapReason,
    canonical_field,
    classify_field,
    coverage_summary,
    explain_gap,
    is_blank,
)
from find_next_pipeline.paths import SNAPSHOT_DIR


def test_both_field_vocabularies_map_to_one_name() -> None:
    # The pipeline emits snake_case; the legacy snapshot still carries provider camelCase.
    assert canonical_field("trailingPE") == "trailing_pe"
    assert canonical_field("trailing_pe") == "trailing_pe"
    assert canonical_field("recommendationMean") == "recommendation_mean"
    assert canonical_field("returnOnEquity") == "roe_pct"


def test_analyst_fields_are_not_reported_as_recoverable() -> None:
    for field in ("recommendation_mean", "targetMeanPrice", "forward_pe", "upside_pct"):
        assert classify_field(field) is GapReason.ANALYST


def test_blank_detection_covers_nan_and_empty_strings() -> None:
    assert is_blank(None)
    assert is_blank(float("nan"))
    assert is_blank("   ")
    assert not is_blank(0)
    assert not is_blank(0.0)
    assert not is_blank("Energy")


def test_loss_making_company_has_no_pe_or_peg() -> None:
    # OMAXE: a loss-making real-estate company no broker covers. Its blank trailing_pe is
    # arithmetic, not a fetching failure.
    omaxe = {"ticker": "OMAXE", "sector": "Real Estate", "trailing_eps": -4.2,
             "earnings_growth": None, "profit_margin_pct": -12.0}
    assert explain_gap("trailingPE", omaxe) is GapReason.UNDEFINED
    assert explain_gap("pegRatio", omaxe) is GapReason.UNDEFINED
    assert explain_gap("earningsGrowth", omaxe) is GapReason.UNDEFINED
    assert explain_gap("recommendationMean", omaxe) is GapReason.ANALYST


def test_profitable_company_missing_pe_is_recoverable() -> None:
    profitable = {"ticker": "TCS", "sector": "Technology", "trailing_eps": 120.0,
                  "earnings_growth": 0.11, "profit_margin_pct": 18.0}
    assert explain_gap("trailingPE", profitable) is GapReason.RECOVERABLE


def test_bank_balance_sheet_ratios_are_not_applicable() -> None:
    bank = {"ticker": "HDFCBANK", "sector": "Financial Services", "trailing_eps": 80.0}
    assert explain_gap("current_ratio", bank) is GapReason.NOT_APPLICABLE
    assert explain_gap("quickRatio", bank) is GapReason.NOT_APPLICABLE
    # The same field on a manufacturer is genuinely missing.
    maker = {"ticker": "TATASTEEL", "sector": "Basic Materials", "trailing_eps": 12.0}
    assert explain_gap("current_ratio", maker) is GapReason.RECOVERABLE


def test_not_applicable_takes_precedence_over_undefined() -> None:
    # A loss-making bank: the ratio does not apply to the business at all, which is a
    # stronger statement than "its financials leave it undefined".
    loss_making_bank = {"ticker": "YESBANK", "sector": "Financial Services",
                        "trailing_eps": -1.0, "profit_margin_pct": -5.0}
    assert explain_gap("current_ratio", loss_making_bank) is GapReason.NOT_APPLICABLE


def test_summary_separates_obtainable_from_raw_coverage() -> None:
    stocks = [
        # Complete profitable company.
        {"ticker": "A", "sector": "Technology", "trailing_eps": 10.0,
         "profit_margin_pct": 15.0, "trailing_pe": 20.0, "recommendation_mean": 2.0},
        # Uncovered loss-maker: both blanks are unobtainable, so coverage should not
        # be dragged down by them.
        {"ticker": "B", "sector": "Real Estate", "trailing_eps": -2.0,
         "profit_margin_pct": -8.0, "trailing_pe": None, "recommendation_mean": None},
    ]
    summary = coverage_summary(stocks)
    assert summary["stocks"] == 2
    assert summary["gaps"]["undefined"] == 1       # B.trailing_pe
    assert summary["gaps"]["analyst"] == 1         # B.recommendation_mean
    assert summary["gaps"]["recoverable"] == 0
    # Nothing is actually missing, so obtainable coverage is complete even though raw is not.
    assert summary["obtainable_coverage_pct"] == 100.0
    assert summary["raw_coverage_pct"] < 100.0


def test_summary_counts_a_real_gap_as_recoverable() -> None:
    stocks = [{"ticker": "A", "sector": "Technology", "trailing_eps": 10.0,
               "profit_margin_pct": 15.0, "current_ratio": None}]
    summary = coverage_summary(stocks)
    assert summary["gaps"]["recoverable"] == 1
    assert summary["obtainable_coverage_pct"] < 100.0


def test_unknown_when_the_deciding_figures_are_absent() -> None:
    """A blank P/E on a stock whose EPS we do not have is not evidence of anything.

    The dashboard snapshot carries no trailingEps, so an earlier version of this module
    silently reported every such gap as recoverable and inflated the actionable backlog.
    """
    no_financials = {"ticker": "OMAXE", "sector": "Real Estate", "trailingPE": None}
    assert explain_gap("trailingPE", no_financials) is GapReason.UNKNOWN
    assert explain_gap("pegRatio", no_financials) is GapReason.UNKNOWN

    # Supply the figure and it becomes answerable.
    with_eps = {**no_financials, "trailingEps": -4.2}
    assert explain_gap("trailingPE", with_eps) is GapReason.UNDEFINED


def test_unknown_gaps_are_excluded_from_the_denominator() -> None:
    stocks = [{"ticker": "A", "sector": "Real Estate", "trailing_pe": None}]
    summary = coverage_summary(stocks)
    assert summary["unclassified_gaps"] == 1
    assert summary["gaps"]["recoverable"] == 0
    # Neither counted as a gap we can fix nor quietly treated as complete: the unknown
    # cell is removed from the denominator, leaving only `sector`.
    assert summary["cells"] == 2
    assert summary["obtainable_cells"] == 1


def test_pipeline_outputs_are_not_counted_as_provider_gaps() -> None:
    # rank/score/rank-tracker columns are our own bookkeeping. A blank `rank` means the
    # model declined to score the stock, not that a provider failed us.
    stocks = [
        {
            "ticker": "A",
            "sector": "Technology",
            "trailing_eps": 10.0,
            "rank": None,
            "final_score": None,
            "g_quality": None,
            "staged_rank": None,
            "rank_vs_pushed": None,
        }
    ]
    summary = coverage_summary(stocks)
    assert summary["gaps"]["recoverable"] == 0
    assert summary["gaps"]["derived"] == 0  # excluded from the field list entirely
    assert summary["cells"] == 2  # sector + trailing_eps only
    assert explain_gap("rank", stocks[0]) is GapReason.DERIVED


def test_current_prefix_does_not_swallow_real_metrics() -> None:
    """Regression: a "current_" prefix rule misclassified two genuine fundamentals.

    current_ratio and current_price are provider metrics; current_rank and current_score
    are rank-tracker bookkeeping. Prefix matching cannot tell them apart.
    """
    maker = {"ticker": "TATASTEEL", "sector": "Basic Materials", "trailing_eps": 12.0}
    assert explain_gap("current_ratio", maker) is GapReason.RECOVERABLE
    assert explain_gap("current_price", maker) is GapReason.RECOVERABLE
    assert explain_gap("current_rank", maker) is GapReason.DERIVED
    assert explain_gap("current_score", maker) is GapReason.DERIVED


def test_summary_handles_an_empty_universe() -> None:
    assert coverage_summary([])["stocks"] == 0


def test_real_snapshot_is_mostly_unobtainable_gaps() -> None:
    """The dashboard's own data: most blanks cannot be filled by any provider."""
    payload = json.loads((SNAPSHOT_DIR / "dashboard-data.json").read_text())
    summary = coverage_summary(payload["stocks"])

    assert summary["stocks"] == payload["record_count"]
    # Obtainable coverage must be at least as good as raw coverage by construction.
    assert summary["obtainable_coverage_pct"] >= summary["raw_coverage_pct"]
    # And the analyst bucket must be non-trivial: hundreds of these names are uncovered.
    assert summary["gaps"]["analyst"] > 0
