"""Why a metric is missing, not just that it is.

`select_canonical_metrics` drops a `(ticker, field)` pair when no candidate observation is
valid, which is correct but records no reason. Downstream that becomes a bare dash in the
dashboard and a coverage percentage that reads as a fetching backlog.

It usually is not one. Of the ~6,000 blank cells measured across the 1,353-stock universe,
only about a third were values any provider could supply:

* ``analyst`` — the value exists only if a broker publishes research on the stock. 463 of
  the names have no analyst coverage at all, so no provider, free or paid, has a price
  target for them.
* ``undefined`` — the company's own financials leave the ratio undefined. A loss-making
  company has no P/E; one whose earnings shrank has no PEG. A better provider changes
  nothing.
* ``not_applicable`` — the concept does not apply to the business. Banks do not report
  current assets and liabilities the way an industrial does.
* ``recoverable`` — genuinely missing. **This is the only bucket worth acting on.**

Reporting one blended "89% coverage" hides all four. Coverage over *obtainable* cells is
the number that means something, and is what :func:`coverage_summary` returns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

__all__ = [
    "GapReason",
    "classify_field",
    "coverage_summary",
    "explain_gap",
    "is_blank",
]


class GapReason(StrEnum):
    RECOVERABLE = "recoverable"
    ANALYST = "analyst"
    UNDEFINED = "undefined"
    NOT_APPLICABLE = "not_applicable"
    DESCRIPTIVE = "descriptive"
    DERIVED = "derived"
    UNKNOWN = "unknown"


# The pipeline emits snake_case, while the legacy dashboard snapshot still carries the
# provider's camelCase. Both vocabularies are in use, so both are recognised.
_ALIASES = {
    "trailingpe": "trailing_pe",
    "forwardpe": "forward_pe",
    "forwardeps": "forward_eps",
    "trailingeps": "trailing_eps",
    "pegratio": "peg_ratio",
    "pricetobook": "price_to_book",
    "marketcap": "market_cap",
    "currentprice": "current_price",
    "recommendationmean": "recommendation_mean",
    "numberofanalystopinions": "number_of_analyst_opinions",
    "targetmeanprice": "target_mean_price",
    "targethighprice": "target_high_price",
    "targetlowprice": "target_low_price",
    "returnonequity": "roe_pct",
    "returnonassets": "return_on_assets_pct",
    "profitmargins": "profit_margin_pct",
    "currentratio": "current_ratio",
    "quickratio": "quick_ratio",
    "debttoequity": "debt_to_equity",
    "freecashflow": "free_cash_flow",
    "enterprisetoebitda": "enterprise_to_ebitda",
    "enterprisevalue": "enterprise_value",
    "earningsgrowth": "earnings_growth",
    "revenuegrowth": "revenue_growth",
    "earningsquarterlygrowth": "earnings_quarterly_growth",
    "ebitdamargins": "ebitda_margin_pct",
    "shortname": "name",
    "upside_pct": "upside_pct",
}

ANALYST_FIELDS = frozenset(
    {
        "recommendation_mean",
        "number_of_analyst_opinions",
        "target_mean_price",
        "target_high_price",
        "target_low_price",
        "forward_pe",
        "forward_eps",
        # Derived from a price target, so it inherits the target's availability.
        "upside_pct",
    }
)

DESCRIPTIVE_FIELDS = frozenset({"name", "sector", "industry", "ticker"})

# Balance-sheet concepts that do not exist for banks and lenders.
NOT_APPLICABLE_TO_FINANCIALS = frozenset(
    {
        "current_ratio",
        "quick_ratio",
        "debt_to_equity",
        "enterprise_to_ebitda",
        "enterprise_value",
        "ebitda_margin_pct",
        "free_cash_flow",
    }
)

FINANCIAL_SECTOR = "financial services"

# Scores, ranks and rank-tracker columns are produced by this pipeline, not fetched from
# anyone. A blank `rank` means the model declined to score the stock; a blank
# `rank_vs_pushed` means there was nothing to compare against. Counting them as missing
# provider data would swamp the real backlog with our own bookkeeping.
#
# Prefixes are matched only where they cannot collide with a real metric. "current_" is
# deliberately NOT one: it would swallow current_ratio and current_price, quietly
# reclassifying two genuine fundamentals as our own bookkeeping. Those rank-tracker
# columns are listed by name instead.
DERIVED_PREFIXES = ("g_", "staged_", "pushed_", "pat_", "movement_")
DERIVED_FIELDS = frozenset(
    {
        "rank",
        "rank_chg",
        "final_score",
        "model_score",
        "score_status",
        "data_cov",
        "quality_cov",
        "valuation_cov",
        "data_quality",
        "price_chg_pct",
        "rank_vs_staged",
        "rank_vs_pushed",
        "score_vs_staged",
        "score_vs_pushed",
        "current_rank",
        "current_score",
        "current_score_status",
    }
)

# Deciding whether a ratio is undefined needs the company's own figures. When those are
# themselves absent we cannot tell, and must say so rather than defaulting to a verdict.
_UNDEFINED_INPUTS = {
    "trailing_pe": ("trailing_eps",),
    "forward_pe": ("trailing_eps",),
    "peg_ratio": ("trailing_eps", "earnings_growth"),
    "earnings_growth": ("profit_margin_pct",),
    "earnings_quarterly_growth": ("profit_margin_pct",),
}


def _is_derived(field: str) -> bool:
    name = field.strip()
    return name in DERIVED_FIELDS or name.startswith(DERIVED_PREFIXES)


def canonical_field(field: str) -> str:
    """Map either vocabulary onto one canonical name."""
    key = field.strip()
    return _ALIASES.get(key.replace("_", "").lower(), key)


def classify_field(field: str) -> GapReason:
    """Classify a field on its own, ignoring any particular company."""
    name = canonical_field(field)
    if name in ANALYST_FIELDS:
        return GapReason.ANALYST
    if name in DESCRIPTIVE_FIELDS:
        return GapReason.DESCRIPTIVE
    return GapReason.RECOVERABLE


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    # NaN is the only value that is not equal to itself.
    return isinstance(value, float) and value != value


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


def _is_financial(stock: Mapping[str, Any]) -> bool:
    sector = stock.get("sector")
    return isinstance(sector, str) and sector.strip().casefold() == FINANCIAL_SECTOR


def _input(stock: Mapping[str, Any], canonical: str) -> float | None:
    """Read one of the deciding figures under either vocabulary."""
    camel = {
        "trailing_eps": "trailingEps",
        "earnings_growth": "earningsGrowth",
        "profit_margin_pct": "profitMargins",
    }[canonical]
    for key in (canonical, camel):
        if key in stock:
            value = _number(stock[key])
            if value is not None:
                return value
    return None


def _is_undefined(name: str, stock: Mapping[str, Any]) -> bool | None:
    """Does the company's own arithmetic leave this ratio undefined?

    Returns None when the figures needed to decide are themselves missing. Callers must
    not read that as "no" — treating an unanswerable question as a clean bill of health
    is how a blank P/E on a loss-maker gets reported as a fetching backlog.
    """
    required = _UNDEFINED_INPUTS.get(name)
    if required is None:
        return False

    values = {key: _input(stock, key) for key in required}
    if all(value is None for value in values.values()):
        return None

    if name in {"trailing_pe", "forward_pe"}:
        eps = values["trailing_eps"]
        return eps is not None and eps <= 0
    if name == "peg_ratio":
        # A PEG needs positive earnings *and* positive growth to mean anything.
        eps, growth = values["trailing_eps"], values["earnings_growth"]
        if (eps is not None and eps <= 0) or (growth is not None and growth <= 0):
            return True
        # Undefined only if both inputs were readable and both were fine.
        return None if eps is None or growth is None else False
    # Growth off a loss-making base is not economically comparable, so the pipeline
    # withholds it rather than manufacturing a turnaround number.
    margin = values["profit_margin_pct"]
    return margin is not None and margin <= 0


def explain_gap(field: str, stock: Mapping[str, Any]) -> GapReason:
    """Why is `field` blank for this particular company?

    Order matters: a concept that does not apply to the business is not "undefined", and
    a field nobody publishes for this stock is not "recoverable".
    """
    if _is_derived(field):
        return GapReason.DERIVED
    name = canonical_field(field)
    if name in DESCRIPTIVE_FIELDS:
        return GapReason.DESCRIPTIVE
    if name in NOT_APPLICABLE_TO_FINANCIALS and _is_financial(stock):
        return GapReason.NOT_APPLICABLE
    if name in ANALYST_FIELDS:
        return GapReason.ANALYST
    undefined = _is_undefined(name, stock)
    if undefined is None:
        return GapReason.UNKNOWN
    return GapReason.UNDEFINED if undefined else GapReason.RECOVERABLE


def coverage_summary(
    stocks: Iterable[Mapping[str, Any]],
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Split every blank cell by cause and report coverage over obtainable cells.

    ``obtainable_coverage_pct`` is the honest headline: it excludes cells no provider
    could ever fill, so a change in it reflects real fetching progress rather than the
    sector mix of the universe.
    """
    rows = [dict(stock) for stock in stocks]
    if not rows:
        return {
            "stocks": 0,
            "cells": 0,
            "filled": 0,
            "gaps": {reason.value: 0 for reason in GapReason},
            "raw_coverage_pct": 0.0,
            "obtainable_cells": 0,
            "obtainable_coverage_pct": 0.0,
        }

    if fields is None:
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    names.append(key)
        fields = names
    # Pipeline outputs are excluded: they are our own bookkeeping, not provider data,
    # and counting them would swamp the real backlog.
    field_list = [f for f in fields if f != "ticker" and not _is_derived(f)]

    gaps = {reason.value: 0 for reason in GapReason}
    blanks = 0
    cells = len(rows) * len(field_list)

    for row in rows:
        for field in field_list:
            if not is_blank(row.get(field)):
                continue
            blanks += 1
            gaps[explain_gap(field, row).value] += 1

    unobtainable = (
        gaps[GapReason.ANALYST.value]
        + gaps[GapReason.NOT_APPLICABLE.value]
        + gaps[GapReason.UNDEFINED.value]
        + gaps[GapReason.DESCRIPTIVE.value]
    )
    # `unknown` gaps are excluded from the denominator rather than assumed either way:
    # claiming them as obtainable would overstate the backlog, and claiming them as
    # unobtainable would flatter the coverage figure. They are reported separately so
    # the count is visible instead of quietly folded into a verdict.
    unobtainable += gaps[GapReason.UNKNOWN.value]
    obtainable = cells - unobtainable
    recoverable = gaps[GapReason.RECOVERABLE.value]

    return {
        "stocks": len(rows),
        "cells": cells,
        "filled": cells - blanks,
        "gaps": gaps,
        "raw_coverage_pct": round(100 * (1 - blanks / cells), 2) if cells else 0.0,
        "obtainable_cells": obtainable,
        "obtainable_coverage_pct": (
            round(100 * (1 - recoverable / obtainable), 3) if obtainable > 0 else 0.0
        ),
        # Non-zero means the universe lacks the figures needed to classify some blanks.
        # Feed trailing_eps / profit_margin_pct / earnings_growth through and it drops.
        "unclassified_gaps": gaps[GapReason.UNKNOWN.value],
    }
