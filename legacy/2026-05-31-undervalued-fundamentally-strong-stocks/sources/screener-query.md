# Screener.in Queries — "Fundamentally Amazing but Beaten Down"

**Date:** 2026-05-31
**Use:** screener.in → Screens → "Create new screen" → paste the query → Run. Tweak numbers to widen/narrow.
**Idea:** in a market crash, *everything* falls. These queries keep only stocks whose **price fell hard**
(well below 52-week high / near 52-week low) **while fundamentals stayed strong** (high returns, low debt,
real growth, clean ownership) — i.e. the drop is sentiment/market-driven, not business deterioration.

> In Screener, `High price` = 52-week high, `Low price` = 52-week low. Expressions & parentheses are allowed.

---

## 1. MAIN query — balanced "great business, on sale"
```
Market Capitalization > 1000 AND
Return on equity > 15 AND
Average return on equity 5Years > 15 AND
Return on capital employed > 15 AND
Debt to equity < 0.5 AND
OPM > 12 AND
Profit growth 5Years > 12 AND
Sales growth 5Years > 10 AND
Promoter holding > 40 AND
Pledged percentage < 5 AND
Current price < 0.7 * High price
```
Reads as: ≥₹1000 Cr, consistently high ROE/ROCE, low debt, healthy margins, 5-yr profit & sales growth,
solid promoter holding with little pledge — **trading 30%+ below its 52-week high.**

---

## 2. STRICT "amazing" — only the highest quality, deeply discounted
```
Market Capitalization > 2000 AND
Return on equity > 18 AND
Average return on equity 5Years > 18 AND
Return on capital employed > 18 AND
Debt to equity < 0.3 AND
Interest Coverage Ratio > 5 AND
OPM > 15 AND
Profit growth 5Years > 15 AND
Sales growth 5Years > 12 AND
Profit growth 3Years > 10 AND
Promoter holding > 45 AND
Pledged percentage < 2 AND
Current price < 0.65 * High price AND
Current price < 1.4 * Low price
```
Adds interest coverage, tighter debt, 3-yr growth check, and `< 1.4 × 52w-low` so it's **near the bottom**,
not just off the highs. Fewer, higher-conviction names.

---

## 3. DEEP-VALUE tilt — cheap multiples + crashed price
```
Market Capitalization > 1000 AND
Price to Earning < Industry PE AND
Price to Earning < 20 AND
Price to book value < 4 AND
Return on capital employed > 15 AND
Debt to equity < 0.6 AND
Profit growth 5Years > 10 AND
Promoter holding > 40 AND
Pledged percentage < 5 AND
Current price < 0.7 * High price AND
Dividend Yield > 0.5
```
Layers cheap valuation (PE below industry & < 20, P/B < 4) and a dividend floor onto the drawdown filter —
good for finding crashed names that are also statistically cheap.

---

## 4. "SMART-MONEY still there" — ownership-confirmed dip
```
Market Capitalization > 1000 AND
Return on equity > 15 AND
Debt to equity < 0.6 AND
Profit growth 5Years > 12 AND
Promoter holding > 45 AND
Change in promoter holding 3Years >= 0 AND
Pledged percentage < 3 AND
Current price < 0.7 * High price
```
Uses `Change in promoter holding 3Years >= 0` so **promoters are holding/adding**, not exiting — the
ownership confirmation our pipeline proxies with delivery-trend.

---

## Tuning knobs
| Want more results? | Want stricter? |
|---|---|
| Lower ROE/ROCE to 12 | Raise ROE/ROCE to 20 |
| `Current price < 0.8 * High price` (only 20% off) | `Current price < 0.6 * High price` (40%+ off) |
| Market cap > 500 | Market cap > 5000 (large-caps only) |
| Drop the pledge/promoter lines | Add `Quarterly profit variation > 0` (latest qtr still growing) |

## Useful Screener field names (for editing)
`Market Capitalization · Current price · High price · Low price · Price to Earning · Industry PE ·
Price to book value · Return on equity · Average return on equity 5Years · Return on capital employed ·
Return on assets · Debt to equity · Interest Coverage Ratio · OPM · OPM 5Year · Sales growth 5Years ·
Profit growth 5Years · Profit growth 3Years · EPS growth 3Years · Promoter holding ·
Change in promoter holding 3Years · Pledged percentage · Dividend Yield · PEG Ratio · Free cash flow`

## Note vs our pipeline
Screener's strengths fill exactly our two gaps: **multi-year consistency** (5-yr ROE/growth) and **ownership
trend** (`Change in promoter holding`, pledge). Use Screener to generate/validate the candidate list, then run
our `rank_all.py` factor model on the survivors for the weighted ranking.
```
```
