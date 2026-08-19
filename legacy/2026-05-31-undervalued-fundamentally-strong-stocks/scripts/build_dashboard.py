"""
build_dashboard.py — turn data/final_ranking.csv into an interactive HTML dashboard
plus a separately versioned JSON payload for spotting beaten-down-but-strong stocks.

Vanilla JS + hand-rolled SVG charts, NO CDN / no external dependencies. Serve the
research folder over HTTP so the browser can load ``site/dashboard-data.json``.
Features:
  - "The Map" scatter: valuation cheapness (x) vs quality (y), bubble = mcap, colour = sector,
    with a CHEAP + STRONG quadrant highlighted -> the contrarian-value sweet spot.
  - Top-N bar chart of final_score.
  - Sector view: average final_score + count per sector.
  - Sortable / filterable table of every stock; click a row to see its 7-factor breakdown.
  - Tooltips on EVERY abbreviation / short form (hover the ⓘ or the column header).
  - Patterns & Insights: accumulation signals, near-52w-low quality, value traps, score
    distribution histogram, sector concentration chart.
  - Rank change vs previous week (from data/history/).

Run: ./.venv/bin/python build_dashboard.py   ->  ../DASHBOARD.html
Legal: personal/internal research use only.
"""
import csv, json, glob, datetime as dt
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CSV = BASE / "data" / "final_ranking.csv"
OUT = BASE / "DASHBOARD.html"
DATA_JSON = BASE / "site" / "dashboard-data.json"
HIST_DIR = BASE / "data" / "history"
RANK_TRACKER = BASE / "data" / "rank_tracker.csv"

# --- glossary: every short form -> plain-English explanation (used for tooltips) ---
GLOSSARY = {
    "rank": "Overall rank in the model (1 = best blend of cheap + fundamentally strong).",
    "ticker": "NSE trading symbol.",
    "shortName": "Company name.",
    "sector": "Broad industry the company belongs to.",
    "mcap_cr": "Market Capitalisation in ₹ crore = share price × number of shares. The total size of the company. (1 crore = 10 million.)",
    "currentPrice": "CMP — Current Market Price, the latest traded price per share in ₹.",
    "pct_below_52w_high": "How far the price has fallen from its highest point in the last 52 weeks (one year), in %. Higher = more 'on sale' / beaten down.",
    "trailingPE": "P/E — Price-to-Earnings ratio = price ÷ earnings per share. How many rupees you pay for ₹1 of annual profit. Lower is cheaper (but very low can signal trouble).",
    "roe_pct": "ROE — Return on Equity, in %. Profit generated per ₹100 of shareholders' money. Higher = more efficient, higher-quality business (>15% is good).",
    "promoter_pct": "Promoter holding %. The stake owned by the founders / controlling group. High & stable promoter holding signals confidence ('skin in the game').",
    "institutional_pct": "Institutional holding %. The stake held by mutual funds, insurers, FIIs etc. ('smart money'). Higher = more professional conviction.",
    "delivery_trend": "Delivery % trend — change in the share of trades taken to delivery (not intraday) recently vs earlier. Positive = rising real ownership, our FREE PROXY for accumulation (smart money entering).",
    "upside_pct": "Analyst target upside %. How far brokerages' average 12-month price target sits above the current price. Positive = expected to rise.",
    "recommendationMean": "Analyst recommendation score: 1 = Strong Buy, 2 = Buy, 3 = Hold, 4 = Sell, 5 = Strong Sell. LOWER is more bullish.",
    "final_score": "Coverage-adjusted 0–100 score blending all 7 factor groups. It is shrunk toward neutral (50) when inputs are missing. Stocks below 60% weighted coverage, or without enough quality/valuation evidence, are left unranked.",
    "g_quality": "QUALITY factor (0–100): how good the business is — ROE, ROA, EBITDA margin, low debt, current ratio. Weight 2.5 (highest).",
    "g_smart_money": "SMART-MONEY factor (0–100): who owns it — institutional %, number of institutions, promoter %, delivery trend & delivery %. Weight 2.0.",
    "g_valuation": "VALUATION factor (0–100): how cheap it is — P/E, P/B, PEG, EV/EBITDA, dividend yield. Higher score = cheaper. Weight 2.0.",
    "g_growth": "GROWTH factor (0–100): earnings growth, revenue growth, quarterly earnings growth, forward-EPS growth. Weight 1.75.",
    "g_price_setup": "PRICE-SETUP factor (0–100): the 'down' part — % below 52-week high plus price vs 50-day average (is it bottoming/turning?). Weight 1.5.",
    "g_analyst": "ANALYST factor (0–100): target upside %, recommendation, and how many analysts cover it. Weight 1.25.",
    "g_momentum": "MOMENTUM factor (0–100): 1-year price change and price vs 200-day average — avoids pure 'falling knives'. Weight 0.75 (lowest).",
    "beta": "Beta — how sharply the share moves vs the overall market. Beta 1 = moves with the market; 1.5 = ~50% more violent (falls harder in a crash); <1 = steadier. Drives the drawdown projection. (Missing → assumed 1.0.)",
    "fiftyTwoWeekLow": "The lowest price the stock traded at in the last 52 weeks — a historical support / floor reference.",
    "twoHundredDayAverage": "200-DMA — average closing price over the last 200 trading days. A widely-watched long-term support level; prices often bounce near it.",
    "fiftyDayAverage": "50-DMA — average closing price over the last 50 trading days; a short-term trend reference.",
    "buy_target": "IDEAL BUY price (₹) — where this stock would likely trade if the market corrects by the chosen drawdown %, estimated as CMP × (1 − beta × drawdown%). Accumulate at or below this for the best entry. Adjust the 'Market drawdown scenario' slider to change it. This is a scenario estimate, NOT a guaranteed level.",
    "disc_pct": "To-buy % — how far the current price sits ABOVE the ideal buy price (= beta × drawdown%). Small/zero = already near its ideal entry (buy-now candidate). Large = needs a bigger market fall first. Sort ascending to find stocks ready to accumulate today.",
    "data_cov": "Weighted data coverage % — how much of this stock's score is backed by real inputs, weighted by each metric's contribution. Scores are shrunk toward neutral as coverage falls. Below 60%, or without enough quality/valuation evidence, the stock is shown but not ranked.",
    "rank_chg": "Rank change vs previous week. ▲ = moved up (improved), ▼ = fell (deteriorated). — = no prior data.",
    "rank_vs_staged": "Current working ranking vs the Git index (staged changes). Positive/▲ means the stock moved up; negative/▼ means it moved down. Score movement is shown in the stock detail.",
    "staged_rank_vs_pushed": "Git index (staged ranking) vs the upstream branch's last locally-known pushed commit. Positive/▲ means the staged stock moved up; negative/▼ means it moved down.",
    "rank_vs_pushed": "Current working ranking vs the upstream branch's last locally-known pushed commit. Positive/▲ means the stock moved up; negative/▼ means it moved down. Score movement is shown in the stock detail.",
    "price_chg_pct": "Weekly price change % — how much the stock's price moved since the previous ranking run. Green = gained, red = fell. — = no prior price data.",
}

# extra finance abbreviations shown in the glossary panel (not necessarily columns)
EXTRA_TERMS = {
    "NSE / BSE": "National Stock Exchange / Bombay Stock Exchange — India's two main stock exchanges.",
    "FII / FPI": "Foreign Institutional / Portfolio Investor — overseas funds investing in Indian stocks.",
    "DII": "Domestic Institutional Investor — Indian mutual funds, insurers (e.g. LIC), banks, pension funds.",
    "P/B": "Price-to-Book = price ÷ book value per share. <1 means trading below accounting net worth.",
    "PEG": "P/E ÷ earnings growth rate. <1 can mean growth is cheaply priced.",
    "EV/EBITDA": "Enterprise Value ÷ operating earnings — a debt-aware valuation multiple; lower = cheaper.",
    "EBITDA margin": "Operating profit (before interest, tax, depreciation) as a % of revenue. Higher = more profitable operations.",
    "ROA": "Return on Assets — profit per ₹100 of total assets; efficiency of asset use.",
    "EPS": "Earnings Per Share — net profit ÷ number of shares.",
    "DMA": "Day Moving Average (e.g. 50-DMA, 200-DMA) — the average price over the last N days; a trend reference.",
    "Dividend yield": "Annual dividend ÷ price, in % — cash income return from holding the stock.",
    "Debt-to-Equity": "Total debt ÷ shareholders' equity. Lower = safer balance sheet.",
    "Current ratio": "Current assets ÷ current liabilities. >1 means short-term bills are covered.",
    "Value trap": "A stock that looks cheap but keeps falling because the business is genuinely deteriorating — cheap for a reason.",
    "Delivery %": "Share of traded volume actually settled into demat accounts (real buying) vs intraday speculation.",
    "Drawdown scenario": "A hypothetical market-wide fall (e.g. −15%). The dashboard projects each stock's dip using its beta to estimate the ideal accumulation price. Bigger assumed drawdown ⇒ lower buy targets.",
    "Buy zone": "A price band (shallow-dip → full-drawdown estimate) where accumulating offers a better margin of safety than the current price.",
    "Accumulation signal": "A stock where delivery trend is rising (+5 or more) AND institutional holding ≥30% — suggesting organised/smart money is actively building positions.",
    "Near-52w-low quality": "A high-quality stock (quality score ≥60) trading within 20% of its 52-week low. These are genuinely beaten-down names with strong fundamentals — prime contrarian candidates.",
    "Value trap": "A stock that is deeply discounted (>30% off high), poor quality (score <40), AND delivery trend is falling — price is cheap for a reason, not a bargain.",
    "Score distribution": "Histogram of all 1,353 stocks by final score. Most stocks cluster in the 45–55 range. Stocks scoring 60+ are in the top 15% of the universe.",
}

