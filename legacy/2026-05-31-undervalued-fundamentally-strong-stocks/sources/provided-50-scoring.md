# Scoring the 50 Provided Companies

**Date:** 2026-05-31 · **Script:** `../scripts/score_provided.py` · **Data:** `../data/provided_50.csv` → `../data/provided_50_scored.csv`

## Method
Each column → a **weight** + a **direction**. Normalized by **percentile-rank** (0–1) within the 50 names
(robust to outliers like Indo Thai's +1114% profit var). `final_score = Σ(weight × rank_pct) × 100`.

## Column weights (sum = 1.00)
| Column | Weight | Dir | Why / group |
|---|---|---|---|
| ROCE % | 0.15 | higher | Quality — capital efficiency (best single quality gauge) |
| 6-mth return % | 0.13 | **lower** | The "down" — more negative = more beaten-down opportunity |
| P/E | 0.12 | lower | Valuation — cheaper |
| ROE % | 0.12 | higher | Quality — current return |
| Qtr Profit Var % | 0.12 | higher | Growth/momentum — latest-quarter profit growth |
| ROE 5Yr % | 0.10 | higher | Quality consistency over a cycle |
| Qtr Sales Var % | 0.10 | higher | Growth — latest-quarter sales growth |
| CMP/BV | 0.08 | lower | Valuation — price/book |
| Mar Cap | 0.05 | higher | Size/stability |
| Div Yld % | 0.03 | higher | Shareholder return |
> Columns CMP, NP Qtr, Sales Qtr are absolute scale (not comparable cross-stock) → used only for context, not scored.

## Ranked result (top 15)
| # | Name | P/E | ROCE | ROE | Qtr Profit% | Qtr Sales% | 6m% | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | **Garuda Cons** | 13.2 | 41.8 | 31.2 | +90.8 | +84.2 | -21.2 | **79.2** |
| 2 | **Crizac** | 17.2 | 52.3 | 40.3 | +50.3 | +15.0 | -24.5 | 78.2 |
| 3 | **BLS Internat.** | 15.6 | 29.3 | 32.7 | +31.6 | +17.6 | -21.5 | 71.9 |
| 4 | **Cigniti Tech.** | 11.4 | 34.1 | 26.0 | +31.8 | +12.2 | -31.8 | 68.4 |
| 5 | **eClerx Services** | 20.1 | 34.8 | 29.0 | +24.5 | +23.3 | -35.7 | 68.4 |
| 6 | Sharda Motor | 14.6 | 36.0 | 27.9 | +6.4 | +29.6 | -13.3 | 67.5 |
| 7 | Jyoti Resins | 15.8 | 36.5 | 27.0 | +1.5 | +18.2 | -21.3 | 66.3 |
| 8 | IRCTC | 29.5 | 46.1 | 34.7 | -0.4 | +15.1 | -25.5 | 66.1 |
| 9 | Arkade | 11.5 | 19.1 | 20.8 | +118.0 | +49.6 | -30.0 | 65.9 |
| 10 | Indo Thai Sec. | 37.6 | 34.6 | 28.6 | +1114.7 | +555.4 | -43.8 | 64.0 |
| 11 | MPS | 18.8 | 39.3 | 31.2 | +9.9 | +12.7 | -15.8 | 63.4 |
| 12 | LTM | 22.3 | 29.6 | 23.1 | +19.4 | +15.6 | -34.0 | 61.2 |
| 13 | Indrapr.Medical | 19.0 | 35.8 | 27.5 | +1.7 | +9.3 | -24.4 | 60.4 |
| 14 | Mazagon Dock | 38.4 | 36.0 | 29.2 | +108.8 | +21.3 | -8.1 | 60.2 |
| 15 | Wealth First Por | 27.2 | 34.6 | 27.7 | +345.2 | +606.4 | -4.2 | 60.1 |

Full 50 in `provided_50_scored.csv`. Bottom 3: Elantas Beck (17.9), Jupiter Wagons (21.2), Thejo Engg. (26.3).

## Reads
- **Best all-round (cheap + quality + growth + down):** Garuda Cons, Crizac, BLS, Cigniti, eClerx — high on every group.
- **High quality but pricey / not very down** (so mid-rank): Gillette (ROCE 90%, ROE 66% but PE 40, P/B 27.6, only -6.5%), Shilchar (great returns but profit -49%, sales -35%).
- **Explosive growth outliers** (Indo Thai, Wealth First, Marsons): rank-based scoring caps their influence so one freak quarter doesn't crown them — verify if growth is sustainable or a low-base/one-off.

## Caveats
- **Rank-based = relative within these 50 only.** A score of 79 means "best of this list", not "great absolute".
- **Arkade** shows NP Qtr **−109.57** yet Qtr Profit Var **+118%** — inconsistent (loss with positive variation %); likely a turnaround/base-effect or data quirk. Treat its growth score with caution.
- Single-quarter variation (Qtr Profit/Sales Var) is noisy — multi-year growth (which we don't have here) is more reliable. Cross-check on Screener.
- No debt/pledge column provided → leverage & promoter-pledge risk not captured. Add before acting.

## Tuning
Edit `COLW` in `score_provided.py` (e.g. raise `ret_6m` weight for a deeper-value tilt, or drop it to rank on pure quality). Weights must sum to 1.
