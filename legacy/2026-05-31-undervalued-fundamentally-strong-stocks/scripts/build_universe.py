"""
Stage 1 — build the investable universe: ALL NSE equities with market cap >= MIN_MCAP_CR.

Source: nselib equity_list() (all NSE symbols) + yfinance fast_info (market cap).
RESUMABLE: appends to data/universe_mcap.csv as it goes; re-running skips done symbols.
So if Yahoo rate-limits/interrupts, just run again — it continues where it left off.

Output:
  data/universe_mcap.csv     -> every symbol attempted: symbol, name, mcap_cr, price, status
  data/universe_filtered.csv -> symbols with mcap_cr >= MIN_MCAP_CR (the new universe)

Run: ./.venv/bin/python build_universe.py
Legal: personal/internal research use only.
"""
import warnings, time, sys, csv
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
from pathlib import Path
from nselib import capital_market as cm

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)
MCAP_CSV = DATA / "universe_mcap.csv"
MIN_MCAP_CR = 1000.0
PACE = 0.4

def done_symbols():
    if not MCAP_CSV.exists(): return set()
    try: return set(pd.read_csv(MCAP_CSV)["symbol"].astype(str))
    except Exception: return set()

def mcap_cr(sym):
    try:
        fi = yf.Ticker(f"{sym}.NS").fast_info
        mc = getattr(fi, "market_cap", None)
        px = getattr(fi, "last_price", None)
        return (round(mc / 1e7, 1) if mc else None), (round(px, 2) if px else None)
    except Exception:
        return None, None

def main():
    el = cm.equity_list()
    el = el[el[" SERIES"].astype(str).str.strip() == "EQ"]  # main-board equities only
    el["SYMBOL"] = el["SYMBOL"].astype(str).str.strip()
    names = dict(zip(el["SYMBOL"], el["NAME OF COMPANY"].astype(str).str.strip()))
    all_syms = list(el["SYMBOL"])

    done = done_symbols()
    todo = [s for s in all_syms if s not in done]
    print(f"NSE EQ symbols: {len(all_syms)} | already done: {len(done)} | to fetch: {len(todo)}")

    new = not MCAP_CSV.exists()
    f = open(MCAP_CSV, "a", newline="")
    w = csv.writer(f)
    if new: w.writerow(["symbol", "name", "mcap_cr", "price", "status"])

    for i, s in enumerate(todo, 1):
        mc, px = mcap_cr(s)
        w.writerow([s, names.get(s, ""), mc if mc is not None else "", px if px is not None else "",
                    "ok" if mc is not None else "no_mcap"])
        if i % 25 == 0:
            f.flush()
            print(f"  [{i}/{len(todo)}] last={s} mcap_cr={mc}")
        time.sleep(PACE)
    f.flush(); f.close()

    df = pd.read_csv(MCAP_CSV)
    df["mcap_cr"] = pd.to_numeric(df["mcap_cr"], errors="coerce")
    filt = df[df["mcap_cr"] >= MIN_MCAP_CR].sort_values("mcap_cr", ascending=False)
    filt.to_csv(DATA / "universe_filtered.csv", index=False)
    print(f"\nTotal attempted: {len(df)} | with mcap: {df['mcap_cr'].notna().sum()} "
          f"| >= {MIN_MCAP_CR:.0f} cr: {len(filt)}")
    print(f"Saved -> {DATA/'universe_filtered.csv'}")

if __name__ == "__main__":
    main()
