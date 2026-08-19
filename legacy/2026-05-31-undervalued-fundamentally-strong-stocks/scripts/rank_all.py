"""
rank_all.py — comprehensive, TUNABLE multi-factor ranking of beaten-down but
fundamentally strong Indian stocks (>=1000cr universe).

Merges fundamentals (data/raw_fundamentals.csv) + ownership/delivery
(data/shareholding_layer.csv), derives extra factors, normalizes every metric
(percentile 0-1 across the universe, neutral-filled, direction-aware), then
combines 7 weighted FACTOR GROUPS into a 0-100 score. The score is adjusted for
weighted data coverage; stocks below the minimum coverage or without enough
quality/valuation evidence remain visible but are explicitly unranked.

FACTOR GROUPS & DEFAULT WEIGHTS (see sources/factor-model.md for rationale)
  quality      2.5   ROE, ROA, EBITDA margin, low debt, current ratio
  smart_money  2.0   institutional%, #institutions, promoter%, delivery trend*, delivery%
  valuation    2.0   PE, P/B, PEG, EV/EBITDA, dividend yield   (cheaper = better)
  growth       1.75  earnings gr, revenue gr, qtrly earnings gr, forward-EPS growth
  price_setup  1.5   % below 52w high (the "down") + price vs 50DMA (bottoming/turning)
  analyst      1.25  target upside %, recommendation (buy=low), # analysts covering
  momentum     0.75  1-yr price change, price vs 200DMA   (avoid pure falling knives)

  * delivery_trend is our FREE PROXY for FII/DII *entering/exiting* (accumulation vs
    distribution). TRUE quarterly FII/DII % change needs Screener.in / a paid feed — see
    sources/factor-model.md "Known gap".

TUNE: edit WEIGHTS or override on CLI:
  ./.venv/bin/python rank_all.py --weights "valuation=3,smart_money=3,momentum=0"
  ./.venv/bin/python rank_all.py --top 30 --min-mcap 5000     (mcap in Rs cr)
Legal: personal/internal research use only.
"""
import argparse, sys
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# --------------------------- TUNABLE GROUP WEIGHTS ---------------------------
WEIGHTS = {
    "quality":     2.5,
    "smart_money": 2.0,
    "valuation":   2.0,
    "growth":      1.75,
    "price_setup": 1.5,
    "analyst":     1.25,
    "momentum":    0.75,
}

# group -> [(metric_column, direction)]   +1 higher-better, -1 lower-better
GROUPS = {
    "quality":     [("roe_pct", +1), ("returnOnAssets", +1), ("ebitda_margin", +1),
                    ("debtToEquity", -1), ("currentRatio", +1)],
    "smart_money": [("institutional_pct", +1), ("institutions_count", +1), ("promoter_pct", +1),
                    ("delivery_trend", +1), ("avg_delivery_pct", +1)],
    "valuation":   [("trailingPE", -1), ("priceToBook", -1), ("pegRatio", -1),
                    ("enterpriseToEbitda", -1), ("dividendYield", +1)],
    "growth":      [("earningsGrowth", +1), ("revenueGrowth", +1),
                    ("earningsQuarterlyGrowth", +1), ("fwd_eps_growth", +1)],
    "price_setup": [("pct_below_52w_high", +1), ("px_vs_50dma", +1)],
    "analyst":     [("upside_pct", +1), ("recommendationMean", -1), ("numberOfAnalystOpinions", +1)],
    "momentum":    [("fiftyTwoWeekChangePercent", +1), ("px_vs_200dma", +1)],
}
PCT_BELOW_CAP = 55.0   # cap drawdown so falling knives don't dominate price_setup
MIN_WEIGHTED_COVERAGE = 60.0
MIN_CORE_GROUP_COVERAGE = 40.0
FINANCIAL_NOT_APPLICABLE = {"ebitda_margin", "debtToEquity", "currentRatio", "enterpriseToEbitda"}

def rankpct(s, direction):
    """Percentile-rank to 0-1 (robust to outliers like ROE 276% / PE 2509).
    Higher-better -> high values get high pct. Missing -> 0.5 (neutral)."""
    s = pd.to_numeric(s, errors="coerce")
    return s.rank(pct=True, ascending=(direction > 0)).fillna(0.5)


