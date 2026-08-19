# First Screen — Beaten-Down but Fundamentally Strong (NSE)

**Date:** 2026-05-31 · **Source:** yfinance (free) · **Universe:** 186 NSE large/mid-caps → 180 with data
**Data:** `../data/raw_fundamentals.csv` (all metrics), `../data/screen_results.csv` (scored+ranked)
**Method:** `../scripts/screen_indian_stocks.py` — scores price weakness (% below 52w high) + ROE + low debt + margins + earnings/revenue growth + cheap PE/PB.

## Top candidates (score ≥ 11)

| Ticker | Company | Sector | Price | % below 52w high | PE | ROE% | D/E | EPS gr | Why interesting |
|---|---|---|---|---|---|---|---|---|---|
| MAZDOCK | Mazagon Dock | Defence/Industrials | 2457 | 31% | 38 | 29 | 4 | +109% | Defence order book, near-zero debt, explosive earnings — pricey PE |
| ESCORTS | Escorts Kubota | Industrials | 2860 | 32% | 23 | 12 | 1 | +1% | Tractors/capex, low debt, 21% margin |
| CHAMBLFERT | Chambal Fert | Materials | 466 | 20% | **10** | 20 | 10 | +30% | Cheap, growing, low debt |
| NMDC | NMDC | Mining | 88 | 7% | **10** | 23 | 19 | +38% | Cheap PSU miner, rev +68% |
| TCS | TCS | IT | 2259 | **36%** | 17 | **48** | 10 | — | Best-in-class ROE, IT down-cycle discount |
| INFY | Infosys | IT | 1161 | 33% | 15 | 31 | 10 | — | Cheap large-cap IT, FII-heavy |
| HEROMOTOCO | Hero MotoCorp | Auto | 4903 | 23% | 17 | 28 | 4 | +26% | 2-wheeler leader, rev +30% |
| BPCL | BPCL | Energy (OMC) | 298 | 24% | **5** | 28 | 54 | +28% | Very cheap PSU OMC |
| HINDPETRO | HPCL | Energy (OMC) | 394 | 23% | **5** | 31 | 85 | +78% | Cheap, but higher debt |
| COALINDIA | Coal India | Energy | 458 | 7% | **9** | 28 | 12 | — | Cheap, high yield PSU |
| TATAELXSI | Tata Elxsi | IT/Design | 4285 | 36% | 43 | 21 | 5 | +28% | Quality compounder, big drawdown |
| PERSISTENT | Persistent | IT | 5194 | 21% | 44 | 26 | 6 | +32% | Mid-IT growth, expensive |
| HINDUNILVR | HUL | FMCG | 2154 | 22% | 48 | 22 | 3 | +21% | Defensive blue-chip on sale |
| IRCTC | IRCTC | Travel/PSU | 510 | 36% | 29 | 35 | 2 | -9% | Monopoly moat, high ROE |
| MRF | MRF | Auto/Tyres | 123420 | 25% | 22 | 12 | 15 | +37% | Brand moat |
| TRENT | Trent | Retail | 4224 | 33% | **87** | 27 | 36 | +26% | High growth but very expensive |
| NAUKRI | Info Edge | Internet | 995 | 36% | 44 | 5 | 1 | +22% | Naukrai/Zomato/PB holdco value |
| APLAPOLLO | APL Apollo | Steel tubes | 1831 | 20% | 42 | 25 | 9 | +21% | Structural steel-tube leader |

## Read of the data — clusters

1. **Cheap PSUs / cyclicals (best raw value):** CHAMBLFERT, NMDC, BPCL, HINDPETRO, COALINDIA — single-digit PE, high ROE, growing. Risk: cyclical earnings, govt/policy overhang.
2. **Quality IT on a down-cycle discount:** TCS, INFY, TATAELXSI, PERSISTENT — best-in-class ROE, FII favorites, ~30–36% off highs on sector pessimism. Strong "temporary weakness, durable franchise" thesis.
3. **Capex / defence / industrials:** MAZDOCK, ESCORTS, APLAPOLLO — structural tailwinds, low debt; some (MAZDOCK) carry rich multiples.
4. **Defensive blue-chips on sale:** HINDUNILVR, HEROMOTOCO, MRF, IRCTC — quality at a discount.

## ⚠️ Caveats & known data issues
- **yfinance fundamentals can be stale/wrong** for some names — e.g. BAJAJHLDNG shows margin/D/E artifacts (holding-company accounting). Verify any finalist on Screener.in before acting.
- This screen uses **price + fundamentals only**. It does NOT yet include the decisive **ownership signal** (FII/DII/promoter buying) — so it cannot yet distinguish "temporary dip" from "value trap." See impact-factors §3.
- **% below 52w high** uses trailing 52w; a stock 55% down (VEDL) may be a falling knife, not a bargain — needs the value-trap red-flag check (pledge, debt trend, sector decline).
- 6 tickers returned no data (incl. TATAMOTORS.NS — 2025 demerger changed symbol). Universe is a curated ~Nifty-200 subset, not exhaustive.

## Next steps (to make this decision-grade)
1. **Add ownership layer** (`nselib`): FII/DII trend + quarterly shareholding / promoter-holding change per finalist → confirm smart-money is *accumulating* while price is down.
2. **Value-trap filter:** promoter pledge, debt trend, interest coverage, sector cycle.
3. **Cross-verify finalists on Screener.in** (10yr financials) — yfinance is a first pass, not ground truth.
4. **Macro overlay** (impact-factors §4): rate cycle, crude (for OMCs), sector tailwinds.
5. Expand universe to full Nifty 500 once the pipeline is stable.
