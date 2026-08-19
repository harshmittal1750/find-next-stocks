"""
Screen Indian (NSE) stocks that are beaten-down on price but fundamentally strong.

Data source: yfinance (Yahoo Finance) — free, .NS tickers.
Output:
  data/raw_fundamentals.csv   -> every metric pulled for the universe
  data/screen_results.csv     -> scored + ranked candidates
Run: python3 screen_indian_stocks.py
Legal: personal/internal research use only (see ../notes.md).
"""
import time, json, sys
import yfinance as yf
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)

# Curated universe: liquid NSE large/mid-caps across sectors (~Nifty 200 subset).
UNIVERSE = """
RELIANCE TCS HDFCBANK ICICIBANK INFY HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK
LT AXISBANK ASIANPAINT MARUTI BAJFINANCE HCLTECH SUNPHARMA TITAN ULTRACEMCO WIPRO
ONGC NTPC POWERGRID NESTLEIND M&M TATAMOTORS TATASTEEL JSWSTEEL ADANIENT ADANIPORTS
COALINDIA BAJAJFINSV GRASIM HDFCLIFE SBILIFE TECHM DIVISLAB DRREDDY CIPLA BRITANNIA
EICHERMOT HEROMOTOCO BAJAJ-AUTO HINDALCO INDUSINDBK APOLLOHOSP TATACONSUM PIDILITIND DABUR DMART
GODREJCP MARICO COLPAL BERGEPAINT HAVELLS SIEMENS ABB BOSCHLTD SHREECEM AMBUJACEM
ACC GAIL IOC BPCL VEDL JINDALSTEL SAIL NMDC NATIONALUM HINDPETRO
PFC RECLTD CHOLAFIN BAJAJHLDNG MUTHOOTFIN SBICARD ICICIPRULI ICICIGI HDFCAMC BANDHANBNK
FEDERALBNK IDFCFIRSTB AUBANK PNB BANKBARODA CANBK UNIONBANK INDIANB IOB UCOBANK
TVSMOTOR ASHOKLEY BHARATFORG MOTHERSON BALKRISIND MRF APOLLOTYRE EXIDEIND TIINDIA ESCORTS
LUPIN AUROPHARMA BIOCON ALKEM TORNTPHARM ZYDUSLIFE GLENMARK IPCALAB LAURUSLABS NATCOPHARM
DLF GODREJPROP OBEROIRLTY PHOENIXLTD PRESTIGE LODHA BRIGADE SOBHA NBCC IRB
TATAPOWER ADANIGREEN ADANIPOWER TORNTPOWER JSWENERGY NHPC SJVN CESC
PIIND SRF DEEPAKNTR AARTIIND NAVINFLUOR ATUL TATACHEM COROMANDEL UPL CHAMBLFERT
DIXON POLYCAB KEI AMBER VOLTAS BLUESTARCO WHIRLPOOL CROMPTON VGUARD
TRENT PAGEIND ABFRL ADANIWILSON JUBLFOOD DEVYANI WESTLIFE VBL TATAELXSI PERSISTENT
COFORGE MPHASIS LTIM LTTS OFSS NAUKRI ZOMATO PAYTM NYKAA POLICYBZR
INDIGO GMRINFRA CONCOR IRCTC IRFC RVNL HAL BEL BHEL MAZDOCK
CUMMINSIND THERMAX KAJARIACER ASTRAL SUPREMEIND FINOLEXIND APLAPOLLO RATNAMANI JINDALSAW
""".split()

WANT = ["shortName","sector","industry","currentPrice","fiftyTwoWeekHigh","fiftyTwoWeekLow",
        "trailingPE","forwardPE","priceToBook","pegRatio","returnOnEquity","returnOnAssets",
        "debtToEquity","earningsGrowth","earningsQuarterlyGrowth","revenueGrowth",
        "profitMargins","operatingMargins","grossMargins","dividendYield","marketCap",
        "currentRatio","quickRatio","freeCashflow","totalCashPerShare","bookValue"]