NUM = {"rank","mcap_cr","currentPrice","pct_below_52w_high","trailingPE","roe_pct","promoter_pct",
       "institutional_pct","delivery_trend","upside_pct","recommendationMean","g_quality",
       "g_smart_money","g_valuation","g_growth","g_price_setup","g_analyst","g_momentum",
       "data_cov","quality_cov","valuation_cov","model_score","final_score",
       "current_rank","current_score","staged_rank","staged_score","pushed_rank","pushed_score",
       "rank_vs_staged","score_vs_staged","staged_rank_vs_pushed","staged_score_vs_pushed",
       "rank_vs_pushed","score_vs_pushed",
       # merged from raw_fundamentals for the drawdown / buy-zone feature:
       "fiftyTwoWeekHigh","fiftyTwoWeekLow","twoHundredDayAverage","fiftyDayAverage","beta"}

# pulled from raw_fundamentals.csv and merged onto each ranked row by ticker
EXTRA_COLS = ["fiftyTwoWeekHigh","fiftyTwoWeekLow","twoHundredDayAverage","fiftyDayAverage","beta"]

def numify(v):
    try: return round(float(v), 4) if v not in ("", None) else None
    except (ValueError, TypeError): return None

def load_extra():
    raw = BASE / "data" / "raw_fundamentals.csv"
    out = {}
    if not raw.exists(): return out
    with open(raw, newline="") as f:
        for r in csv.DictReader(f):
            out[r.get("ticker")] = {k: numify(r.get(k)) for k in EXTRA_COLS}
    return out

def load_rank_tracker():
    """Load Git snapshot movements generated by build_rank_tracker.py."""
    out = {}
    if not RANK_TRACKER.exists():
        return out
    numeric = {
        "current_rank", "current_score", "staged_rank", "staged_score",
        "pushed_rank", "pushed_score", "rank_vs_staged", "score_vs_staged",
        "staged_rank_vs_pushed", "staged_score_vs_pushed",
        "rank_vs_pushed", "score_vs_pushed",
    }
    with open(RANK_TRACKER, newline="") as f:
        for row in csv.DictReader(f):
            ticker = row.get("ticker")
            if not ticker:
                continue
            out[ticker] = {
                key: numify(value) if key in numeric else value
                for key, value in row.items()
                if key not in {"ticker", "shortName", "sector"}
            }
    return out

# the 24 metrics rank_all.py actually scores on — used to compute per-stock data coverage
SCORING_METRICS_RAW = ["returnOnEquity","returnOnAssets","ebitdaMargins","debtToEquity","currentRatio",
    "trailingPE","priceToBook","pegRatio","enterpriseToEbitda","dividendYield",
    "earningsGrowth","revenueGrowth","earningsQuarterlyGrowth","forwardEps",
    "targetMeanPrice","recommendationMean","numberOfAnalystOpinions",
    "fiftyTwoWeekChangePercent","twoHundredDayAverage"]
SCORING_METRICS_SHP = ["institutional_pct","institutions_count","promoter_pct","avg_delivery_pct","delivery_trend"]

def load_coverage():
    """Per-ticker % of the 24 scoring metrics that have real (non-blank) data."""
    raw = BASE / "data" / "raw_fundamentals.csv"
    shp = BASE / "data" / "shareholding_layer.csv"
    total = len(SCORING_METRICS_RAW) + len(SCORING_METRICS_SHP)
    present = {}
    if raw.exists():
        with open(raw, newline="") as f:
            for r in csv.DictReader(f):
                present[r.get("ticker")] = sum(1 for k in SCORING_METRICS_RAW if numify(r.get(k)) is not None)
    if shp.exists():
        with open(shp, newline="") as f:
            for r in csv.DictReader(f):
                t = r.get("ticker")
                present[t] = present.get(t, 0) + sum(1 for k in SCORING_METRICS_SHP if numify(r.get(k)) is not None)
    return {t: round(p / total * 100) for t, p in present.items()}

def load_history_ranks():
    """Load previous week's ranks and prices from the most recent history file."""
    if not HIST_DIR.exists():
        return {}, {}, ""
    files = sorted(HIST_DIR.glob("final_ranking_*.csv"))
    if not files:
        return {}, {}, ""
    prev = files[-1]
    ranks, prices = {}, {}
    try:
        with open(prev, newline="") as f:
            for r in csv.DictReader(f):
                t = r.get("ticker")
                rk = r.get("rank")
                cp = r.get("currentPrice")
                if t and rk:
                    try: ranks[t] = int(rk)
                    except ValueError: pass
                if t and cp:
                    try: prices[t] = float(cp)
                    except ValueError: pass
    except Exception:
        pass
    return ranks, prices, str(prev.name)

def compute_patterns(rows, extra_map):
    """Tag each row with pattern flags for the insights panel."""
    for r in rows:
        tk = r.get("ticker")
        ex = extra_map.get(tk, {})
        cmp = r.get("currentPrice")
        lo = ex.get("fiftyTwoWeekLow")
        hi = ex.get("fiftyTwoWeekHigh") or (cmp / (1 - (r.get("pct_below_52w_high") or 0) / 100) if cmp and r.get("pct_below_52w_high") else None)
        # pct_above_52w_low: 0=at low, 100=at high
        if cmp and lo and hi and hi > lo:
            r["pct_above_52w_low"] = round((cmp - lo) / (hi - lo) * 100, 1)
        else:
            r["pct_above_52w_low"] = None

        dt_ = r.get("delivery_trend")
        inst = r.get("institutional_pct")
        qual = r.get("g_quality")
        off_high = r.get("pct_below_52w_high")
        score = r.get("final_score")

        # Accumulation: delivery rising + significant institutional holding
        r["pat_accum"] = bool(
            dt_ is not None and dt_ >= 5 and
            inst is not None and inst >= 30 and
            score is not None and score >= 55
        )
        # Value trap: deeply discounted + poor quality + delivery falling
        r["pat_trap"] = bool(
            off_high is not None and off_high >= 30 and
            qual is not None and qual < 40 and
            dt_ is not None and dt_ <= -5
        )
        # Near 52w low + high quality (buy zone candidate)
        r["pat_near_low"] = bool(
            score is not None and r["pct_above_52w_low"] is not None and r["pct_above_52w_low"] < 20 and
            qual is not None and qual >= 60
        )
    return rows

def build_insights(rows, prev_prices=None):
    """Pre-compute top lists for each pattern for the insights panel."""
    accum = sorted([r for r in rows if r.get("pat_accum")], key=lambda r: -(r.get("final_score") or 0))[:10]
    near_low = sorted([r for r in rows if r.get("pat_near_low")], key=lambda r: -(r.get("final_score") or 0))[:10]
    traps = sorted([r for r in rows if r.get("pat_trap")], key=lambda r: (r.get("final_score") or 100))[:10]

    # score distribution
    buckets = [(0,40),(40,45),(45,50),(50,55),(55,60),(60,65),(65,100)]
    dist = []
    for lo, hi in buckets:
        n = sum(1 for r in rows if r.get("final_score") is not None and lo <= r["final_score"] < hi)
        dist.append({"label": f"{lo}–{hi}", "count": n})

    # sector concentration in cheap & strong
    cs_sector = {}
    all_sector = {}
    for r in rows:
        s = r.get("sector") or "Unknown"
        all_sector[s] = all_sector.get(s, 0) + 1
        if (r.get("final_score") is not None and r.get("g_valuation") and r["g_valuation"] >= 55 and
                r.get("g_quality") and r["g_quality"] >= 55 and (r.get("pct_below_52w_high") or 0) >= 10):
            cs_sector[s] = cs_sector.get(s, 0) + 1

    sector_conc = sorted([
        {"sector": s, "cs_count": cs_sector.get(s, 0), "total": all_sector[s],
         "pct": round(cs_sector.get(s, 0) / all_sector[s] * 100)}
        for s in all_sector
    ], key=lambda x: -x["cs_count"])

    # weekly price movers (vs previous ranking's prices)
    gainers, fallers = [], []
    if prev_prices:
        for r in rows:
            tk = r.get("ticker")
            old_p = prev_prices.get(tk)
            new_p = r.get("currentPrice")
            if old_p and new_p and old_p > 0:
                chg = round((new_p - old_p) / old_p * 100, 1)
                r["price_chg_pct"] = chg
            else:
                r["price_chg_pct"] = None
        gainers = sorted([r for r in rows if (r.get("price_chg_pct") or 0) > 5],
                         key=lambda r: -(r.get("price_chg_pct") or 0))[:10]
        fallers = sorted([r for r in rows if (r.get("price_chg_pct") or 0) < -5],
                         key=lambda r: (r.get("price_chg_pct") or 0))[:10]

    # sector performance this week
    sector_perf = {}
    for r in rows:
        s = r.get("sector") or "Unknown"
        chg = r.get("price_chg_pct")
        if chg is not None:
            sector_perf.setdefault(s, []).append(chg)
    sector_weekly = sorted([
        {"sector": s, "avg_chg": round(sum(v)/len(v), 1), "count": len(v)}
        for s, v in sector_perf.items() if v
    ], key=lambda x: -x["avg_chg"])

    return {
        "accum": accum,
        "near_low": near_low,
        "traps": traps,
        "dist": dist,
        "sector_conc": sector_conc,
        "gainers": gainers,
        "fallers": fallers,
        "sector_weekly": sector_weekly,
    }

