"""
retry_blanks.py — second pass over symbols that returned no market cap in build_universe.py.
Yahoo often answers on retry. Updates universe_mcap.csv in place and rebuilds
universe_filtered.csv. Reports how many newly cross the >=1000cr line.

Run (after the main pipeline, to avoid Yahoo contention):
  ./.venv/bin/python retry_blanks.py
"""
import warnings, time
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MCAP = DATA / "universe_mcap.csv"
MIN_MCAP_CR = 1000.0

def mcap_cr(sym):
    try:
        fi = yf.Ticker(f"{sym}.NS").fast_info
        mc = getattr(fi, "market_cap", None); px = getattr(fi, "last_price", None)
        return (round(mc/1e7,1) if mc else None), (round(px,2) if px else None)
    except Exception:
        return None, None

def main():
    df = pd.read_csv(MCAP)
    df["mcap_cr"] = pd.to_numeric(df["mcap_cr"], errors="coerce")
    blanks = df[df["mcap_cr"].isna()].copy()
    print(f"blanks to retry: {len(blanks)}")
    recovered = 0
    for idx, row in blanks.iterrows():
        mc, px = mcap_cr(str(row["symbol"]))
        if mc is not None:
            df.at[idx, "mcap_cr"] = mc
            df.at[idx, "price"] = px
            df.at[idx, "status"] = "ok_retry"
            recovered += 1
        time.sleep(0.6)
    df.to_csv(MCAP, index=False)
    filt = df[df["mcap_cr"] >= MIN_MCAP_CR].sort_values("mcap_cr", ascending=False)
    filt.to_csv(DATA / "universe_filtered.csv", index=False)
    new_qual = (filt["status"] == "ok_retry").sum() if "status" in filt else 0
    print(f"recovered mcap for {recovered} symbols | newly >=1000cr: {new_qual} "
          f"| total >=1000cr now: {len(filt)}")

if __name__ == "__main__":
    main()
