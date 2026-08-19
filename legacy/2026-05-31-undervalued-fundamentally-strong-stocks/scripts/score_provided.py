"""
Score the 50 user-provided companies on "fundamentally amazing but beaten down".

Each column gets a WEIGHT and a DIRECTION. Robust to outliers via PERCENTILE-RANK
normalization (0-1) instead of min-max (so Indo Thai's +1114% profit var etc. don't
dominate). final_score = sum(weight * rank_pct) * 100, weights sum to 1.

Input : data/provided_50.csv   Output: data/provided_50_scored.csv
Run   : ./.venv/bin/python score_provided.py
"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# column -> (weight, direction)   +1 higher-better, -1 lower-better
COLW = {
    "roce":           (0.15, +1),   # quality: capital efficiency
    "ret_6m":         (0.13, -1),   # the "down": more negative 6m return = more beaten down
    "pe":             (0.12, -1),   # valuation: cheaper
    "roe":            (0.12, +1),   # quality: current return
    "qtr_profit_var": (0.12, +1),   # growth/momentum: latest qtr profit growth
    "roe_5yr":        (0.10, +1),   # quality consistency
    "qtr_sales_var":  (0.10, +1),   # growth: latest qtr sales growth
    "cmp_bv":         (0.08, -1),   # valuation: price/book
    "mcap_cr":        (0.05, +1),   # size/stability
    "div_yld":        (0.03, +1),   # shareholder return
}
GROUPS = {
    "Quality":   ["roce", "roe", "roe_5yr"],
    "Valuation": ["pe", "cmp_bv", "div_yld"],
    "Growth":    ["qtr_profit_var", "qtr_sales_var"],
    "BeatenDown":["ret_6m"],
    "Size":      ["mcap_cr"],
}

def main():
    df = pd.read_csv(DATA / "provided_50.csv")
    assert abs(sum(w for w, _ in COLW.values()) - 1.0) < 1e-9, "weights must sum to 1"

    # percentile-rank each column to 0-1 in its preferred direction
    rk = {}
    for col, (w, d) in COLW.items():
        s = pd.to_numeric(df[col], errors="coerce")
        rk[col] = s.rank(pct=True, ascending=(d > 0))  # higher-better -> high values get high pct
    R = pd.DataFrame(rk)

    df["final_score"] = sum(COLW[c][0] * R[c] for c in COLW) * 100
    for g, cols in GROUPS.items():
        # equal-weight within group, shown as 0-100 (informational)
        df[f"s_{g}"] = (sum(R[c] for c in cols) / len(cols) * 100).round(0)

    df = df.sort_values("final_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["final_score"] = df["final_score"].round(1)

    cols = ["rank","name","pe","mcap_cr","roce","roe","roe_5yr","qtr_profit_var",
            "qtr_sales_var","ret_6m","cmp_bv","div_yld",
            "s_Quality","s_Valuation","s_Growth","s_BeatenDown","final_score"]
    df[cols].to_csv(DATA / "provided_50_scored.csv", index=False)
    pd.set_option("display.width", 260, "display.max_columns", 40)
    show = ["rank","name","pe","roce","roe","roe_5yr","qtr_profit_var","qtr_sales_var",
            "ret_6m","cmp_bv","s_Quality","s_Valuation","s_Growth","s_BeatenDown","final_score"]
    print("Column weights:", {k: v[0] for k, v in COLW.items()})
    print(f"\nSaved -> {DATA/'provided_50_scored.csv'}\n")
    print(df[show].to_string(index=False))

if __name__ == "__main__":
    main()
