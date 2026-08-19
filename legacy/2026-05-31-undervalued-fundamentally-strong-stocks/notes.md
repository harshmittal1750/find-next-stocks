# Research: Undervalued but Fundamentally Strong Indian Stocks

**Started:** 2026-05-31
**Goal:** Find NSE stocks that are *down on price but fundamentally strong* (contrarian value), and catalog the factors that move a stock positively/negatively — to power a stock-ranking system.

## Tooling used
- MCPs available are crypto-only (Nansen/Messari) → N/A for equities.
- Free APIs: **yfinance** (used, works for `.NS` tickers — price + fundamentals). Also available per prior research: nselib, jugaad-data, nsepython. Screener.in = UI only (no API, no-copy ToS).

## Deliverables (this session)
1. **`sources/stock-impact-factors.md`** — full catalog of positive/negative stock-impact factors (fundamentals, valuation, ownership, macro, sector/moat, catalysts, technicals), tagged free/paid, mapped to our thesis + value-trap red flags.
2. **`scripts/screen_indian_stocks.py`** — reproducible screen over ~Nifty-200 universe.
3. **`data/raw_fundamentals.csv`** (186 rows) + **`data/screen_results.csv`** (scored/ranked).
4. **`sources/screen-findings.md`** — top candidates + interpretation + caveats.

## Key results (first screen, price + fundamentals only)
- 180/186 tickers pulled. Top clusters:
  - **Cheap PSUs/cyclicals:** CHAMBLFERT, NMDC, BPCL, HINDPETRO, COALINDIA (single-digit PE, high ROE).
  - **Quality IT down-cycle discount:** TCS, INFY, TATAELXSI, PERSISTENT (~30–36% off highs, top ROE).
  - **Capex/defence:** MAZDOCK, ESCORTS, APLAPOLLO.
  - **Defensive blue-chips on sale:** HINDUNILVR, HEROMOTOCO, MRF, IRCTC.

## Important limitations
- No ownership/smart-money signal yet → can't separate "temporary dip" from "value trap."
- yfinance fundamentals can be stale/wrong (e.g. BAJAJHLDNG holdco artifacts) → cross-verify finalists on Screener.in.

## Ownership layer (DONE — 2026-05-31)
- **`scripts/ownership_layer.py`** + **`data/ownership_overlay.csv`** + **`sources/ownership-findings.md`**
- Signals via nselib→NSE/NSDL: **delivery %** (per-stock conviction, fresh ✅ = primary), **bulk/block deals** (by client name — only fires for small/midcaps, empty for large-caps), **NSDL FPI** (market regime — stale via nselib ❌, context only).
- **Top combined (down + cheap + strong + accumulation):** TCS, HEROMOTOCO, ESCORTS, ITC, COALINDIA, also HINDUNILVR, TATAELXSI, BEL, VGUARD, LUPIN, HAVELLS, OBEROIRLTY.
- **Value-trap caught:** VEDL (56% off high but delivery FALLING → no conviction) correctly scored low.
- Fixed a delivery-ordering bug (NSE data is newest-first; trend signs were inverted in first run).

## Shareholding % layer (DONE — 2026-05-31)
- **`scripts/shareholding_layer.py`** + **`data/shareholding_layer.csv`** + **`sources/shareholding-findings.md`**
- Source: yfinance `major_holders` → promoter% + institutional% + #institutions (snapshot, robust for large-caps).
- **FINAL top picks (all 3 layers aligned):** TCS (18), then ESCORTS, COALINDIA, HEROMOTOCO, HINDUNILVR, BEL (17).
- **Gaps (free-source reality):** snapshot only = NO QoQ change; NO FII/DII split; Yahoo insider% misclassifies no-promoter names (e.g. ITC). NSE blocks shareholding API; BSE private API fragile; Screener.in = no API/no-copy.