def metric_applicability(df, column):
    """Metrics such as EV/EBITDA are not comparable for banks and lenders."""
    applicable = pd.Series(True, index=df.index)
    if column in FINANCIAL_NOT_APPLICABLE and "sector" in df:
        financial = df["sector"].astype(str).str.casefold().eq("financial services")
        applicable &= ~financial
    return applicable


def coverage_by_group(df, weights=None):
    """Return weighted and per-group coverage for the metrics actually scored.

    This runs after ``derive`` so, for example, forward-EPS coverage only counts
    when a usable trailing EPS exists and the derived growth value is real.
    """
    weights = weights or WEIGHTS
    total_weight = sum(weights.values())
    group_cov = {}
    weighted = pd.Series(0.0, index=df.index)
    for group, metrics in GROUPS.items():
        available = []
        applicable_metrics = []
        for column, _direction in metrics:
            applicable = metric_applicability(df, column).astype(float)
            applicable_metrics.append(applicable)
            if column in df:
                present = pd.to_numeric(df[column], errors="coerce").notna().astype(float)
                available.append(present * applicable)
            else:
                available.append(pd.Series(0.0, index=df.index))
        denominator = sum(applicable_metrics).replace(0, np.nan)
        coverage = (sum(available) / denominator).fillna(0)
        group_cov[group] = coverage * 100
        weighted += (weights[group] / total_weight) * coverage
    return (weighted * 100).round(1), {k: v.round(1) for k, v in group_cov.items()}

def parse_overrides(arg):
    out = {}
    if not arg: return out
    for kv in arg.split(","):
        k, _, v = kv.partition("="); k = k.strip()
        if k not in WEIGHTS: sys.exit(f"Unknown weight '{k}'. Valid: {', '.join(WEIGHTS)}")
        out[k] = float(v)
    return out

def load():
    raw = pd.read_csv(DATA / "raw_fundamentals.csv")
    shp = pd.read_csv(DATA / "shareholding_layer.csv")
    keep = ["ticker","promoter_pct","institutional_pct","institutions_count",
            "avg_delivery_pct","delivery_trend"]
    keep = [c for c in keep if c in shp.columns]
    df = raw.merge(shp[keep], on="ticker", how="inner")
    return df