def load():
    extra = load_extra()
    cov = load_coverage()
    tracker = load_rank_tracker()

    prev_ranks, prev_prices, hist_label = load_history_ranks()

    rows = []
    with open(CSV, newline="") as f:
        for r in csv.DictReader(f):
            o = {}
            for k, v in r.items():
                if k in NUM:
                    try: o[k] = round(float(v), 2) if v not in ("", None) else None
                    except ValueError: o[k] = None
                else:
                    o[k] = v
            o.update(extra.get(o.get("ticker"), {k: None for k in EXTRA_COLS}))
            if o.get("data_cov") is None:
                o["data_cov"] = cov.get(o.get("ticker"))
            # rank change vs previous week
            tk = o.get("ticker")
            o.update(tracker.get(tk, {}))
            cur_rank = int(o["rank"]) if o.get("rank") else None
            prev_rank = prev_ranks.get(tk)
            if cur_rank and prev_rank:
                o["rank_chg"] = prev_rank - cur_rank  # positive = improved (rank went lower number)
            else:
                o["rank_chg"] = None
            rows.append(o)

    rows = compute_patterns(rows, extra)
    insights = build_insights(rows, prev_prices=prev_prices)

    refreshed = dt.datetime.now().strftime("%d %b %Y %H:%M")
    return rows, insights, refreshed, hist_label

