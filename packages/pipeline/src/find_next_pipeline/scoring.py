from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "rank_all_v1"

# Ported from legacy/2026-05-31-undervalued-fundamentally-strong-stocks/scripts/rank_all.py —
# same weights, same groups, same thresholds. See that file's docstring and
# sources/factor-model.md for the rationale behind each weight.
WEIGHTS: dict[str, float] = {
    "quality": 2.5,
    "smart_money": 2.0,
    "valuation": 2.0,
    "growth": 1.75,
    "price_setup": 1.5,
    "analyst": 1.25,
    "momentum": 0.75,
}

# group -> [(metric_column, direction)]   +1 higher-better, -1 lower-better
GROUPS: dict[str, list[tuple[str, int]]] = {
    "quality": [
        ("roe_pct", 1),
        ("returnOnAssets", 1),
        ("ebitda_margin", 1),
        ("debtToEquity", -1),
        ("currentRatio", 1),
    ],
    "smart_money": [
        ("institutional_pct", 1),
        ("institutions_count", 1),
        ("promoter_pct", 1),
        ("delivery_trend", 1),
        ("avg_delivery_pct", 1),
    ],
    "valuation": [
        ("trailingPE", -1),
        ("priceToBook", -1),
        ("pegRatio", -1),
        ("enterpriseToEbitda", -1),
        ("dividendYield", 1),
    ],
    "growth": [
        ("earningsGrowth", 1),
        ("revenueGrowth", 1),
        ("earningsQuarterlyGrowth", 1),
        ("fwd_eps_growth", 1),
    ],
    "price_setup": [("pct_below_52w_high", 1), ("px_vs_50dma", 1)],
    "analyst": [
        ("upside_pct", 1),
        ("recommendationMean", -1),
        ("numberOfAnalystOpinions", 1),
    ],
    "momentum": [("fiftyTwoWeekChangePercent", 1), ("px_vs_200dma", 1)],
}

PCT_BELOW_CAP = 55.0  # cap drawdown so falling knives don't dominate price_setup
MIN_WEIGHTED_COVERAGE = 60.0
MIN_CORE_GROUP_COVERAGE = 40.0
FINANCIAL_NOT_APPLICABLE = {"ebitda_margin", "debtToEquity", "currentRatio", "enterpriseToEbitda"}

SCORE_COLUMNS = (
    ["rank", "ticker", "data_cov", "quality_cov", "valuation_cov"]
    + [f"g_{group}" for group in WEIGHTS]
    + ["model_score", "final_score", "score_status"]
)


def _rankpct(series: pd.Series, direction: int) -> pd.Series:
    """Percentile-rank to 0-1 (robust to outliers like ROE 276% / PE 2509).

    Higher-better -> high values get high pct. Missing -> 0.5 (neutral), so an
    absent metric doesn't drag or inflate a stock's group score either way.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=(direction > 0)).fillna(0.5)


def _metric_applicability(df: pd.DataFrame, column: str) -> pd.Series:
    """Metrics such as EV/EBITDA are not comparable for banks and lenders."""
    applicable = pd.Series(True, index=df.index)
    if column in FINANCIAL_NOT_APPLICABLE and "sector" in df:
        financial = df["sector"].astype(str).str.casefold().eq("financial services")
        applicable &= ~financial
    return applicable


def _derive_extra_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the columns GROUPS reads but no provider fetches directly.

    Ported from rank_all.py's ``derive()``. Runs on whatever currentPrice /
    fiftyTwoWeekHigh / etc. are in the frame at call time — when those have
    already been overlaid with live-fetched values, these derive fresh from
    today's price rather than the archived snapshot's.
    """

    def g(column: str) -> pd.Series:
        return pd.to_numeric(df[column], errors="coerce") if column in df else np.nan

    df["roe_pct"] = g("returnOnEquity") * 100
    df["ebitda_margin"] = g("ebitdaMargins").fillna(g("profitMargins")) * 100
    df["pct_below_52w_high"] = (
        (g("fiftyTwoWeekHigh") - g("currentPrice")) / g("fiftyTwoWeekHigh") * 100
    ).clip(upper=PCT_BELOW_CAP)
    df["px_vs_50dma"] = (g("currentPrice") / g("fiftyDayAverage") - 1) * 100
    df["px_vs_200dma"] = (g("currentPrice") / g("twoHundredDayAverage") - 1) * 100
    df["upside_pct"] = (g("targetMeanPrice") / g("currentPrice") - 1) * 100
    trailing_eps = g("trailingEps")
    df["fwd_eps_growth"] = np.where(
        trailing_eps > 0, (g("forwardEps") / trailing_eps - 1) * 100, np.nan
    )

    # Sanity guards against data artifacts (same as rank_all.py).
    for column in ["trailingPE", "priceToBook", "pegRatio", "enterpriseToEbitda"]:
        if column in df:
            values = pd.to_numeric(df[column], errors="coerce")
            df[column] = values.where(values > 0)
    profit_margin = pd.to_numeric(df.get("profitMargins"), errors="coerce")
    df["roe_pct"] = df["roe_pct"].clip(-100, 150)
    df.loc[(profit_margin < 0) & (df["roe_pct"] > 50), "roe_pct"] = np.nan
    df["returnOnAssets"] = pd.to_numeric(df.get("returnOnAssets"), errors="coerce").clip(-1, 1)
    df["ebitda_margin"] = df["ebitda_margin"].clip(-100, 100)

    if "institutional_pct" not in df or df["institutional_pct"].isna().all():
        df["institutional_pct"] = g("heldPercentInstitutions") * 100
    if "promoter_pct" not in df or df["promoter_pct"].isna().all():
        df["promoter_pct"] = g("heldPercentInsiders") * 100
    for column in ["promoter_pct", "institutional_pct"]:
        values = pd.to_numeric(df.get(column), errors="coerce")
        df[column] = values.where(values.between(0, 100, inclusive="both"))
    ownership_total = df["promoter_pct"] + df["institutional_pct"]
    df.loc[ownership_total > 100.5, "institutional_pct"] = np.nan
    return df


