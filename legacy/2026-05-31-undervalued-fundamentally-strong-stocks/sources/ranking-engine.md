# Ranking Engine — `rank_all.py` (tunable weights)

**Date:** 2026-05-31 · **Script:** `../scripts/rank_all.py` · **Output:** `../data/final_ranking.csv`

Single reproducible scorer that merges all three layers into one **0–100 final score**.
Reads CSVs only (no network) → instant; re-tune weights freely.

## How it scores
6 components, each = mean of its **min-max normalized** (0–1 across the universe) metrics,
each metric flagged higher- or lower-is-better. Final = weighted sum of components → 0–100.

| Component | Metrics (direction) | Default weight |
|---|---|---|
| `price_weakness` | % below 52w high (+, capped at 55) | 1.5 |
| `valuation` | trailingPE (−), priceToBook (−) | 2.0 |
| `quality` | ROE (+), margin (+), debt/equity (−) | 2.5 |
| `growth` | earnings growth (+), revenue growth (+) | 1.5 |
| `conviction` | avg delivery % (+), delivery trend (+) | 1.5 |
| `ownership` | promoter % (+), institutional % (+), #institutions (+) | 1.0 |

Weights are relative (normalized to sum=1 internally).

## Usage
```bash
# default
./.venv/bin/python rank_all.py --top 15

# tune weights (value investor)
./.venv/bin/python rank_all.py --weights "valuation=4,growth=0.5,price_weakness=2"

# quality/ownership investor, large-caps only (mcap in Rs cr)
./.venv/bin/python rank_all.py --weights "quality=4,ownership=3,conviction=2" --min-mcap 50000
```

## Result snapshot — FULL UNIVERSE (180 fully-signalled names, default weights)

**Top 12:** TCS (60.1) · BAJAJHLDNG (59.7) · PRESTIGE (59.5) · ITC (58.2) · INFY (57.1) · WHIRLPOOL (56.5) · LODHA (56.1) · JUBLFOOD (56.1) · VEDL (56.1) · COROMANDEL (55.8) · ESCORTS (55.4) · KAJARIACER (55.4)

Expanding from 40→180 surfaced names absent from the earlier shortlist (PRESTIGE, WHIRLPOOL, JUBLFOOD, COROMANDEL, KAJARIACER, HDFCBANK, RVNL, MARUTI, JSWSTEEL, ICICIBANK, SBIN, DLF). **Absolute scores shifted vs the 40-name run** (TCS 63.2→60.1) because min-max normalization is relative to the universe — this is expected; compare *ranks within a run*.

**Value-tilt (`valuation=4`) earlier 40-run:** BAJAJHLDNG · TCS · ITC · INFY · ESCORTS · VEDL · PNB → cheap PSUs climb; **VEDL rises despite a negative delivery trend**, illustrating that the `conviction` weight is the value-trap guard.

## Scope & limitations
- Now ranks the **full 180-stock universe** (all have delivery + promoter/institutional signals). `TOP_N_FOR_DELIVERY=999` in the ownership layer covers everything.
- Inherits upstream caveats: shareholding is a **snapshot (no QoQ / no FII-DII split)**; FPI regime stale; yfinance fundamentals need Screener.in cross-check for finalists.
- Min-max normalization is **relative to the current universe** — scores shift as the universe changes; compare ranks, not absolute scores across different runs.

## Full pipeline
```
1. screen_indian_stocks.py  -> data/screen_results.csv      (fundamentals + price)
2. ownership_layer.py       -> data/ownership_overlay.csv   (delivery + deals + FPI)
3. shareholding_layer.py    -> data/shareholding_layer.csv  (promoter/institutional)
4. rank_all.py [--weights]  -> data/final_ranking.csv       (weighted 0-100 score)
```