def main():
    rows, insights, refreshed, hist_label = load()
    # Keep the generated JSON in ticker order so a rank change edits the stock's
    # fields in place instead of moving most of the array in Git diffs.
    stable_rows = sorted(rows, key=lambda row: str(row.get("ticker") or ""))
    document = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "refreshed": refreshed,
        "history_label": hist_label,
        "record_count": len(rows),
        "source_files": [
            "data/final_ranking.csv",
            "data/raw_fundamentals.csv",
            "data/shareholding_layer.csv",
            "data/rank_tracker.csv",
        ],
        "stocks": stable_rows,
        "insights": insights,
        "glossary": GLOSSARY,
        "extra_terms": EXTRA_TERMS,
    }
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    temp = DATA_JSON.with_suffix(DATA_JSON.suffix + ".tmp")
    temp.write_text(json.dumps(document, indent=2, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(DATA_JSON)
    OUT.write_text(TEMPLATE, encoding="utf-8")
    accum_n = sum(1 for r in rows if r.get("pat_accum"))
    near_n  = sum(1 for r in rows if r.get("pat_near_low"))
    trap_n  = sum(1 for r in rows if r.get("pat_trap"))
    print(f"Wrote {OUT} and {DATA_JSON} ({len(rows)} stocks)")
    print(f"Patterns — Accumulation: {accum_n}  Near-52w-low+quality: {near_n}  Value traps: {trap_n}")

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Indian Stocks — Cheap &amp; Strong Dashboard</title>
<style>
:root{
  --bg:#0c1018; --panel:#141b29; --panel2:#1b2435; --line:#26324a; --ink:#e8edf6;
  --muted:#93a1b8; --accent:#4fd1c5; --accent2:#7c9cff; --good:#34d399; --warn:#fbbf24;
  --bad:#f87171; --chip:#202a3e;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent2)}
.wrap{max-width:1500px;margin:0 auto;padding:24px}
header h1{margin:0 0 4px;font-size:26px;letter-spacing:.2px}
header p{margin:0;color:var(--muted)}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;min-width:120px}
.stat b{display:block;font-size:22px}.stat span{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:1.35fr 1fr;gap:18px;margin-top:8px}
@media(max-width:1050px){.grid{grid-template-columns:1fr}}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;margin-top:18px}
@media(max-width:1100px){.grid3{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.grid3{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:18px}
.card h2{margin:0 0 2px;font-size:16px}
.card .sub{color:var(--muted);font-size:12px;margin-bottom:10px}
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center;background:var(--panel);
  border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.controls label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
input[type=text],select{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
  border-radius:8px;padding:7px 10px;font-size:13px;min-width:150px}
input[type=range]{width:150px;accent-color:var(--accent)}
.toggle{flex-direction:row !important;align-items:center;gap:8px;cursor:pointer;margin-top:14px}
.toggle input{accent-color:var(--accent);width:16px;height:16px}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.legend .lg{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);cursor:pointer;
  padding:2px 8px;border-radius:20px;border:1px solid transparent}
.legend .lg.off{opacity:.35}.legend .lg:hover{border-color:var(--line)}
.dot{width:11px;height:11px;border-radius:50%}
svg{display:block;width:100%;height:auto}
.axis{stroke:var(--line)}.tick{fill:var(--muted);font-size:11px}
.axlabel{fill:var(--muted);font-size:12px}
.quad{fill:#34d39912}.quadlabel{fill:#34d399aa;font-size:12px;font-weight:600}
.bub{cursor:pointer;stroke:#0c1018;stroke-width:.6}
.bar{cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:nth-child(5),td:nth-child(5),th:nth-child(6),td:nth-child(6),th:nth-child(7),td:nth-child(7){text-align:left}
th{position:sticky;top:0;background:var(--panel2);cursor:pointer;user-select:none;font-weight:600;
  color:var(--muted);border-bottom:2px solid var(--line)}
th:hover{color:var(--ink)}
th .ar{font-size:10px;opacity:.6}
tbody tr{cursor:pointer}
tbody tr:hover{background:#1b2640}
tbody tr.sel{background:#1d3349;outline:1px solid var(--accent)}
.tablewrap{max-height:620px;overflow:auto;border:1px solid var(--line);border-radius:12px}
.score-pill{display:inline-block;min-width:38px;padding:2px 8px;border-radius:20px;font-weight:700;color:#08121f}
.dim{color:var(--muted)}
.help{cursor:help;color:var(--accent);font-weight:700;font-size:11px;border:1px solid var(--accent);
  border-radius:50%;width:14px;height:14px;display:inline-flex;align-items:center;justify-content:center;
  line-height:1;margin-left:4px;vertical-align:middle}
/* floating tooltip */
#tip{position:fixed;z-index:99;max-width:300px;background:#05080e;color:var(--ink);
  border:1px solid var(--accent);border-radius:10px;padding:10px 12px;font-size:12.5px;line-height:1.45;
  box-shadow:0 8px 30px #000a;pointer-events:none;opacity:0;transition:opacity .1s;display:none}
#tip b{color:var(--accent)}
.fac{display:grid;grid-template-columns:120px 1fr 42px;gap:8px;align-items:center;margin:7px 0;font-size:12.5px}
.fac .track{background:var(--panel2);border-radius:6px;height:12px;overflow:hidden}
.fac .fill{height:100%;border-radius:6px}
.fac .nm{color:var(--muted);cursor:help}
.detail .hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.detail .hd .t{font-size:18px;font-weight:700}
.kv{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0;color:var(--muted);font-size:12.5px}
.kv b{color:var(--ink)}
.gloss{columns:2;column-gap:30px}
@media(max-width:760px){.gloss{columns:1}}
.gloss dt{font-weight:700;color:var(--accent);margin-top:10px}
.gloss dd{margin:2px 0 0;color:var(--muted);font-size:12.5px}
.note{color:var(--muted);font-size:12px;margin-top:6px}
footer{color:var(--muted);font-size:12px;margin:30px 0 10px;border-top:1px solid var(--line);padding-top:14px}
.empty{color:var(--muted);padding:20px;text-align:center}
/* pattern badges */
.badge{display:inline-block;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:700;margin-right:4px;margin-top:3px}
.badge-accum{background:#34d39922;color:#34d399;border:1px solid #34d39966}
.badge-near{background:#4fd1c522;color:#4fd1c5;border:1px solid #4fd1c566}
.badge-trap{background:#f8717122;color:#f87171;border:1px solid #f8717166}
/* mini-table inside insight cards */
.ins-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
.ins-tbl td,.ins-tbl th{padding:5px 8px;text-align:left;border-bottom:1px solid var(--line)}
.ins-tbl th{color:var(--muted);font-weight:600;font-size:11px;background:var(--panel2)}
.ins-tbl tbody tr{cursor:pointer}.ins-tbl tbody tr:hover{background:var(--panel2)}
.ins-tbl .tk{font-weight:700;color:var(--accent)}
.ins-tbl .sc{font-weight:700}
/* rank change */
.up{color:var(--good);font-weight:700}.dn{color:var(--bad);font-weight:700}
/* score dist histogram */
.hist-bar{fill:var(--accent2);opacity:.75}
.hist-bar.top{fill:var(--good);opacity:.9}
/* refresh badge */
.refresh-badge{display:inline-block;background:var(--chip);border:1px solid var(--line);
  border-radius:8px;padding:2px 10px;font-size:11.5px;color:var(--muted);vertical-align:middle;margin-left:10px}
</style></head>
<body><div class="wrap">
<header>
  <h1>🇮🇳 Beaten-down but Fundamentally Strong — Stock Dashboard <span class="refresh-badge" id="refresh-badge"></span></h1>
  <p>1,353 NSE stocks ≥ ₹1,000 Cr, ranked on 7 weighted factors. <b>Hover any <span class="help">i</span> or column header</b> for a plain-English explanation. Click a row to see its factor breakdown. <span class="dim">Research use only — verify finalists on Screener.in.</span></p>
</header>
<div class="stats" id="stats"></div>

<div class="controls">
  <label>Search ticker / name<input type="text" id="q" placeholder="e.g. HINDZINC, Oberoi…"></label>
  <label>Sector<select id="sector"><option value="">All sectors</option></select></label>
  <label>Min final score <span id="msv" class="dim"></span><input type="range" id="ms" min="0" max="100" value="0"></label>
  <label>Min market cap (₹ Cr) <span id="mcv" class="dim"></span><input type="range" id="mc" min="0" max="50000" step="500" value="0"></label>
  <label class="toggle"><input type="checkbox" id="cheapstrong"> Cheap <b>&amp;</b> Strong only <span class="help" data-k="_cheapstrong">i</span></label>
  <label>Min data coverage <span id="dcv" class="dim">0%</span> <span class="help" data-k="data_cov">i</span><input type="range" id="dc" min="0" max="100" step="5" value="0"></label>
  <label style="border-left:1px solid var(--line);padding-left:14px">📉 Market drawdown scenario <b id="drawv" style="color:var(--accent)">−15%</b> <span class="help" data-k="_drawdown">i</span><input type="range" id="draw" min="0" max="40" step="1" value="15"></label>
  <label style="margin-left:auto"><span class="dim" id="count"></span></label>
</div>

<div class="grid">
  <div class="card">
    <h2>The Map — Valuation vs Quality <span class="help" data-k="_map">i</span></h2>
    <div class="sub">X = cheapness (valuation score) · Y = business quality · bubble size = market cap · colour = sector. <b style="color:var(--good)">Top-right green zone = cheap AND strong</b> — the contrarian sweet spot.</div>
    <div id="scatter"></div>
    <div class="legend" id="legend"></div>
  </div>
  <div class="card detail" id="detail">
    <div class="sub">Click any bubble, bar, or table row to inspect a stock here.</div>
    <div id="detailbody" class="empty">No stock selected.</div>
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2>Top 20 by Final Score <span class="help" data-k="final_score">i</span></h2>
    <div class="sub">The best overall blend of cheap + strong in the current filter.</div>
    <div id="topbars"></div>
  </div>
  <div class="card">
    <h2>Sector Scan <span class="help" data-k="sector">i</span></h2>
    <div class="sub">Average final score per sector (bar) and number of stocks (label). Where is value concentrated?</div>
    <div id="sectorbars"></div>
  </div>
</div>

<!-- PATTERNS & INSIGHTS -->
<div class="card" style="margin-top:18px">
  <h2>🔍 Patterns &amp; Insights</h2>
  <div class="sub">Auto-detected signals across the universe. Click any row to inspect the stock. Patterns refresh with data.</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:12px;border-bottom:1px solid var(--line);padding-bottom:12px">
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-accum" onclick="switchPTab('accum')">🟢 Accumulation signals</div>
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-near" onclick="switchPTab('near')">🎯 Near 52w low + quality</div>
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-trap" onclick="switchPTab('trap')">⚠️ Value traps</div>
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-movers" onclick="switchPTab('movers')">📈 Weekly movers</div>
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-dist" onclick="switchPTab('dist')">📊 Score distribution</div>
    <div style="cursor:pointer;padding:5px 14px;border-radius:8px;font-size:13px" id="ptab-sector" onclick="switchPTab('sector')">🏭 Sector heat</div>
  </div>
  <div id="ptab-content"></div>
</div>

<div class="card">
  <h2>All Stocks <span class="help" data-k="_table">i</span></h2>
  <div class="sub">Click a header to sort · click a row to inspect. Hover any header for what it means.</div>
  <div class="tablewrap"><table id="tbl"><thead><tr id="thead"></tr></thead><tbody id="tbody"></tbody></table></div>
</div>

<div class="card">
  <h2>📖 Glossary — every short form explained</h2>
  <dl class="gloss" id="gloss"></dl>
</div>

<footer>
  Built from <code>data/final_ranking.csv</code> and loaded from <code>site/dashboard-data.json</code>.
  Scores are <b>relative within this universe</b>, not absolute valuations.
  Smart-money signal uses delivery-trend as a free proxy for FII/DII accumulation; true quarterly FII/DII change needs Screener.in or a paid feed.
  yfinance fundamentals can be stale — always verify before acting. <b>Not investment advice.</b>
</footer>
</div>

<div id="tip"></div>
<script>
let DATA=[];
let GLOSSARY={};
let EXTRA={};
let INSIGHTS={};
let REFRESHED="";
let HIST_LABEL="";
const SPECIAL = {
  _map:"Each bubble is a stock. Far right = the model thinks it's cheaply valued. High up = a high-quality business (strong ROE, margins, low debt). The green top-right corner is the goal: cheap AND strong.",
  _table:"Every stock in the filtered universe. Click a column header to sort by it; click again to reverse. Click a row to load its factor breakdown on the right.",
  _cheapstrong:"Shows only ranked stocks in the sweet spot: valuation score ≥ 55 (cheap), quality score ≥ 55 (strong business), and at least 10% below their 52-week high.",
  _drawdown:"Set how far you expect the broader market to fall. Each stock's projected dip = beta × this %, so the IDEAL BUY price and 'To-buy %' update for everything. 0% = buy at today's price; −15% = a typical correction; −30%+ = a crash scenario.",
  _ladder:"Price ladder for the selected stock: from 52-week LOW (left) to 52-week HIGH (right). Markers show today's price (CMP), the 50- & 200-day averages (support), and the shaded BUY ZONE — the band you'd accumulate in if the market falls by your chosen drawdown."
};
const FACTORS=[["g_quality","Quality","#34d399"],["g_smart_money","Smart money","#7c9cff"],
  ["g_valuation","Valuation","#4fd1c5"],["g_growth","Growth","#fbbf24"],
  ["g_price_setup","Price setup","#f472b6"],["g_analyst","Analyst","#a78bfa"],
  ["g_momentum","Momentum","#fb923c"]];
let SECTORS=[];
const PALETTE=["#4fd1c5","#7c9cff","#fbbf24","#f472b6","#34d399","#fb923c","#a78bfa","#f87171",
  "#60a5fa","#facc15","#2dd4bf","#c084fc"];
const SCOLOR={};

// ---- columns shown in the table ----
const COLS=[["rank","#"],["rank_chg","Δ Week"],["rank_vs_staged","Δ Stage"],["rank_vs_pushed","Δ Push"],["ticker","Ticker"],["shortName","Name"],["sector","Sector"],
  ["final_score","Score"],["data_cov","Data %"],["mcap_cr","M-Cap ₹Cr"],["currentPrice","CMP ₹"],
  ["price_chg_pct","Wk %"],
  ["buy_target","Buy ≤ ₹"],["disc_pct","To-buy %"],
  ["pct_below_52w_high","% off high"],["trailingPE","P/E"],["roe_pct","ROE %"],
  ["promoter_pct","Promoter %"],["institutional_pct","Instn %"],["delivery_trend","Deliv. trend"],
  ["upside_pct","Upside %"],["recommendationMean","Reco"],["g_quality","Qual"],
  ["g_smart_money","Smart$"],["g_valuation","Val"],["g_growth","Grow"]];

// ---- drawdown / buy-zone model ----
function betaOf(d){let b=d.beta;if(b==null||isNaN(b))b=1.0;return Math.max(0.3,Math.min(2.5,b));}
function buyInfo(d){
  const draw=state.draw/100, b=betaOf(d), cmp=d.currentPrice;
  if(cmp==null)return{deep:null,shallow:null,disc:null,b};
  const deep=cmp*(1-b*draw);
  const shallow=cmp*(1-0.5*b*draw);
  return{deep,shallow,disc:b*draw*100,b};
}

const $=s=>document.querySelector(s);
const fmt=(v,d=1)=>v==null||v===""?"<span class=dim>—</span>":(typeof v==="number"?v.toLocaleString("en-IN",{maximumFractionDigits:d}):v);
const scoreColor=v=>{
  if(v==null)return "#3a4661";
  const t=Math.max(0,Math.min(100,v))/100;
  const r=t<.5?248:Math.round(248-(248-52)*(t-.5)*2);
  const g=t<.5?Math.round(113+(191-113)*t*2):Math.round(191+(211-191)*(t-.5)*2);
  const b=t<.5?Math.round(113-(36)*t*2):Math.round(77+(153-77)*(t-.5)*2);
  return `rgb(${r},${g},${b})`;
};

// ---- tooltip engine ----
const tip=$("#tip");
function showTip(html,x,y){tip.innerHTML=html;tip.style.display="block";tip.style.opacity=1;
  const w=tip.offsetWidth,h=tip.offsetHeight;let nx=x+14,ny=y+14;
  if(nx+w>innerWidth-8)nx=x-w-14; if(ny+h>innerHeight-8)ny=y-h-14;
  tip.style.left=nx+"px";tip.style.top=ny+"px";}
function hideTip(){tip.style.opacity=0;tip.style.display="none";}
function tipFor(key){const t=GLOSSARY[key]||SPECIAL[key];return t?`<b>${labelOf(key)}</b><br>${t}`:null;}
function labelOf(k){const c=COLS.find(c=>c[0]===k);if(c)return c[1];
  return {_map:"The Map",_table:"All Stocks table",_cheapstrong:"Cheap & Strong filter",
    final_score:"Final Score",sector:"Sector Scan"}[k]||k;}
document.addEventListener("mousemove",e=>{const el=e.target.closest("[data-k]");
  if(el){const h=tipFor(el.dataset.k);if(h){showTip(h,e.clientX,e.clientY);return;}}hideTip();});

// ---- state ----
let state={q:"",sector:"",ms:0,mc:0,cs:false,dc:0,draw:15,sort:"final_score",dir:-1,sel:null,ptab:"accum"};
function filtered(){
  return DATA.filter(d=>{
    if(state.q){const s=(d.ticker+" "+d.shortName).toLowerCase();if(!s.includes(state.q))return false;}
    if(state.sector&&d.sector!==state.sector)return false;
    if(d.final_score<state.ms)return false;
    if((d.mcap_cr||0)<state.mc)return false;
    if(state.dc&&(d.data_cov==null||d.data_cov<state.dc))return false;
    if(state.cs&&!(d.final_score!=null&&d.g_valuation>=55&&d.g_quality>=55&&d.pct_below_52w_high>=10))return false;
    return true;
  });
}
function sorted(arr){const k=state.sort,dir=state.dir;
  return [...arr].sort((a,b)=>{let x=a[k],y=b[k];
    if(typeof x==="string"||typeof y==="string"){x=(x||"").toString();y=(y||"").toString();
      return dir*x.localeCompare(y);}
    x=x==null?-1e9:x;y=y==null?-1e9:y;return dir*(x-y);});}

// ---- charts: SCATTER ----
function drawScatter(rows){
  const W=720,H=440,m={l:54,r:18,t:18,b:46};
  const iw=W-m.l-m.r, ih=H-m.t-m.b;
  const xv=d=>d.g_valuation, yv=d=>d.g_quality;
  const X=v=>m.l+(v/100)*iw, Y=v=>m.t+ih-(v/100)*ih;
  const maxM=Math.max(...rows.map(d=>d.mcap_cr||0),1);
  const R=d=>4+10*Math.sqrt((d.mcap_cr||0)/maxM);
  const offSect=window._offSect||new Set();
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  s+=`<rect class="quad" x="${X(55)}" y="${m.t}" width="${X(100)-X(55)}" height="${Y(55)-m.t}"></rect>`;
  s+=`<text class="quadlabel" x="${X(99)}" y="${m.t+16}" text-anchor="end">CHEAP &amp; STRONG ★</text>`;
  for(let g=0;g<=100;g+=25){
    s+=`<line class="axis" x1="${X(g)}" y1="${m.t}" x2="${X(g)}" y2="${m.t+ih}" opacity=".25"></line>`;
    s+=`<line class="axis" x1="${m.l}" y1="${Y(g)}" x2="${m.l+iw}" y2="${Y(g)}" opacity=".25"></line>`;
    s+=`<text class="tick" x="${X(g)}" y="${m.t+ih+16}" text-anchor="middle">${g}</text>`;
    s+=`<text class="tick" x="${m.l-8}" y="${Y(g)+4}" text-anchor="end">${g}</text>`;}
  s+=`<text class="axlabel" x="${m.l+iw/2}" y="${H-6}" text-anchor="middle">← expensive   ·   VALUATION score (cheapness)   ·   cheap →</text>`;
  s+=`<text class="axlabel" transform="translate(14,${m.t+ih/2}) rotate(-90)" text-anchor="middle">QUALITY score (business strength) →</text>`;
  rows.forEach(d=>{ if(d.final_score==null||d.g_valuation==null||d.g_quality==null)return; if(offSect.has(d.sector))return;
    const sel=state.sel===d.ticker;
    s+=`<circle class="bub" data-tk="${d.ticker}" cx="${X(xv(d)).toFixed(1)}" cy="${Y(yv(d)).toFixed(1)}" r="${R(d).toFixed(1)}" fill="${SCOLOR[d.sector]||'#888'}" fill-opacity="${sel?.95:.62}" stroke="${sel?'#fff':'#0c1018'}" stroke-width="${sel?2:.6}"></circle>`;});
  s+=`</svg>`;
  $("#scatter").innerHTML=s;
  $("#scatter").querySelectorAll(".bub").forEach(c=>{
    c.onclick=()=>select(c.dataset.tk);
    c.onmouseenter=e=>{const d=byTk(c.dataset.tk);showTip(bubTip(d),e.clientX,e.clientY);};
    c.onmousemove=e=>showTip(bubTip(byTk(c.dataset.tk)),e.clientX,e.clientY);
    c.onmouseleave=hideTip;});
}
function bubTip(d){const bi=buyInfo(d);
  const buy=bi.deep!=null&&state.draw>0?`<br>Ideal buy (−${state.draw}%): <b style="color:var(--accent)">₹${fmt(bi.deep,0)}</b> <span style="color:#93a1b8">(₹${fmt(d.currentPrice,0)} now)</span>`:"";
  const pats=[];
  if(d.pat_accum)pats.push(`<span class="badge badge-accum">Accumulation</span>`);
  if(d.pat_near_low)pats.push(`<span class="badge badge-near">Near 52w Low</span>`);
  if(d.pat_trap)pats.push(`<span class="badge badge-trap">Value trap?</span>`);
  return `<b>${d.ticker}</b> · ${d.shortName}<br>${d.sector}<br>
  Score <b>${fmt(d.final_score)}</b> · Valuation <b>${fmt(d.g_valuation)}</b> · Quality <b>${fmt(d.g_quality)}</b><br>
  ₹${fmt(d.mcap_cr,0)} Cr · ${fmt(d.pct_below_52w_high)}% off high · P/E ${fmt(d.trailingPE)} · ROE ${fmt(d.roe_pct)}%${buy}${pats.length?'<br>'+pats.join(''):''}`;
}

function drawLegend(){
  const off=window._offSect||(window._offSect=new Set());
  $("#legend").innerHTML=SECTORS.map(s=>
    `<span class="lg ${off.has(s)?'off':''}" data-s="${s}"><span class="dot" style="background:${SCOLOR[s]}"></span>${s}</span>`).join("");
  $("#legend").querySelectorAll(".lg").forEach(el=>el.onclick=()=>{
    const s=el.dataset.s; off.has(s)?off.delete(s):off.add(s); render();});
}

// ---- TOP BARS ----
function drawTop(rows){
  const top=sorted(rows.filter(d=>d.final_score!=null)).slice(0,20);
  if(!top.length){$("#topbars").innerHTML='<div class=empty>No stocks match.</div>';return;}
  const W=720,rowH=24,m={l:120,r:60,t:6,b:6},H=m.t+m.b+top.length*rowH;
  const max=Math.max(...top.map(d=>d.final_score),1),iw=W-m.l-m.r;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  top.forEach((d,i)=>{const y=m.t+i*rowH,w=(d.final_score/max)*iw,c=scoreColor(d.final_score);
    s+=`<text class="tick" x="${m.l-8}" y="${y+15}" text-anchor="end" fill="#e8edf6">${d.ticker}</text>`;
    s+=`<rect class="bar" data-tk="${d.ticker}" x="${m.l}" y="${y+4}" width="${w.toFixed(1)}" height="${rowH-8}" rx="4" fill="${c}"></rect>`;
    s+=`<text class="tick" x="${m.l+w+6}" y="${y+15}" fill="#e8edf6">${fmt(d.final_score)}</text>`;});
  s+=`</svg>`;$("#topbars").innerHTML=s;
  $("#topbars").querySelectorAll(".bar").forEach(b=>{b.onclick=()=>select(b.dataset.tk);
    b.onmouseenter=e=>showTip(bubTip(byTk(b.dataset.tk)),e.clientX,e.clientY);
    b.onmousemove=e=>showTip(bubTip(byTk(b.dataset.tk)),e.clientX,e.clientY);
    b.onmouseleave=hideTip;});
}

// ---- SECTOR BARS ----
function drawSectors(rows){
  const map={};rows.forEach(d=>{if(!d.sector||d.final_score==null)return;(map[d.sector]=map[d.sector]||[]).push(d.final_score);});
  let arr=Object.entries(map).map(([s,v])=>({s,avg:v.reduce((a,b)=>a+b,0)/v.length,n:v.length}))
    .sort((a,b)=>b.avg-a.avg);
  if(!arr.length){$("#sectorbars").innerHTML='<div class=empty>No stocks match.</div>';return;}
  const W=720,rowH=26,m={l:140,r:60,t:6,b:6},H=m.t+m.b+arr.length*rowH;
  const max=Math.max(...arr.map(a=>a.avg),1),iw=W-m.l-m.r;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  arr.forEach((a,i)=>{const y=m.t+i*rowH,w=(a.avg/max)*iw;
    s+=`<text class="tick" x="${m.l-8}" y="${y+16}" text-anchor="end" fill="#e8edf6">${a.s}</text>`;
    s+=`<rect class="bar" data-s="${a.s}" x="${m.l}" y="${y+4}" width="${w.toFixed(1)}" height="${rowH-9}" rx="4" fill="${SCOLOR[a.s]}"></rect>`;
    s+=`<text class="tick" x="${m.l+w+6}" y="${y+16}" fill="#e8edf6">${a.avg.toFixed(1)} <tspan class="dim">(${a.n})</tspan></text>`;});
  s+=`</svg>`;$("#sectorbars").innerHTML=s;
  $("#sectorbars").querySelectorAll(".bar").forEach(b=>{b.onclick=()=>{state.sector=b.dataset.s;$("#sector").value=b.dataset.s;render();};});
}

// ---- PATTERNS panel ----
let _ptab="accum";
function switchPTab(t){
  _ptab=t;
  ["accum","near","trap","movers","dist","sector"].forEach(id=>{
    const el=document.getElementById("ptab-"+id);
    if(el)el.style.background=id===t?"var(--accent2)22":"";
    if(el)el.style.color=id===t?"var(--accent2)":"var(--muted)";
  });
  drawPatternContent(t);
}

function insMinTable(stocks,cols){
  // cols = [{key, label, fmt}]
  if(!stocks||!stocks.length)return '<div class="empty">None found.</div>';
  let h=`<table class="ins-tbl"><thead><tr>`;
  cols.forEach(c=>h+=`<th>${c.label}</th>`);
  h+=`</tr></thead><tbody>`;
  stocks.forEach(d=>{
    h+=`<tr data-tk="${d.ticker}">`;
    cols.forEach(c=>{
      let v=d[c.key];
      if(c.key==="ticker")h+=`<td class="tk">${v||""}</td>`;
      else if(c.key==="final_score")h+=`<td class="sc"><span class="score-pill" style="background:${scoreColor(v)}">${v!=null?v.toFixed(1):"—"}</span></td>`;
      else if(c.key==="pat_accum"||c.key==="pat_near_low"||c.key==="pat_trap")h+=`<td>${v?'✓':''}</td>`;
      else h+=`<td class="dim">${v!=null?(typeof v==="number"?v.toFixed(1):v):"—"}</td>`;
    });
    h+=`</tr>`;
  });
  h+=`</tbody></table>`;
  return h;
}

function drawScoreDist(){
  const dist=INSIGHTS.dist;
  if(!dist||!dist.length)return '<div class="empty">No data.</div>';
  const W=640,H=200,m={l:50,r:20,t:20,b:40};
  const iw=W-m.l-m.r,ih=H-m.t-m.b;
  const max=Math.max(...dist.map(d=>d.count),1);
  const bw=iw/dist.length;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  // grid lines
  for(let g=0;g<=max;g+=Math.ceil(max/4)){
    const y=m.t+ih-(g/max)*ih;
    s+=`<line class="axis" x1="${m.l}" y1="${y}" x2="${m.l+iw}" y2="${y}" opacity=".2"></line>`;
    s+=`<text class="tick" x="${m.l-6}" y="${y+4}" text-anchor="end">${g}</text>`;
  }
  // baseline
  s+=`<line class="axis" x1="${m.l}" y1="${m.t+ih}" x2="${m.l+iw}" y2="${m.t+ih}" opacity=".5"></line>`;
  dist.forEach((d,i)=>{
    const x=m.l+i*bw+2, bh=(d.count/max)*ih, y=m.t+ih-bh;
    const isTop=i>=4; // score 55+ = top tier
    s+=`<rect class="hist-bar ${isTop?'top':''}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw-4).toFixed(1)}" height="${bh.toFixed(1)}" rx="3"></rect>`;
    s+=`<text class="tick" x="${(x+bw/2-2).toFixed(1)}" y="${(y-5).toFixed(1)}" text-anchor="middle">${d.count}</text>`;
    s+=`<text class="tick" x="${(x+bw/2-2).toFixed(1)}" y="${(m.t+ih+16).toFixed(1)}" text-anchor="middle">${d.label}</text>`;
  });
  s+=`</svg>`;
  const total=dist.reduce((a,b)=>a+b.count,0);
  const top15=dist.filter((_,i)=>i>=4).reduce((a,b)=>a+b.count,0);
  return s+`<div class="note">Score buckets across all ${total.toLocaleString("en-IN")} stocks. <b style="color:var(--good)">${top15}</b> (${Math.round(top15/total*100)}%) score 55+ — the top tier where cheap AND strong align. Most stocks cluster in 45–55 (average quality, average price).</div>`;
}

function drawSectorConc(){
  const sc=INSIGHTS.sector_conc;
  if(!sc||!sc.length)return '<div class="empty">No data.</div>';
  const W=640,rowH=28,m={l:160,r:80,t:8,b:8},H=m.t+m.b+sc.length*rowH;
  const maxC=Math.max(...sc.map(x=>x.cs_count),1),iw=W-m.l-m.r;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  sc.forEach((a,i)=>{
    const y=m.t+i*rowH, w=(a.cs_count/maxC)*iw;
    const col=SCOLOR[a.sector]||"#888";
    s+=`<text class="tick" x="${m.l-8}" y="${y+16}" text-anchor="end" fill="#e8edf6">${a.sector}</text>`;
    s+=`<rect x="${m.l}" y="${y+4}" width="${w.toFixed(1)}" height="${rowH-10}" rx="3" fill="${col}" opacity=".8"></rect>`;
    s+=`<text class="tick" x="${m.l+w+6}" y="${y+16}" fill="#e8edf6">${a.cs_count} <tspan class="dim">/ ${a.total} (${a.pct}%)</tspan></text>`;
  });
  s+=`</svg>`;
  return s+`<div class="note">Number of Cheap &amp; Strong stocks (val≥55, quality≥55, ≥10% off 52w high) per sector, as count and % of that sector's total stocks.</div>`;
}

function drawSectorWeekly(){
  const sw=INSIGHTS.sector_weekly;
  if(!sw||!sw.length)return '';
  let h=`<div style="margin-top:18px"><div class="sub">Weekly price change by sector (avg %) — this run vs previous</div><table class="ins-tbl" style="max-width:480px"><thead><tr><th>Sector</th><th>Avg change</th><th>Stocks</th></tr></thead><tbody>`;
  sw.forEach(a=>{
    const c=a.avg_chg>0?"var(--good)":a.avg_chg<-2?"var(--bad)":"var(--warn)";
    const ar=a.avg_chg>0?"▲":"▼";
    h+=`<tr><td>${a.sector}</td><td style="color:${c};font-weight:700">${ar} ${Math.abs(a.avg_chg)}%</td><td class="dim">${a.count}</td></tr>`;
  });
  h+=`</tbody></table></div>`;
  return h;
}

function drawPatternContent(t){
  const el=document.getElementById("ptab-content");
  if(!el)return;
  if(t==="accum"){
    const n=INSIGHTS.accum.length;
    el.innerHTML=`<div class="sub" style="margin-bottom:8px"><b style="color:var(--good)">${n} stocks</b> where delivery trend is rising (+5 or more) AND institutional holding ≥30% AND score ≥55 — organised money building positions into weakness.</div>`
      +insMinTable(INSIGHTS.accum,[
        {key:"ticker",label:"Ticker"},{key:"shortName",label:"Name"},{key:"sector",label:"Sector"},
        {key:"final_score",label:"Score"},{key:"delivery_trend",label:"Deliv. trend"},
        {key:"institutional_pct",label:"Instn %"},{key:"pct_below_52w_high",label:"% off high"},
        {key:"trailingPE",label:"P/E"},{key:"roe_pct",label:"ROE %"}]);
  } else if(t==="near"){
    const n=INSIGHTS.near_low.length;
    el.innerHTML=`<div class="sub" style="margin-bottom:8px"><b style="color:var(--accent)">${n} stocks</b> with quality score ≥60, trading within 20% of their 52-week low — high-quality businesses that are genuinely beaten down. These offer the most price-driven margin of safety.</div>`
      +insMinTable(INSIGHTS.near_low,[
        {key:"ticker",label:"Ticker"},{key:"shortName",label:"Name"},{key:"sector",label:"Sector"},
        {key:"final_score",label:"Score"},{key:"g_quality",label:"Quality"},
        {key:"pct_above_52w_low",label:"% above 52w low"},{key:"currentPrice",label:"CMP ₹"},
        {key:"trailingPE",label:"P/E"},{key:"roe_pct",label:"ROE %"}]);
  } else if(t==="trap"){
    const n=INSIGHTS.traps.length;
    el.innerHTML=`<div class="sub" style="margin-bottom:8px"><b style="color:var(--bad)">${n} value traps detected</b>: >30% below 52w high, quality score <40, AND delivery trend falling. Price is cheap for a reason — avoid until thesis improves.</div>`
      +insMinTable(INSIGHTS.traps,[
        {key:"ticker",label:"Ticker"},{key:"shortName",label:"Name"},{key:"sector",label:"Sector"},
        {key:"final_score",label:"Score"},{key:"pct_below_52w_high",label:"% off high"},
        {key:"g_quality",label:"Quality"},{key:"delivery_trend",label:"Deliv. trend"},
        {key:"trailingPE",label:"P/E"}]);
  } else if(t==="movers"){
    const g=INSIGHTS.gainers||[], f=INSIGHTS.fallers||[];
    const hasData=g.length||f.length;
    if(!hasData){el.innerHTML='<div class="empty">No prior-week prices available for comparison.</div>';return;}
    let h=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;flex-wrap:wrap">`;
    // Gainers
    h+=`<div><div class="sub" style="margin-bottom:6px"><b style="color:var(--good)">▲ Top gainers</b> this week</div>`;
    h+=`<table class="ins-tbl"><thead><tr><th>Ticker</th><th>Name</th><th>+%</th><th>Score</th><th>Sector</th></tr></thead><tbody>`;
    g.forEach(d=>{h+=`<tr data-tk="${d.ticker}"><td class="tk">${d.ticker}</td><td class="dim">${(d.shortName||'').slice(0,20)}</td><td style="color:var(--good);font-weight:700">+${(d.price_chg_pct||0).toFixed(1)}%</td><td><span class="score-pill" style="background:${scoreColor(d.final_score)}">${(d.final_score||0).toFixed(1)}</span></td><td class="dim">${d.sector||''}</td></tr>`;});
    h+=`</tbody></table></div>`;
    // Fallers
    h+=`<div><div class="sub" style="margin-bottom:6px"><b style="color:var(--bad)">▼ Top fallers</b> this week</div>`;
    h+=`<table class="ins-tbl"><thead><tr><th>Ticker</th><th>Name</th><th>−%</th><th>Score</th><th>Sector</th></tr></thead><tbody>`;
    f.forEach(d=>{h+=`<tr data-tk="${d.ticker}"><td class="tk">${d.ticker}</td><td class="dim">${(d.shortName||'').slice(0,20)}</td><td style="color:var(--bad);font-weight:700">${(d.price_chg_pct||0).toFixed(1)}%</td><td><span class="score-pill" style="background:${scoreColor(d.final_score)}">${(d.final_score||0).toFixed(1)}</span></td><td class="dim">${d.sector||''}</td></tr>`;});
    h+=`</tbody></table></div>`;
    h+=`</div>`;
    el.innerHTML=h;
    el.querySelectorAll("tr[data-tk]").forEach(tr=>tr.onclick=()=>select(tr.dataset.tk));
  } else if(t==="dist"){
    el.innerHTML=drawScoreDist();
  } else if(t==="sector"){
    // Sector heat: two charts side by side
    el.innerHTML=drawSectorConc()+drawSectorWeekly();
  }
  // wire up row clicks
  if(el.querySelectorAll){
    el.querySelectorAll("tr[data-tk]").forEach(tr=>tr.onclick=()=>select(tr.dataset.tk));
  }
}

// ---- TABLE ----
function drawTable(rows){
  const th=COLS.map(([k,l])=>{const ar=state.sort===k?(state.dir<0?'▼':'▲'):'';
    return `<th data-sort="${k}" data-k="${k}">${l} <span class="ar">${ar}</span></th>`;}).join("");
  $("#thead").innerHTML=th;
  const rs=sorted(rows);
  if(!rs.length){$("#tbody").innerHTML=`<tr><td colspan="${COLS.length}" class="empty">No stocks match your filters.</td></tr>`;}
  else $("#tbody").innerHTML=rs.map(d=>{
    const sel=state.sel===d.ticker?' class="sel"':'';
    return `<tr${sel} data-tk="${d.ticker}">`+COLS.map(([k])=>{
      if(k==="final_score")return `<td><span class="score-pill" style="background:${scoreColor(d[k])}">${fmt(d[k])}</span></td>`;
      if(k==="shortName")return `<td>${d[k]||""}</td>`;
      if(k==="ticker"||k==="sector")return `<td>${d[k]||""}</td>`;
      if(k==="rank")return `<td class="dim">${d[k]==null?'—':d[k]}</td>`;
      if(["rank_chg","rank_vs_staged","rank_vs_pushed"].includes(k)){
        const v=d.rank_chg;
        if(v==null)return `<td class="dim">—</td>`;
        if(v>0)return `<td class="up">▲${v}</td>`;
        if(v<0)return `<td class="dn">▼${Math.abs(v)}</td>`;
        return `<td class="dim">—</td>`;
      }
      if(k==="price_chg_pct"){const v=d.price_chg_pct;if(v==null)return `<td class="dim">—</td>`;const c=v>0?"var(--good)":v<0?"var(--bad)":"var(--muted)";return `<td style="color:${c};font-weight:${Math.abs(v)>5?700:500}">${v>0?'+':''}${v.toFixed(1)}%</td>`;}
      if(k==="buy_target")return `<td style="color:var(--accent);font-weight:600">${d[k]==null?'<span class=dim>—</span>':'₹'+fmt(d[k],0)}</td>`;
      if(k==="disc_pct"){const v=d[k];const c=v==null?'':v<=2?'color:var(--good);font-weight:700':v<=8?'color:var(--warn)':'color:var(--muted)';
        return `<td style="${c}">${v==null?'<span class=dim>—</span>':fmt(v,1)+'%'}</td>`;}
      if(k==="data_cov"){const v=d[k];const c=v==null?'#3a4661':v>=75?'var(--good)':v>=60?'var(--warn)':'var(--bad)';
        return `<td style="color:${c};font-weight:600">${v==null?'<span class=dim>—</span>':v+'%'}</td>`;}
      let d2=(k==="mcap_cr"||k==="currentPrice")?0:1;
      return `<td>${fmt(d[k],d2)}</td>`;}).join("")+`</tr>`;}).join("");
  $("#thead").querySelectorAll("th").forEach(h=>h.onclick=()=>{
    const k=h.dataset.sort; if(state.sort===k)state.dir*=-1; else{state.sort=k;state.dir=(k==="ticker"||k==="shortName"||k==="sector")?1:-1;}
    render();});
  $("#tbody").querySelectorAll("tr[data-tk]").forEach(tr=>tr.onclick=()=>select(tr.dataset.tk));
}

// ---- DETAIL PANEL ----
function byTk(tk){return DATA.find(d=>d.ticker===tk);}
function select(tk){state.sel=state.sel===tk?null:tk;render();
  if(state.sel)document.querySelector("tr.sel")?.scrollIntoView({block:"nearest"});}

// price ladder
function priceLadder(d){
  const cmp=d.currentPrice; if(cmp==null)return "";
  const bi=buyInfo(d);
  let hi=d.fiftyTwoWeekHigh; if(hi==null&&d.pct_below_52w_high!=null)hi=cmp/(1-d.pct_below_52w_high/100);
  const lo=d.fiftyTwoWeekLow;
  const vals=[hi,cmp,lo,d.twoHundredDayAverage,d.fiftyDayAverage,bi.deep,bi.shallow].filter(v=>v!=null);
  if(vals.length<2)return "";
  let mn=Math.min(...vals),mx=Math.max(...vals);const pad=(mx-mn)*0.07||1;mn-=pad;mx+=pad;
  const W=680,m={l:18,r:18,t:46,b:34},iw=W-m.l-m.r,axisY=m.t,H=m.t+m.b+8;
  const X=v=>m.l+(v-mn)/(mx-mn)*iw;
  let s=`<svg viewBox="0 0 ${W} ${H+24}">`;
  if(bi.deep!=null&&state.draw>0){const x0=X(bi.deep),x1=X(bi.shallow);
    s+=`<rect x="${x0.toFixed(1)}" y="${axisY-14}" width="${(x1-x0).toFixed(1)}" height="28" fill="#34d39926" stroke="#34d399" stroke-dasharray="3 3" rx="3"></rect>`;
    s+=`<text x="${((x0+x1)/2).toFixed(1)}" y="${axisY-20}" text-anchor="middle" fill="#34d399" font-size="11" font-weight="700">BUY ZONE</text>`;
    s+=`<text x="${x0.toFixed(1)}" y="${axisY+30}" text-anchor="middle" fill="#34d399" font-size="11" font-weight="700">₹${fmt(bi.deep,0)}</text>`;}
  s+=`<line class="axis" x1="${m.l}" y1="${axisY}" x2="${m.l+iw}" y2="${axisY}" opacity=".5"></line>`;
  const marks=[{v:lo,n:"52w Low",c:"#93a1b8"},{v:d.twoHundredDayAverage,n:"200-DMA",c:"#7c9cff"},
    {v:d.fiftyDayAverage,n:"50-DMA",c:"#a78bfa"},{v:cmp,n:"CMP",c:"#fbbf24",big:1},
    {v:hi,n:"52w High",c:"#93a1b8"}].filter(x=>x.v!=null).sort((a,b)=>a.v-b.v);
  marks.forEach((mk,i)=>{const x=X(mk.v),up=i%2===0;const ny=up?axisY-22:axisY-34;
    s+=`<line x1="${x.toFixed(1)}" y1="${axisY-7}" x2="${x.toFixed(1)}" y2="${axisY+7}" stroke="${mk.c}" stroke-width="${mk.big?3:1.5}"></line>`;
    s+=`<circle cx="${x.toFixed(1)}" cy="${axisY}" r="${mk.big?4:3}" fill="${mk.c}"></circle>`;
    s+=`<text x="${x.toFixed(1)}" y="${ny}" text-anchor="middle" fill="${mk.c}" font-size="10.5" font-weight="${mk.big?700:500}">${mk.n}</text>`;
    s+=`<text x="${x.toFixed(1)}" y="${axisY+(up?42:42)}" text-anchor="middle" fill="${mk.big?'#fbbf24':'#93a1b8'}" font-size="10.5">₹${fmt(mk.v,0)}</text>`;});
  s+=`</svg>`;
  const note=state.draw>0
    ? `Buy ≤ <b style="color:var(--accent)">₹${fmt(bi.deep,0)}</b> on a −${state.draw}% market drawdown (β ${fmt(bi.b,2)}) — that's <b>${fmt(bi.disc,1)}%</b> below CMP.`
    : `Set a market-drawdown scenario above to project an ideal buy price.`;
  return `<div data-k="_ladder" style="margin-top:12px;font-size:12px;color:var(--muted);cursor:help">📉 Price ladder &amp; buy zone <span class="help">i</span></div>${s}<div class="note">${note}</div>`;
}

function gitComparison(d,label,rankKey,scoreKey,movementKey,oldRankKey,oldScoreKey,newRankKey="current_rank",newScoreKey="current_score"){
  const rv=d[rankKey], sv=d[scoreKey], movement=d[movementKey]||"unavailable";
  let rankText, rankClass="dim";
  if(rv>0){rankText=`▲${rv}`;rankClass="up";}
  else if(rv<0){rankText=`▼${Math.abs(rv)}`;rankClass="dn";}
  else if(rv===0){rankText="—";}
  else{rankText=movement;rankClass=movement==="newly ranked"?"up":movement==="became unranked"?"dn":"dim";}
  const scoreText=sv==null?"—":`${sv>0?"+":""}${fmt(sv,2)}`;
  const scoreClass=sv>0?"up":sv<0?"dn":"dim";
  const oldRank=d[oldRankKey]==null?"unranked":"#"+fmt(d[oldRankKey],0);
  const newRank=d[newRankKey]==null?"unranked":"#"+fmt(d[newRankKey],0);
  const oldScore=d[oldScoreKey]==null?"—":fmt(d[oldScoreKey],2);
  const newScore=d[newScoreKey]==null?"—":fmt(d[newScoreKey],2);
  return `<div data-k="${rankKey}" style="cursor:help"><b>${label}</b>: rank <span class="${rankClass}">${rankText}</span> · score <span class="${scoreClass}">${scoreText}</span> <span class="dim">(${oldRank} → ${newRank}; ${oldScore} → ${newScore})</span></div>`;
}

function gitTrackerHtml(d){
  if(!d.movement_vs_staged&&!d.movement_vs_pushed)return "";
  return `<div style="margin-top:8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:12px;line-height:1.7">
    <div style="color:var(--muted);font-weight:700">Git rank tracker · positive rank movement means moved up</div>
    ${gitComparison(d,"vs staged","rank_vs_staged","score_vs_staged","movement_vs_staged","staged_rank","staged_score")}
    ${gitComparison(d,"staged vs pushed","staged_rank_vs_pushed","staged_score_vs_pushed","staged_movement_vs_pushed","pushed_rank","pushed_score","staged_rank","staged_score")}
    ${gitComparison(d,"vs pushed","rank_vs_pushed","score_vs_pushed","movement_vs_pushed","pushed_rank","pushed_score")}
  </div>`;
}

function drawDetail(){
  const d=state.sel?byTk(state.sel):null;
  if(!d){$("#detailbody").innerHTML='No stock selected.';$("#detailbody").className="empty";return;}
  $("#detailbody").className="";
  // pattern badges
  const pats=[];
  if(d.pat_accum)pats.push(`<span class="badge badge-accum" data-k="_accumulation" title="Delivery trend rising + high institutional holding">🟢 Accumulation signal</span>`);
  if(d.pat_near_low)pats.push(`<span class="badge badge-near" data-k="_nearlow" title="High quality stock near 52w low — contrarian entry point">🎯 Near 52w low</span>`);
  if(d.pat_trap)pats.push(`<span class="badge badge-trap" data-k="_trap" title="Deep discount + poor quality + falling delivery — possible value trap">⚠️ Value trap risk</span>`);
  // rank change
  const rchg=d.rank_chg;
  const rchgHtml=rchg==null?'':(rchg>0?`<span class="up" style="font-size:12px">▲${rchg} vs prev week</span>`:(rchg<0?`<span class="dn" style="font-size:12px">▼${Math.abs(rchg)} vs prev week</span>`:''));
  const facs=FACTORS.map(([k,nm,c])=>{const v=d[k]==null?0:d[k];
    return `<div class="fac"><span class="nm" data-k="${k}">${nm}</span>
      <span class="track"><span class="fill" style="width:${v}%;background:${c}"></span></span>
      <span style="text-align:right">${fmt(d[k])}</span></div>`;}).join("");
  const rating=d.final_score==null
    ? `<span class="badge badge-trap">Unranked · insufficient data</span>`
    : `<span class="score-pill" style="background:${scoreColor(d.final_score)};font-size:15px">${fmt(d.final_score)}</span>`;
  $("#detailbody").innerHTML=`
    <div class="hd"><span class="t">${d.ticker} <span class="dim" style="font-size:13px">${d.rank==null?'Unranked':'#'+d.rank}</span> ${rchgHtml}</span>
      ${rating}</div>
    <div class="dim">${d.shortName} · ${d.sector}</div>
    ${pats.length?`<div style="margin-top:6px">${pats.join(' ')}</div>`:''}
    <div data-k="data_cov" style="cursor:help;margin-top:6px;font-size:12px">Weighted data coverage: <b style="color:${d.data_cov==null?'#93a1b8':d.data_cov>=75?'var(--good)':d.data_cov>=60?'var(--warn)':'var(--bad)'}">${d.data_cov==null?'—':d.data_cov+'%'}</b> <span class="dim">${d.final_score==null?'— insufficient evidence for a defensible score; this stock is not ranked':'— score adjusted for missing inputs'}</span> <span class="help">i</span></div>
    ${gitTrackerHtml(d)}
    <div class="kv">
      <span>M-Cap <b>₹${fmt(d.mcap_cr,0)} Cr</b></span><span>CMP <b>₹${fmt(d.currentPrice,0)}</b></span>
      <span data-k="pct_below_52w_high"><b>${fmt(d.pct_below_52w_high)}%</b> off 52w high</span>
      <span data-k="trailingPE">P/E <b>${fmt(d.trailingPE)}</b></span>
      <span data-k="roe_pct">ROE <b>${fmt(d.roe_pct)}%</b></span>
      <span data-k="promoter_pct">Promoter <b>${fmt(d.promoter_pct)}%</b></span>
      <span data-k="institutional_pct">Instn <b>${fmt(d.institutional_pct)}%</b></span>
      <span data-k="delivery_trend">Deliv. trend <b>${fmt(d.delivery_trend)}</b></span>
      <span data-k="upside_pct">Analyst upside <b>${fmt(d.upside_pct)}%</b></span>
      <span data-k="recommendationMean">Reco <b>${fmt(d.recommendationMean)}</b></span>
    </div>
    ${priceLadder(d)}
    <div style="margin-top:10px;font-size:12px;color:var(--muted)">Factor breakdown (0–100, hover names):</div>
    ${facs}`;
}

// ---- stats header ----
function drawStats(){
  const rated=DATA.filter(d=>d.final_score!=null);
  const top=[...rated].sort((a,b)=>(a.rank??Infinity)-(b.rank??Infinity))[0];
  const cs=rated.filter(d=>d.g_valuation>=55&&d.g_quality>=55&&d.pct_below_52w_high>=10).length;
  const avg=(rated.reduce((a,b)=>a+b.final_score,0)/Math.max(rated.length,1)).toFixed(1);
  const accum=DATA.filter(d=>d.pat_accum).length;
  const near=DATA.filter(d=>d.pat_near_low).length;
  const traps=DATA.filter(d=>d.pat_trap).length;
  $("#stats").innerHTML=`
    <div class="stat"><b>${rated.length.toLocaleString("en-IN")}</b><span>ranked of ${DATA.length.toLocaleString("en-IN")} stocks</span></div>
    <div class="stat"><b>${cs}</b><span data-k="_cheapstrong">in the cheap &amp; strong zone</span></div>
    <div class="stat"><b>${SECTORS.length}</b><span>sectors</span></div>
    <div class="stat"><b>${avg}</b><span>average final score</span></div>
    <div class="stat"><b>${top?top.ticker:'—'}</b><span>top pick (${top?fmt(top.final_score):'—'})</span></div>
    <div class="stat"><b style="color:var(--good)">${accum}</b><span>accumulation signals</span></div>
    <div class="stat"><b style="color:var(--accent)">${near}</b><span>near 52w low + quality</span></div>
    <div class="stat"><b style="color:var(--bad)">${traps}</b><span>value trap alerts</span></div>`;
}
function drawGloss(){
  let h="";
  COLS.forEach(([k,l])=>{if(GLOSSARY[k])h+=`<dt>${l} <span class="dim">(${k})</span></dt><dd>${GLOSSARY[k]}</dd>`;});
  ["beta","fiftyTwoWeekLow","twoHundredDayAverage","fiftyDayAverage"].forEach(k=>{
    if(GLOSSARY[k])h+=`<dt>${k}</dt><dd>${GLOSSARY[k]}</dd>`;});
  Object.entries(EXTRA).forEach(([k,v])=>h+=`<dt>${k}</dt><dd>${v}</dd>`);
  $("#gloss").innerHTML=h;
}

// ---- master render ----
function render(){
  DATA.forEach(d=>{const bi=buyInfo(d);d.buy_target=bi.deep==null?null:Math.round(bi.deep);
    d.disc_pct=bi.disc==null?null:Math.round(bi.disc*10)/10;});
  const rows=filtered();
  $("#count").textContent=`${rows.length} of ${DATA.length} shown`;
  drawScatter(rows);drawLegend();drawTop(rows);drawSectors(rows);drawTable(rows);drawDetail();
  drawPatternContent(_ptab);
}
function init(){
  // set refresh badge
  const rb=document.getElementById("refresh-badge");
  if(rb)rb.textContent="Refreshed: "+REFRESHED+(HIST_LABEL?" · vs "+HIST_LABEL:"");
  $("#sector").innerHTML='<option value="">All sectors</option>'+SECTORS.map(s=>`<option>${s}</option>`).join("");
  $("#q").oninput=e=>{state.q=e.target.value.trim().toLowerCase();render();};
  $("#sector").onchange=e=>{state.sector=e.target.value;render();};
  $("#ms").oninput=e=>{state.ms=+e.target.value;$("#msv").textContent=e.target.value;render();};
  $("#mc").oninput=e=>{state.mc=+e.target.value;$("#mcv").textContent="₹"+(+e.target.value).toLocaleString("en-IN");render();};
  $("#cheapstrong").onchange=e=>{state.cs=e.target.checked;render();};
  $("#dc").oninput=e=>{state.dc=+e.target.value;$("#dcv").textContent=e.target.value+"%";render();};
  $("#draw").oninput=e=>{state.draw=+e.target.value;$("#drawv").textContent="−"+e.target.value+"%";render();};
  // init pattern tabs
  switchPTab("accum");
  drawStats();drawGloss();render();
}
async function loadDashboardData(){
  const response=await fetch("site/dashboard-data.json",{cache:"no-store"});
  if(!response.ok)throw new Error(`HTTP ${response.status} while loading dashboard data`);
  const payload=await response.json();
  if(payload.schema_version!==1||!Array.isArray(payload.stocks)){
    throw new Error("Unsupported or malformed dashboard-data.json");
  }
  DATA=payload.stocks;
  GLOSSARY=payload.glossary||{};
  EXTRA=payload.extra_terms||{};
  INSIGHTS=payload.insights||{};
  REFRESHED=payload.refreshed||payload.generated_at||"";
  HIST_LABEL=payload.history_label||"";
  SECTORS=[...new Set(DATA.map(d=>d.sector).filter(Boolean))].sort();
  Object.keys(SCOLOR).forEach(key=>delete SCOLOR[key]);
  SECTORS.forEach((sector,index)=>SCOLOR[sector]=PALETTE[index%PALETTE.length]);
  init();
}
loadDashboardData().catch(error=>{
  console.error(error);
  const stats=document.getElementById("stats");
  if(stats)stats.innerHTML=`<div class="card"><b>Dashboard data could not be loaded.</b><br><span class="dim">${error.message}. Serve this research folder over HTTP instead of opening the HTML with file://.</span></div>`;
});
</script></body></html>"""

if __name__ == "__main__":
    main()