def fetch(sym, retries=3):
    for a in range(retries):
        try:
            info = yf.Ticker(f"{sym}.NS").info
            if info and info.get("currentPrice"):
                row = {"ticker": sym}
                row.update({k: info.get(k) for k in WANT})
                return row
        except Exception as e:
            if a == retries - 1:
                print(f"  ! {sym}: {e}", file=sys.stderr)
        time.sleep(1.5 * (a + 1))
    return {"ticker": sym, "shortName": None}

def main():
    rows = []
    n = len(UNIVERSE)
    for i, s in enumerate(UNIVERSE, 1):
        print(f"[{i}/{n}] {s}")
        rows.append(fetch(s))
        time.sleep(0.6)  # be gentle with Yahoo
    df = pd.DataFrame(rows)
    df.to_csv(DATA / "raw_fundamentals.csv", index=False)
    print(f"Saved raw -> {DATA/'raw_fundamentals.csv'} ({len(df)} rows)")

    d = df.dropna(subset=["currentPrice"]).copy()
    d["pct_below_52w_high"] = (d["fiftyTwoWeekHigh"] - d["currentPrice"]) / d["fiftyTwoWeekHigh"] * 100
    d["pct_above_52w_low"]  = (d["currentPrice"] - d["fiftyTwoWeekLow"]) / d["fiftyTwoWeekLow"] * 100
    d["roe_pct"]    = d["returnOnEquity"] * 100
    d["margin_pct"] = d["profitMargins"] * 100

    # ---- "Beaten-down but fundamentally strong" score ----
    def score(r):
        pts, reasons = 0, []
        # DOWN signal (price weakness = opportunity)
        if r["pct_below_52w_high"] >= 30: pts += 3; reasons.append(f"{r['pct_below_52w_high']:.0f}% below 52w high")
        elif r["pct_below_52w_high"] >= 20: pts += 2; reasons.append(f"{r['pct_below_52w_high']:.0f}% below 52w high")
        elif r["pct_below_52w_high"] >= 12: pts += 1
        # FUNDAMENTAL strength
        roe = r.get("returnOnEquity")
        if roe is not None:
            if roe >= 0.18: pts += 3; reasons.append(f"ROE {roe*100:.0f}%")
            elif roe >= 0.12: pts += 2
            elif roe < 0: pts -= 2
        de = r.get("debtToEquity")
        if de is not None:
            if de < 50: pts += 2; reasons.append(f"low D/E {de:.0f}")
            elif de < 100: pts += 1
            elif de > 200: pts -= 2
        pm = r.get("profitMargins")
        if pm is not None:
            if pm >= 0.15: pts += 2; reasons.append(f"margin {pm*100:.0f}%")
            elif pm > 0: pts += 1
            else: pts -= 2
        eg = r.get("earningsGrowth")
        if eg is not None:
            if eg >= 0.15: pts += 2; reasons.append(f"earnings +{eg*100:.0f}%")
            elif eg > 0: pts += 1
            elif eg < -0.10: pts -= 1
        rg = r.get("revenueGrowth")
        if rg is not None and rg >= 0.10: pts += 1; reasons.append(f"rev +{rg*100:.0f}%")
        pe = r.get("trailingPE")
        if pe is not None and 0 < pe < 18: pts += 1; reasons.append(f"PE {pe:.0f}")
        pb = r.get("priceToBook")
        if pb is not None and 0 < pb < 3: pts += 1
        return pd.Series({"score": pts, "reasons": "; ".join(reasons)})

    d[["score","reasons"]] = d.apply(score, axis=1)
    out = d.sort_values("score", ascending=False)[
        ["ticker","shortName","sector","currentPrice","pct_below_52w_high","pct_above_52w_low",
         "trailingPE","priceToBook","roe_pct","debtToEquity","margin_pct","earningsGrowth",
         "revenueGrowth","marketCap","score","reasons"]]
    out.to_csv(DATA / "screen_results.csv", index=False)
    print(f"Saved results -> {DATA/'screen_results.csv'}")
    print("\nTOP 20 beaten-down + fundamentally strong:")
    pd.set_option("display.width", 200, "display.max_columns", 20)
    print(out.head(20).to_string(index=False))

if __name__ == "__main__":
    main()
