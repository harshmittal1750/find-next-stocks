# Multi-Factor Model & Weighting — Indian Stock Ranking

**Date:** 2026-05-31 · **Engine:** `../scripts/rank_all.py` · **Universe:** all NSE EQ ≥1000 Cr

Every metric is normalized **min-max 0–1 across the universe** (median-filled, direction-aware),
averaged within its group, then groups are combined by the weights below into a **0–100 score**.
All data is captured in ONE yfinance `.info` call per stock (Stage 2) + delivery/ownership (Stages 3–4).

## Factor groups, metrics & weights

| Group | Weight | Metrics (direction) | Why it matters |
|---|---|---|---|
| **quality** | 2.5 | ROE+, ROA+, EBITDA margin+, debt/equity−, current ratio+ | Durable, profitable, solvent business — the core of "fundamentally good" |
| **smart_money** | 2.0 | institutional%+, #institutions+, promoter%+, **delivery trend+**, delivery%+ | *Who* is invested + accumulating. Delivery trend = our FII/DII entering/exiting proxy |
| **valuation** | 2.0 | PE−, P/B−, PEG−, EV/EBITDA−, dividend yield+ | Cheapness — paying a fair/low price |
| **growth** | 1.75 | earnings gr+, revenue gr+, qtrly earnings gr+, forward-EPS growth+ | Forward trajectory — value + growth, not value trap |
| **price_setup** | 1.5 | % below 52w high+ (capped 55), price vs 50DMA+ | The "down" (drawdown) **and** short-term bottoming/turning |
| **analyst** | 1.25 | target upside %+, recommendation−(buy=1), # analysts+ | Professional conviction + implied upside + coverage breadth |
| **momentum** | 0.75 | 1-yr price change+, price vs 200DMA+ | Avoid pure falling knives; reward names starting to recover |

Total raw weight = 11.0 (normalized to sum=1 internally). Weights are **fully tunable** (CLI `--weights`).

### Weighting rationale
- **Quality highest (2.5):** the thesis is "fundamentally *good* but down" — quality is the anchor; it's also what protects against value traps.
- **Smart_money & valuation tied (2.0):** the user's two explicit priorities — *who is investing* (FII/DII/institutional accumulation) and *is it cheap*.
- **Growth (1.75):** distinguishes a cheap compounder from a cheap declining business.
- **Price_setup (1.5):** encodes the "down" requirement + a bottoming check so we don't buy mid-crash.
- **Analyst (1.25):** useful corroboration/upside, but down-weighted (lagging, herd-prone).
- **Momentum (0.75):** lowest — we *want* beaten-down names, so momentum is only a falling-knife guard.

## How each factor signals positive vs negative (for stock selection)
- **FII/DII / smart-money ENTERING (↑):** rising delivery %, high & broad institutional holding, promoter increasing. **EXITING (↓):** falling delivery trend, thin/declining institutional base. → `smart_money` group.
- **Undervaluation (↑):** low PE/PB/PEG/EV-EBITDA vs universe; **Overvaluation (↓):** stretched multiples. → `valuation`.
- **Quality (↑):** high ROE/ROA/margins, low debt; **(↓):** negative returns, high leverage. → `quality`.
- **Growth (↑):** positive & accelerating earnings/revenue, forward EPS > trailing; **(↓):** shrinking. → `growth`.
- **Opportunity (↑):** well below 52w high but holding above 50DMA (bottoming); **Risk (↓):** free-falling below 50DMA. → `price_setup` + `momentum`.
- **Analyst (↑):** strong buy + high target upside + wide coverage. → `analyst`.

## ⚠️ Known gap — TRUE quarterly FII/DII change
The single most-requested signal — *exact* FII% and DII% increasing/decreasing quarter-over-quarter
per stock — is **not freely + legitimately available programmatically**:
- yfinance gives only a current institutional% **snapshot** (no FII/DII split, no QoQ history).
- NSE shareholding API is blocked + ToS-prohibited; NSDL FPI (market-level) lags via nselib.
- Screener.in has it but **no API + no-copy ToS**; BSE's private API is fragile/undocumented.

**Current proxy:** `delivery_trend` (recent vs prior delivery %) ≈ accumulation/distribution, plus
institutional level & breadth. **To get the real number:** Screener.in cross-check for finalists, or a
paid feed (Global Datafeeds — covers shareholding patterns incl. FII/DII + promoter pledge).

## Tuning examples
```bash
./.venv/bin/python rank_all.py --top 30
./.venv/bin/python rank_all.py --weights "valuation=3,smart_money=3,momentum=0"   # deep-value + smart-money
./.venv/bin/python rank_all.py --weights "quality=4,growth=3" --min-mcap 20000     # quality large-caps
```

## Additional factors considered but NOT yet wired (free-source limits)
- Promoter **pledge %** (value-trap red flag) — BSE/GDF.
- **Bulk/block deal** institutional net per stock — have data (`bulk_block_deals.csv`), only fires for small/midcaps.
- **Insider transactions** (yfinance `insider_transactions`) — sparse for Indian names; extra per-stock pull.
- Fresh **daily FII/DII cash** (market regime) — NSE provisional figures; market-wide, doesn't differentiate stocks.
- **Sector relative strength**, earnings-surprise history, EV/sales — future additions.
