# Ownership Layer — "Who Is Investing" Signal

**Date:** 2026-05-31 · **Source:** nselib → NSE/NSDL (free)
**Script:** `../scripts/ownership_layer.py` · **Data:** `../data/ownership_overlay.csv`, `nsdl_fpi_latest.csv`, `bulk_block_deals.csv`

## What this layer adds
On top of the fundamental screen, it answers *is smart money accumulating these dips?* via three free signals:

| Signal | What it tells us | Freshness | Verdict |
|---|---|---|---|
| **Delivery %** (per stock) | Conviction — high & rising delivery = real buyers taking delivery, not intraday churn | ✅ Fresh (to 29-May-2026) | **Primary working signal** |
| **Bulk/Block deals** (per stock, by client name) | Which named funds/institutions bought/sold | ✅ Fresh (Apr–May 2026) | ⚠️ Only fires for small/midcaps — **empty for our large-caps** |
| **NSDL FPI flows** (market-level) | FII/MF risk-on vs risk-off regime | ❌ Stale (nselib archive lags → latest = Oct-2025) | Context only, unreliable via this lib |

## Top combined picks (fundamentals + ownership)

Ranked by `combined_score` = fundamental score + ownership score (delivery conviction).

| Ticker | Sector | % below 52w high | PE | ROE% | Avg delivery% | Delivery trend | Combined | Read |
|---|---|---|---|---|---|---|---|---|
| **TCS** | IT | 36% | 17 | 48 | 52 | **+7.8** | 15 | Quality IT, down-cycle discount, being accumulated |
| **HEROMOTOCO** | Auto | 23% | 17 | 28 | 46 | **+7.0** | 15 | 2-wheeler leader, accumulation |
| **ESCORTS** | Industrials | 32% | 23 | 12 | 53 | **+9.5** | 15 | Tractors/capex, strong delivery uptick |
| **COALINDIA** | Energy | 7% | 9 | 28 | 45 | +5.6 | 14 | Cheap PSU, high yield, accumulation |
| **ITC** | FMCG | 33% | 17 | 29 | **59** | **+10.8** | 13 | High & rising delivery = strong conviction |
| **HINDUNILVR** | FMCG | 22% | 48 | 22 | 56 | +6.6 | 14 | Defensive blue-chip on sale, accumulation |
| **TATAELXSI** | IT | 36% | 43 | 21 | 36 | **+9.9** | 14 | Big drawdown + delivery turning up |
| **BEL** | Defence | 13% | 49 | 28 | 49 | **+11.8** | 13 | Defence, strong accumulation |
| **VGUARD** | Industrials | 25% | 44 | 14 | 53 | **+14.4** | 13 | Highest delivery uptick in set |
| **LUPIN** | Pharma | 9% | 19 | 27 | 54 | +6.4 | 13 | Pharma, steady accumulation |
| **HAVELLS** | Industrials | 27% | 44 | 19 | 56 | +8.5 | 13 | Electricals, accumulation |
| **OBEROIRLTY** | Real Estate | 15% | 25 | 15 | 41 | **+14.7** | 12 | Sharpest delivery uptick |

## Value-trap confirmation (signal working as intended)
- **VEDL** — 56% below 52w high (deepest drawdown in the set) but **delivery FALLING (-3.9)** → no accumulation conviction. The ownership layer correctly *withholds* points: cheap + falling = likely falling knife, not a bargain. Exactly the value-trap discrimination this layer was meant to provide.

## Strongest thesis names (down + cheap + fundamentals + accumulation)
**TCS, HEROMOTOCO, ESCORTS, ITC, COALINDIA** — beaten down, reasonable/cheap PE, high ROE, AND rising delivery conviction. These best fit "down for sentiment reasons, smart money quietly accumulating."

## ⚠️ Limitations & honest gaps
1. **Delivery % is a *proxy* for accumulation, not literal ownership.** It is NOT the same as quarterly FII%/DII%/promoter holding. For true shareholding-pattern ownership we still need Screener.in (UI) or a paid feed (Global Datafeeds). Next step.
2. **Bulk/block deal signal is empty for large-caps** — it only surfaces for small/midcaps (2,045 deals across 300 mostly small/midcap symbols). It will become valuable when we expand the universe down-cap.
3. **NSDL FPI regime is stale via nselib** (returns Oct-2025). Need an alternative for fresh daily FII/DII (NSE provisional figures / Moneycontrol) — nselib's `cash_market` FII/DII function has an import bug in 2.5.1.
4. Delivery `trend` = mean(last 5 sessions) − mean(prior sessions); a corrected newest-first ordering bug was fixed (earlier run had inverted signs).

## Next steps
1. **True shareholding %** — pull quarterly promoter/FII/DII holding + QoQ change per finalist (Screener.in cross-check or paid feed).
2. **Fresh FII/DII** — replace stale NSDL with NSE provisional daily figures.
3. **Expand universe down-cap** to activate the bulk/block institutional-buyer signal.
