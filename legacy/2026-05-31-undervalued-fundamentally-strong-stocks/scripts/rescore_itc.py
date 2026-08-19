"""
rescore_itc.py — re-score ITC with DEMERGER-ADJUSTED growth, holding the rest of
the model identical.

WHY: ITC demerged its Hotels business (effective Jan 2025). The year-ago base
quarter was inflated by a one-time demerger/exceptional gain, so yfinance shows
earningsGrowth = earningsQuarterlyGrowth = -72.7%. That craters ITC's growth
percentile to ~7 even though the *operating* business did not fall 73%. Revenue
growth (-5%) and forward-EPS growth (+7.7%) are the un-distorted signals.

This script reuses rank_all.py's exact load/derive/group logic, overrides ONLY
ITC's two distorted earnings-growth fields under several scenarios, recomputes the
whole universe ranking, and reports ITC's new growth-group score / final score /
rank under each. Everything else (every other stock, every other factor) is
untouched, so the comparison is apples-to-apples.

Run: ./.venv/bin/python scripts/rescore_itc.py
Legal: personal/internal research use only.
"""
import pandas as pd, numpy as np
import rank_all as R   # reuse the SAME model

TICKER = "ITC"

# Scenarios: what to put in earningsGrowth & earningsQuarterlyGrowth (decimals).
# revenueGrowth (-5%) and fwd_eps_growth (+7.7%) are kept as-is in every scenario.
SCENARIOS = {
    "baseline (as-shipped, -72.7%)":      {"earningsGrowth": -0.727, "earningsQuarterlyGrowth": -0.727},
    "neutralize (drop distorted metrics)":{"earningsGrowth": np.nan, "earningsQuarterlyGrowth": np.nan},
    "adjusted: flat (0%)":                {"earningsGrowth":  0.00,  "earningsQuarterlyGrowth":  0.00},
    "adjusted: underlying +5%":           {"earningsGrowth":  0.05,  "earningsQuarterlyGrowth":  0.05},
    "adjusted: underlying +8%":           {"earningsGrowth":  0.08,  "earningsQuarterlyGrowth":  0.08},
}

def rank_universe(df, w):
    """Replicates rank_all.main()'s scoring on an already-derived df."""
    df = df.dropna(subset=["currentPrice"]).reset_index(drop=True)
    for grp, metrics in R.GROUPS.items():
        normed = [R.rankpct(df[c], d) for c, d in metrics if c in df]
        gscore = sum(normed) / len(normed) if normed else pd.Series(0.5, index=df.index)
        df[f"g_{grp}"] = (gscore * 100).round(1)
    df["final_score"] = (sum(w[g] * (df[f"g_{g}"] / 100) for g in R.WEIGHTS) * 100).round(1)
    df = df.sort_values("final_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df

def main():
    w = {k: v / sum(R.WEIGHTS.values()) for k, v in R.WEIGHTS.items()}
    base_raw = R.load()
    N = len(base_raw)
    rows = []
    for name, ov in SCENARIOS.items():
        raw = base_raw.copy()
        m = raw["ticker"] == TICKER
        for col, val in ov.items():
            raw.loc[m, col] = val
        ranked = R.derive(raw)
        ranked = rank_universe(ranked, w)
        itc = ranked[ranked["ticker"] == TICKER].iloc[0]
        rows.append({
            "scenario": name,
            "g_growth": itc["g_growth"],
            "final_score": itc["final_score"],
            "rank": int(itc["rank"]),
            "pctile": round(100 - int(itc["rank"]) / N * 100),
        })
    out = pd.DataFrame(rows)
    base = out.iloc[0]
    out["d_score"] = (out["final_score"] - base["final_score"]).round(1)
    out["d_rank"] = (base["rank"] - out["rank"]).astype(int)   # +ve = moved UP
    out.to_csv(R.DATA / "itc_demerger_rescore.csv", index=False)
    pd.set_option("display.width", 200)
    print(f"\nUniverse: {N} stocks.  Re-scoring {TICKER} (growth-group inputs only).\n")
    print(out.to_string(index=False))
    print("\nd_score / d_rank are vs the baseline (as-shipped) row.  +d_rank = moved up.")

if __name__ == "__main__":
    main()