## Ranking engine (DONE — 2026-05-31)
- **`scripts/rank_all.py`** + **`data/final_ranking.csv`** + **`sources/ranking-engine.md`**
- Merges all 3 layers → normalized (min-max) components → **tunable weighted 0-100 score**. CLI: `--weights "valuation=4,..."`, `--top`, `--min-mcap`. Reads CSVs only (instant, no network).
- 6 components: price_weakness, valuation, quality, growth, conviction (delivery), ownership.
- **Now ranks the FULL 180-stock universe** (`TOP_N_FOR_DELIVERY=999`; all 180 have delivery + promoter/institutional signals).
- **Default top 12:** TCS, BAJAJHLDNG, PRESTIGE, ITC, INFY, WHIRLPOOL, LODHA, JUBLFOOD, VEDL, COROMANDEL, ESCORTS, KAJARIACER.
- Expanding 40→180 surfaced new names (PRESTIGE, WHIRLPOOL, JUBLFOOD, COROMANDEL, KAJARIACER, HDFCBANK, RVNL, MARUTI, JSWSTEEL, ICICIBANK, SBIN, DLF). Absolute scores are universe-relative (compare ranks within a run).

## Pipeline now COMPLETE
1. `screen_indian_stocks.py` → screen_results.csv (fundamentals + price)
2. `ownership_layer.py` → ownership_overlay.csv (delivery + deals + FPI)
3. `shareholding_layer.py` → shareholding_layer.csv (promoter/institutional)
4. `rank_all.py [--weights]` → final_ranking.csv (weighted 0-100)