def derive(df):
    g = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    df["roe_pct"] = g("returnOnEquity") * 100
    df["ebitda_margin"] = g("ebitdaMargins").fillna(g("profitMargins")) * 100
    df["pct_below_52w_high"] = ((g("fiftyTwoWeekHigh") - g("currentPrice"))
                                / g("fiftyTwoWeekHigh") * 100).clip(upper=PCT_BELOW_CAP)
    df["px_vs_50dma"] = (g("currentPrice") / g("fiftyDayAverage") - 1) * 100
    df["px_vs_200dma"] = (g("currentPrice") / g("twoHundredDayAverage") - 1) * 100
    df["upside_pct"] = (g("targetMeanPrice") / g("currentPrice") - 1) * 100
    teps = g("trailingEps")
    df["fwd_eps_growth"] = np.where(teps > 0, (g("forwardEps") / teps - 1) * 100, np.nan)
    # ---- sanity guards against data artifacts ----
    # non-positive valuation multiples are meaningless for ranking -> neutral
    for c in ["trailingPE", "priceToBook", "pegRatio", "enterpriseToEbitda"]:
        if c in df:
            s = pd.to_numeric(df[c], errors="coerce"); df[c] = s.where(s > 0)
    # clip return ratios; drop fake-high ROE on loss-makers (negative-equity artifacts)
    pm = pd.to_numeric(df.get("profitMargins"), errors="coerce")
    df["roe_pct"] = df["roe_pct"].clip(-100, 150)
    df.loc[(pm < 0) & (df["roe_pct"] > 50), "roe_pct"] = np.nan
    df["returnOnAssets"] = pd.to_numeric(df.get("returnOnAssets"), errors="coerce").clip(-1, 1)
    df["ebitda_margin"] = df["ebitda_margin"].clip(-100, 100)
    # prefer fresh shareholding-layer ownership; fall back to yfinance held% snapshot
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights"); ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-mcap", type=float, default=0)
    args = ap.parse_args()

    w = dict(WEIGHTS); w.update(parse_overrides(args.weights))
    tot = sum(w.values()); w = {k: v / tot for k, v in w.items()}

    df = derive(load())
    if "marketCap" in df:
        df["mcap_cr"] = pd.to_numeric(df["marketCap"], errors="coerce") / 1e7
        df = df[df["mcap_cr"] >= args.min_mcap]
    df = df.dropna(subset=["currentPrice"]).reset_index(drop=True)
    if df.empty: sys.exit("No rows. Run stages 2-4 first.")

    data_cov, group_cov = coverage_by_group(df, w)
    df["data_cov"] = data_cov
    df["quality_cov"] = group_cov["quality"]
    df["valuation_cov"] = group_cov["valuation"]

    for grp, metrics in GROUPS.items():
        normed = []
        for column, direction in metrics:
            if column not in df:
                continue
            applicable = metric_applicability(df, column)
            score = rankpct(pd.to_numeric(df[column], errors="coerce").where(applicable), direction)
            normed.append(score.where(applicable))
        gscore = (pd.concat(normed, axis=1).mean(axis=1).fillna(0.5)
                  if normed else pd.Series(0.5, index=df.index))
        df[f"g_{grp}"] = (gscore * 100).round(1)
    df["model_score"] = (sum(w[g] * (df[f"g_{g}"] / 100) for g in WEIGHTS) * 100).round(1)
    adjusted = 50 + (df["model_score"] - 50) * (df["data_cov"] / 100)
    eligible = ((df["data_cov"] >= MIN_WEIGHTED_COVERAGE) &
                (df["quality_cov"] >= MIN_CORE_GROUP_COVERAGE) &
                (df["valuation_cov"] >= MIN_CORE_GROUP_COVERAGE))
    df["score_status"] = np.select(
        [df["data_cov"] < MIN_WEIGHTED_COVERAGE,
         df["quality_cov"] < MIN_CORE_GROUP_COVERAGE,
         df["valuation_cov"] < MIN_CORE_GROUP_COVERAGE],
        ["insufficient overall coverage", "insufficient quality data", "insufficient valuation data"],
        default="ranked")
    df["final_score"] = adjusted.round(1).where(eligible)
    df = df.sort_values(["final_score", "model_score"], ascending=[False, False], na_position="last").reset_index(drop=True)
    ranks = pd.Series(pd.NA, index=df.index, dtype="Int64")
    ranked_count = int(df["final_score"].notna().sum())
    ranks.iloc[:ranked_count] = range(1, ranked_count + 1)
    df.insert(0, "rank", ranks)

    cols = (["rank","ticker","shortName","sector","mcap_cr","currentPrice","pct_below_52w_high",
             "trailingPE","roe_pct","promoter_pct","institutional_pct","delivery_trend",
             "upside_pct","recommendationMean","data_cov","quality_cov","valuation_cov"]
            + [f"g_{g}" for g in WEIGHTS] + ["model_score","final_score","score_status"])
    cols = [c for c in cols if c in df]
    df[cols].to_csv(DATA / "final_ranking.csv", index=False)

    print("Group weights (normalized):", {k: round(v, 3) for k, v in w.items()})
    print(f"Universe: {len(df)} stocks; ranked: {ranked_count}; "
          f"unranked for insufficient data: {len(df) - ranked_count} (min mcap {args.min_mcap} cr)")
    print(f"Saved -> {DATA/'final_ranking.csv'}\n")
    pd.set_option("display.width", 280, "display.max_columns", 40)
    show = ["rank","ticker","sector","mcap_cr","pct_below_52w_high","trailingPE","roe_pct",
            "institutional_pct","delivery_trend","upside_pct",
            "g_quality","g_smart_money","g_valuation","g_growth","final_score"]
    show = [c for c in show if c in df]
    print(df[show].head(args.top).to_string(index=False))

if __name__ == "__main__":
    main()
