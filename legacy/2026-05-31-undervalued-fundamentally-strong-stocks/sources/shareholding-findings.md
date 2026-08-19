# Shareholding % Layer — Ownership Snapshot

**Date:** 2026-05-31 · **Source:** yfinance `major_holders` (free, stable)
**Script:** `../scripts/shareholding_layer.py` · **Data:** `../data/shareholding_layer.csv`

## What this layer adds
Real ownership composition per stock (vs. the delivery-% *proxy* from the prior layer):
- **promoter_pct** (Yahoo insider%) — skin in the game / insider confidence
- **institutional_pct** — FII+DII+others combined
- **institutions_count** — breadth of institutional interest

## FINAL ranking (fundamentals + delivery conviction + shareholding)

`final_score` = fundamental score + delivery/ownership score + shareholding score.

| Rank | Ticker | Sector | %↓ 52wH | PE | ROE% | Promoter% | Instit.% | #Inst | Deliv.trend | Final |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **TCS** | IT | 36 | 17 | 48 | 71.8 | 17.9 | 330 | +7.8 | **18** |
| 2 | **ESCORTS** | Industrials | 32 | 23 | 12 | 73.1 | 13.7 | 82 | +9.5 | 17 |
| 2 | **COALINDIA** | Energy | 7 | 9 | 28 | 63.1 | 25.8 | 165 | +5.6 | 17 |
| 2 | **HEROMOTOCO** | Auto | 23 | 17 | 28 | 37.4 | 38.8 | 258 | +7.0 | 17 |
| 2 | **HINDUNILVR** | FMCG | 22 | 48 | 22 | 62.3 | 20.8 | 275 | +6.6 | 17 |
| 2 | **BEL** | Defence | 13 | 49 | 28 | 53.2 | 28.6 | 271 | +11.8 | 17 |
| 7 | BAJAJHLDNG | Financials | 30 | 12 | 13 | 61.6 | 11.8 | 149 | +7.7 | 16 |
| 7 | INFY | IT | 33 | 15 | 31 | 16.4 | 55.1 | 362 | +1.7 | 16 |
| 7 | VGUARD | Industrials | 25 | 44 | 14 | 55.7 | 35.5 | 47 | +14.4 | 16 |
| 10 | HAVELLS | Industrials | 27 | 44 | 19 | 63.5 | 24.1 | 182 | +8.5 | 15 |
| 10 | OBEROIRLTY | Real Estate | 15 | 25 | 15 | 67.7 | 25.0 | 174 | +14.7 | 15 |
| 10 | HINDPETRO | Energy | 23 | 5 | 31 | 55.0 | 25.5 | 201 | +3.8 | 15 |
| 10 | LUPIN | Pharma | 9 | 19 | 27 | 47.0 | 35.1 | 231 | +6.4 | 15 |
| 10 | ITC | FMCG | 33 | 17 | 29 | 27.2* | 52.8 | 207 | +10.8 | 15 |
| 10 | LODHA | Real Estate | 39 | 27 | 16 | 72.3 | 17.8 | 208 | +5.5 | 15 |
| 10 | MAZDOCK | Defence | 31 | 38 | 29 | 81.2⚠ | 6.1 | 69 | +3.8 | 15 |
| 10 | TATAELXSI | IT | 36 | 43 | 21 | 44.9 | 15.0 | 103 | +9.9 | 15 |
| 10 | BPCL | Energy | 24 | 5 | 28 | 56.5 | 28.1 | 219 | +1.2 | 15 |

## Ownership read (who holds what)
- **High promoter + high institutional + broad #inst = best quality ownership:** TCS (72% promoter, 330 inst), HINDUNILVR (62% / 275), BEL (53% / 271), HEROMOTOCO (37% / 258 — institution-validated).
- **Institution-favorite, no promoter dependency:** INFY (16% promoter, 55% institutional, **362 institutions** — most broadly held in the set), ITC (53% institutional).
- **⚠ Low-float watch:** MAZDOCK (81% govt promoter, only 6% institutional, 69 inst) — thin float, govt-controlled. NESTLEIND/NAUKRI show *negative* delivery trend (distribution, not accumulation) → demoted.

## ⚠️ Limitations (free-source reality — be honest)
1. **Snapshot only — NO QoQ change.** This is the single biggest gap. We can see *current* promoter/institutional %, but not whether they are *increasing* this quarter — which is the real "smart money is buying the dip" confirmation. QoQ requires **Screener.in** (UI, no-copy ToS) or a **paid feed** (Global Datafeeds).
2. **No FII-vs-DII split.** Yahoo lumps all institutions together; we can't separate foreign (FII) from domestic (DII) conviction.
3. **Yahoo "insider%" ≈ promoter%, but misclassifies no-promoter names** — e.g. **ITC** shows 27.2%* (ITC technically has no promoter; largest holder BAT ~29%). Cross-verify finalists on Screener.in.

## The complete pipeline now in place
```
1. screen_indian_stocks.py  -> fundamentals + price weakness  (data/screen_results.csv)
2. ownership_layer.py       -> delivery conviction + deals + FPI (data/ownership_overlay.csv)
3. shareholding_layer.py    -> promoter/institutional snapshot  (data/shareholding_layer.csv)  <-- FINAL RANK
```

## Strongest thesis names (all three layers aligned)
**TCS, COALINDIA, HEROMOTOCO, BEL, HINDUNILVR** — beaten down, cheap/fair PE, high ROE, strong promoter + institutional ownership, AND rising delivery conviction. Best fit for "down on sentiment, quality ownership accumulating."

## Next steps
1. **QoQ shareholding change** (the key gap) — Screener.in cross-check for top ~10 finalists, or wire a paid feed.
2. **FII/DII split** — same source.
3. **Value-trap deepening** — promoter pledge %, debt trend, interest coverage on finalists.
4. Build a single `rank_all.py` that chains the 3 layers + weights into one reproducible score.