def _coverage_by_group(
    df: pd.DataFrame, weights: dict[str, float]
) -> tuple[pd.Series, dict[str, pd.Series]]:
    total_weight = sum(weights.values())
    group_coverage: dict[str, pd.Series] = {}
    weighted = pd.Series(0.0, index=df.index)
    for group, metrics in GROUPS.items():
        available = []
        applicable_metrics = []
        for column, _direction in metrics:
            applicable = _metric_applicability(df, column).astype(float)
            applicable_metrics.append(applicable)
            if column in df:
                present = pd.to_numeric(df[column], errors="coerce").notna().astype(float)
                available.append(present * applicable)
            else:
                available.append(pd.Series(0.0, index=df.index))
        denominator = sum(applicable_metrics).replace(0, np.nan)
        coverage = (sum(available) / denominator).fillna(0)
        group_coverage[group] = coverage * 100
        weighted += (weights[group] / total_weight) * coverage
    return (weighted * 100).round(1), {k: v.round(1) for k, v in group_coverage.items()}


def score_universe(
    stocks: list[dict[str, Any]], weights: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Rank a universe of stocks by the same multi-factor model as rank_all.py.

    ``stocks`` is the merged (CSV-archived + live-overlaid) view the dashboard already
    builds — a list of plain dicts keyed by ticker, sector, currentPrice, and the raw
    yfinance-shaped fundamental fields. Returns one row per input ticker with rank,
    final_score, model_score, per-group coverage/scores, and score_status; a stock
    below the coverage thresholds gets ``final_score=None`` and stays unranked rather
    than silently dropped, so it's still visible on the dashboard.
    """
    w = dict(WEIGHTS)
    if weights:
        w.update(weights)
    total = sum(w.values())
    w = {k: v / total for k, v in w.items()}

    df = pd.DataFrame(stocks)
    if df.empty or "ticker" not in df:
        return []
    df = _derive_extra_factors(df)

    data_cov, group_cov = _coverage_by_group(df, w)
    df["data_cov"] = data_cov
    df["quality_cov"] = group_cov["quality"]
    df["valuation_cov"] = group_cov["valuation"]

    for group, metrics in GROUPS.items():
        normed = []
        for column, direction in metrics:
            if column not in df:
                continue
            applicable = _metric_applicability(df, column)
            score = _rankpct(df[column].where(applicable), direction)
            normed.append(score.where(applicable))
        group_score = (
            pd.concat(normed, axis=1).mean(axis=1).fillna(0.5)
            if normed
            else pd.Series(0.5, index=df.index)
        )
        df[f"g_{group}"] = (group_score * 100).round(1)

    df["model_score"] = (sum(w[g] * (df[f"g_{g}"] / 100) for g in WEIGHTS) * 100).round(1)
    adjusted = 50 + (df["model_score"] - 50) * (df["data_cov"] / 100)
    eligible = (
        (df["data_cov"] >= MIN_WEIGHTED_COVERAGE)
        & (df["quality_cov"] >= MIN_CORE_GROUP_COVERAGE)
        & (df["valuation_cov"] >= MIN_CORE_GROUP_COVERAGE)
    )
    df["score_status"] = np.select(
        [
            df["data_cov"] < MIN_WEIGHTED_COVERAGE,
            df["quality_cov"] < MIN_CORE_GROUP_COVERAGE,
            df["valuation_cov"] < MIN_CORE_GROUP_COVERAGE,
        ],
        [
            "insufficient overall coverage",
            "insufficient quality data",
            "insufficient valuation data",
        ],
        default="ranked",
    )
    df["final_score"] = adjusted.round(1).where(eligible)
    df = df.sort_values(
        ["final_score", "model_score"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)
    ranks = pd.Series(pd.NA, index=df.index, dtype="Int64")
    ranked_count = int(df["final_score"].notna().sum())
    ranks.iloc[:ranked_count] = range(1, ranked_count + 1)
    # Overwrite, not insert: the input stocks already carry a stale "rank" from the
    # archived CSV ranking, and df.insert() refuses to add a column that already exists.
    df["rank"] = ranks

    columns = [c for c in SCORE_COLUMNS if c in df]
    result = df[columns].astype(object).where(df[columns].notna(), None)
    return result.to_dict(orient="records")