## FULL UNIVERSE RUN (DONE — 2026-05-31)
- **Universe:** all NSE EQ ≥₹1000 Cr = **1,353 stocks** (vs Screener's 1,530; gap = 97 yfinance blank-mcap + NSE non-EQ + BSE-only). Built via `build_universe.py` → `data/universe_filtered.csv`.
- **Pipeline (all 1,353, full coverage):** `screen_universe.py` (fundamentals incl. analyst/DMA/EV-EBITDA factors) → `ownership_layer.py` (delivery 1353/1353 = 100%, NSE did not throttle) → `shareholding_layer.py` (promoter/inst 1343/1353) → `rank_all.py`.
- **Factor model:** 7 weighted groups (quality 2.5, smart_money 2.0, valuation 2.0, growth 1.75, price_setup 1.5, analyst 1.25, momentum 0.75). Docs: `sources/factor-model.md`.
- **Robustness fix:** switched normalization from min-max → **percentile-rank + sanity guards** (drops artifacts like SPARC's 276% ROE; non-positive PE/PB→neutral). Scores now spread 28–66.
- **Top balanced:** GESHIP, EXPLEOSOL, VENUSREM, VSTIND, HINDZINC, THYROCARE, OBEROIRLTY, COFORGE, NMDC. (EXPLEOSOL & GARUDA also topped the provided-50 — cross-validation.)
- **DELIVERABLE:** `REPORT.html` (visual dashboard) + `REPORT.md` — top 30, 5 investor lenses (deep-value/quality/smart-money/growth), sector heat, value-trap watchlist, provided-50, methodology. Built by `build_report.py`.
- **Provided-50 scoring:** `scripts/score_provided.py` + `sources/provided-50-scoring.md` (top: Garuda Cons, Crizac, BLS, Cigniti, eClerx).

## Interactive dashboard (DONE — 2026-05-31)
- **`scripts/build_dashboard.py`** → **`DASHBOARD.html`** (~814 KB, self-contained: vanilla JS + SVG, no CDN/offline-OK). Reads `data/final_ranking.csv` + merges beta/52w-low/DMAs from `raw_fundamentals.csv`.
- Charts: "The Map" scatter (valuation×quality, bubble=mcap, colour=sector, cheap&strong quadrant), Top-20 bars, Sector scan, sortable/filterable full table, per-stock 7-factor breakdown.
- **Drawdown buy-zone feature:** "Market drawdown scenario" slider → ideal buy price = CMP×(1−β×drawdown), β clamped [0.3,2.5], missing β→1.0 (303 rows). Adds `Buy ≤ ₹` + `To-buy %` columns + a price-ladder chart (52w-low→high with CMP/50-DMA/200-DMA + shaded buy zone) in the detail panel.
- Tooltips on every short form (hover ⓘ / headers) + full glossary card.
- Regenerate: `./.venv/bin/python scripts/build_dashboard.py`.

## ITC demerger-adjusted re-score (DONE — 2026-06-01)
- **`scripts/rescore_itc.py`** + **`data/itc_demerger_rescore.csv`**. Reuses rank_all's exact load/derive/group logic; overrides ONLY ITC's two distorted earnings-growth fields, re-ranks the whole 1353 universe.
- **Why:** ITC Hotels demerger (eff. Jan 2025) inflated the year-ago base with a one-time exceptional gain → yfinance shows `earningsGrowth = earningsQuarterlyGrowth = -72.7%`, craters growth-group to 7 and drops ITC to **rank 260 (top 19%), score 54.7**. Not a business decline — an accounting comp.
- **Result:** stripping the artifact lifts ITC to **rank ~80–135 (top 6–10%)**, score 57–58:
  - *Neutralize* (drop the 2 distorted metrics, keep revenueGrowth −5% + fwdEPS +7.7%): g_growth 7→**30.4**, score **58.2**, **rank 81** (+179 places, top 6%). Most defensible.
  - *Adjusted +5%/+8% underlying*: rank ~117–122, score 57.0–57.2.
- **Key nuance:** even adjusted, ITC's growth lands ~20–30 pctile (NOT high) — a slow-growing mega-cap staple is genuinely below-median on growth in a universe full of fast small/midcaps. The model was right to dock it *some* growth, just not bottom-decile. Net: ITC is a **top-decile quality+value name** once accounting noise is removed; verdict = fairly/cheaply valued, not overvalued.
- **Caveat:** exact magnitude of the year-ago exceptional gain not pulled from Screener; direction (artifact, not deterioration) is certain.

## Next steps (open)
1. **QoQ shareholding change** (biggest data gap) — Screener.in cross-check / paid feed (Global Datafeeds).
2. **FII/DII split** — same source.
3. **Value-trap deepening** — promoter pledge %, debt trend, interest coverage.
4. **Expand universe** down-cap / full Nifty 500 (raise TOP_N_FOR_DELIVERY; re-run layers 2-3).
5. **Fresh FII/DII** — replace stale NSDL with NSE provisional daily figures.

## Weekly auto-refresh + email digest (DONE — 2026-06-04)
- **Goal:** keep the ranking fresh and get emailed about major week-over-week changes.
- **`scripts/weekly_update.py`** orchestrates: archive without clearing the fundamentals cache → batched/incremental prices → bulk NSE delivery files → cached shareholding → targeted NSE/BSE/Yahoo gap recovery → coverage-adjusted ranking → rebuild and synchronise both dashboards → diff/email. Rebuildable caches live in gitignored `data/cache/`; enrichment provenance → `data/enrichment_report.csv`; snapshots → `data/history/`, report → `reports/weekly_<date>.html`, logs → `logs/`.
- **`scripts/diff_rankings.py`** — "major change" detector + HTML/text email renderer. Sections: Top-25 entrants/drop-outs, big price moves (±15%), rank gainers/losers (±25), score jumps (±5), drawdown deepening (+10pp), delivery-trend flips (accum↔distribution), universe add/remove. Thresholds tunable at top of file. Includes NSDL FPI market-regime banner + current Top 10.
- **`scripts/notify_email.py`** — Resend API sender; creds from gitignored `.env` (`RESEND_API_KEY`, `EMAIL_TO`, `EMAIL_FROM`). Free tier sends from `onboarding@resend.dev`. Test: `notify_email.py --test`.
- **Schedule:** macOS `launchd` LaunchAgent `com.harshmittal.stockweekly` — **Saturday 09:00**, installed & verified (runs missed job on next wake). Plist in `scripts/`, installed copy in `~/Library/LaunchAgents/`.
- **Optimized 2026-07-22:** weekly runs reuse 30-day fundamentals, fetch price history in Yahoo batches, replace 1,353 NSE delivery calls with cached whole-market daily files, and remove the duplicate Yahoo shareholding pass. Expected steady runtime: 1–5 minutes; use `--full-fundamentals` for a forced cold refresh.
- **Coverage guardrail 2026-07-22:** `enrich_missing_data.py` targets stocks below 75% weighted coverage or missing core quality/valuation evidence. It fills blanks only through NSE daily P/E, BSE company headers and targeted Yahoo statements. Final scores are shrunk toward neutral based on weighted coverage; stocks below 60% or without enough core evidence are visible but unranked. The live pass recovered 420 fields (NSE 12, BSE 98, Yahoo 310), leaving 29 of 1,353 stocks unranked rather than assigning them guess-heavy scores.
- **Git rank tracker 2026-07-22:** `build_rank_tracker.py` compares `data/final_ranking.csv` with the staged Git index and the upstream branch (`origin/main`) for all stocks. Positive rank deltas mean moved up; score deltas retain their sign. It writes `data/rank_tracker.csv` + metadata JSON and feeds `Δ Stage` / `Δ Push` plus full score transitions into both dashboards. Newly ranked/unranked stocks receive transition labels instead of fabricated numeric movement.
- **Full docs:** `AUTOMATION.md`.

## Dashboard pattern enhancements (DONE — 2026-06-11)
- **`scripts/build_dashboard.py`** enhanced with 5-tab "Patterns & Insights" panel:
  1. **🟢 Accumulation signals** — delivery trend >+5 AND institutional% ≥30 AND score ≥55. Organised money entering weakness.
  2. **🎯 Near 52w low + quality** — quality score ≥60, trading within 20% of 52w low. Genuine contrarian entries.
  3. **⚠️ Value traps** — >30% off high, quality <40, delivery trend <−5. Cheap for bad reasons.
  4. **📈 Weekly movers** — top gainers and fallers vs previous ranking's prices. Side-by-side view.
  5. **🏭 Sector heat** — cheap & strong concentration per sector + weekly price performance by sector.
- **Rank change column** (Δ Rank) added to main table — ▲/▼ vs previous week.
- **Pattern badges** in detail panel — green/blue/red badges per stock.
- **"Last refreshed" badge** in header.
- Additional stats: accumulation signals, near-52w-low, value trap counts in header stats bar.

## Fresh data patterns found (2026-06-11 run)
- **120 stocks near 52w lows** (within 5%): top high-quality names include IEX (87.9 quality), ITC (87.0), GLAXO (86.0), KFINTECH (85.3), PFIZER (85.2), ABBOTINDIA (84.5), TCS (81.6), ECLERX (81.1).
- **Sector rotation this week**: Healthcare (+1.4%) and Energy (+0.9%) only green sectors. Real Estate (−3.6%), Utilities (−4.0%), Technology (−2.5%) biggest laggards.
- **Top gainers Jun 4→11**: UNICHEMLAB +39.7%, NPST +34.7%, CARTRADE +34.6%, VENUSREM +33.1%.
- **Big fallers**: E2E Networks −90.6% (likely stock split/event), ANANDRATHI −49.4%, TRENT −35.7% (now 57% off high, still PE 87x).
- **Deepened drawdown quality names**: ECLERX now 46% off high (quality 81, fell 10.8% this week), SUMICHEM 33% off high (quality 80.4), EXPLEOSOL 43% off high (quality 77.9).
- **Accumulation signals (score≥55)**: ~20 stocks with delivery trend >+5 AND institutional% ≥30.
- **Value traps confirmed**: SEPC, INDOSTAR, SBICARD, LANDMARK, PINELABS — all deeply discounted but fundamentally weak + delivery falling.

## Related
- Data-source research: `../2026-05-30-indian-stock-data-sources/`

## JSON dashboard boundary + ownership validation (DONE — 2026-07-22)

- Root cause of Gallantt's impossible 107.5% promoter value: Yahoo returned
  `heldPercentInsiders=1.07481`; the pipeline correctly treated the field as a fraction,
  but did not reject a physically impossible result above 100%.
- `shareholding_layer.py` now accepts only 0–100% ownership values, rejects disjoint
  promoter + institutional totals above 100.5%, and applies traceable corrections from
  `data/shareholding_overrides.json`. Gallantt is corrected to the official 70.0% value
  reported in its NSE-hosted March 2026 investor presentation. The impossible Yahoo
  value remains untouched in `raw_fundamentals.csv` as raw-provider provenance; it is
  rejected at the derived-data boundary and never reaches ranking or dashboard output.
- `rank_all.py` repeats the ownership guardrails so malformed fallback data cannot bypass
  the shareholding layer.
- Dashboard records moved out of both HTML files into `site/dashboard-data.json`.
  `build_dashboard.py` writes schema-versioned, ticker-sorted JSON for stable Git diffs;
  both `DASHBOARD.html` and `site/index.html` fetch that one payload.
- `update_site.py` no longer reparses or splices a JavaScript array from HTML. It rebuilds
  when requested and validates JSON schema, record count, and ticker uniqueness.
