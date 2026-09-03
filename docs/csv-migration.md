# CSV → database migration

The dashboard reads 96 fields per stock. Storage is already in Postgres, but most of
those fields are still *CSV-shaped rows* imported from the legacy pipeline — nothing in
this repo computes them.

**Done when:** `select count(*) from current_metrics where origin = 'archive'` is 0, and
`legacy/` can be deleted without the dashboard losing a column.

## Where it stands

```sql
select origin, count(*) cells, count(distinct field) fields from current_metrics group by origin;
```

| origin | cells | fields | |
| --- | ---: | ---: | --- |
| `observation` | 58,809 | 46 | fetched or derived here |
| `archive` | 33,731 | 47 | still the legacy CSV |
| `ranking` | 23,001 | 17 | scoring engine output |

Started this session at 14 live fields / 90 archived (79.3% archive). Now **29.2%**, and live observations have passed half the dataset — the
archive is no longer the largest source.

Re-check this table before starting any step. Twice now a step was already finished by a
run in between, and the plumbing existed while a stale file read said otherwise.

## Steps

Ordered by dependency. Each is done when its check passes — not when the code looks right.

- [x] **0. One precedence rule.** `current_metrics` view: valid live observation wins,
      newest archived CSV as fallback. Both the API and the ranking job read it, so the
      rule cannot drift between them.
      *Check:* SPARC `roe_pct` resolves from `bse`, `rank` from `legacy_csv`. ✅

- [x] **1. Populate `price_bars`.** Already wired: `yahoo.py` emits `PriceBar`s and
      `refresh_jobs.py:364` calls `warehouse.write_price_bars`. It read as empty earlier
      only because Yahoo was returning 429 for every request.
      *Check:* 332,856 bars · 1,353 stocks · 2025-09-01 → 2026-08-31. ✅

- [x] **2. Moving averages from bars.** Added to `DerivedMetricsProvider`:
      `fiftyDayAverage` (1,352), `twoHundredDayAverage` (1,304). These feed `px_vs_50dma`
      and `px_vs_200dma`, the momentum and price-setup inputs, and were archive-only.
      *Check:* both report `origin = 'observation'`. ✅ 49 stocks have under 200
      sessions and correctly get no 200-day average.

      I also added `pct_below_52w_high` / `pct_above_52w_low` here and then removed them.
      Two layers already derive both from the live `fiftyTwoWeekHigh`/`Low` observations
      — `repository.py` for display and `scoring._derive_extra_factors` for the model,
      the latter applying `PCT_BELOW_CAP`. A third close-based definition was overwritten
      by both. `current_metrics` had shown them as `archive` only because no provider
      emitted those field *names*; the served value was never stale.

- [x] **2b. `beta` and `price_chg_pct`.** Done as part of 5e — the two entries were the
      same work described twice.

- [x] **3a. Delivery.** New `NseDeliveryProvider`: one whole-market bhavcopy per session
      covers every symbol, so the universe costs ~20 requests, not 1,353.
      `avg_delivery_pct` (1,338), `delivery_trend` (1,338), plus `delivery_recent_pct`.
      *Check:* all report `origin = 'observation'`. ✅
      13 stocks stay on the archive — they did not trade in the window, which is the
      honest answer rather than a zero.
      `delivery_trend` carries `unit="percentage_points"`: it is a difference between two
      percentages, so it is signed and unbounded. Labelling it `percent` would put it
      under the 0-100 ownership range check and silently reject every falling stock.

- [x] **4a. Audit: the engine exists and was reading stale inputs.** `scoring.py` is a
      faithful port of `rank_all.py` (same weights, groups, coverage floors) and
      `scoring_jobs.run_scoring` already writes `ranking_runs` + `ranked_stocks`.
      But `repository.LIVE_FIELD_MAP` was an **allow-list**: any observation field
      missing from its 16 entries was silently dropped, so four providers' worth of new
      fields reached Postgres and never reached the scorer. RELIANCE was ranked on
      `avg_delivery_pct` 58.0 / `delivery_trend` 1.9 from the archived CSV while the live
      values were 59.3 / 6.9.
      Fixed by making the map **rename-only** — unmapped fields pass through under their
      own name. A hand-maintained list of accepted fields fails open the wrong way:
      forgetting an entry loses data quietly instead of erroring.
      *Check:* scorer input now matches `current_metrics` for the delivery fields. ✅

- [x] **4b + 6. Repository reads the view.** `repository.py` now assembles stocks from
      one `current_metrics` query instead of a three-stage Python merge (5 archive CSVs →
      live overlay → ranking overlay). Since `scoring_jobs` feeds on `repository.load()`,
      the scorer inherits it: the API and the engine can no longer disagree about a
      stock's ROE, which they did (0.0893 archive vs 0.0748 live).
      `clean_legacy_stock` still runs per stock — the ownership guard is a property of
      the record, not the source.
      *Check:* `returnOnEquity` 0.0748, `rsi_14` present for the first time, 96 → 105
      fields. ✅
      **Ran scoring twice to test for feedback:** the view now serves the scorer its own
      previous `rank`/`final_score`, so a loop would drift. 0 ranks changed between two
      identical runs; 1,316 changed versus pre-cutover. `scoring.py` reads only
      fundamentals and overwrites those columns.
      Step 6's own check (archive at 0) is *not* met — the archive still supplies fields
      nothing else fetches. This closed the duplicate-merge half of it.
      Ranks are **written per run, not computed on read**: a percentile is only
      comparable within a frozen universe, and `rank_chg` needs a previous ordering to
      subtract from.
      *Check:* a run exists; its top 10 matches the legacy CSV's top 10 within a few
      places; `rank_chg` computes between two runs.

- [x] **4c. Ranking output into the view.** `rank`, `final_score`, `data_cov`,
      `score_status`, seven `g_*`, `model_score` and the movement columns now resolve
      from `ranked_stocks` (latest run) instead of the CSV — 15 fields, verified equal to
      the table row by row. View restructured from nested `FULL OUTER JOIN`s to
      `UNION ALL` + `DISTINCT ON` with an explicit priority, so a fourth source is one
      branch rather than another join arm.
      *Check:* archive 79.3% → 64.0%. ✅
      These are model **outputs**: nothing feeding scoring may read them, or a run takes
      its own previous answer as input.

## Remaining archive fields (73), grouped by what each actually needs

- [x] **5a. Renames and unit conversions.** `legacy_aliased_metrics` re-expresses live
      observations under the legacy CSV names, wired into `current_metrics` between live
      and archive. Archive 64.0% → **54.4%**; live now serves 31 fields, up from 21.
      *Check:* RELIANCE `returnOnEquity` = 0.0748 (fraction), `mcap_cr` = 1,769,176
      (crore). ✅ A naive alias would have put ROE in at 7.48 — 100x — on a scored input
      for all 1,353 stocks.

      **This does not reach the scorer yet.** `repository.py` runs its own merge and does
      not read `current_metrics`, so it still serves `returnOnEquity` 0.0893 from the
      archive while the view says 0.0748. Step 6 is now the blocker for every gain in
      0/4c/5a: the good merge is the unused one.


- [x] **5b. Drop dead legacy columns.** 19 git-rank-tracker artefacts excluded from the
      archive source in `current_metrics`. Nothing in `apps/web` read any of them.
      Archive 54.4% → **43.6%**; payload 105 → 86 fields.
      *Check:* dead columns absent, `current_price` and the three live `*_vs_staged`
      still present, web build clean, 113 tests pass. ✅
      Listed explicitly, not matched by prefix: `current_%` would have taken
      `current_price`, and `%_vs_staged` the three fields that now come live from
      `ranked_stocks.factors`.
      `archive.csv_rows` still holds every original byte — this only stops the view
      *serving* them.

- [x] **5c-1. Five fields for no fetching at all.** Auditing the block showed it was
      mislabelled: several entries needed no provider.
      `quality_cov` + `valuation_cov` — `scoring.py:189` computed both every run and
      `scoring_jobs` persisted neither, so the dashboard read them from the CSV instead
      of the run that had just produced them. Two lines.
      `shortName` — the archived column is blank for **every** row; `instruments`
      carries the real name. Added `instruments` as a view source.
      `margin_pct` — the same quantity as `profit_margin_pct`, same unit. One alias row.
      `fiftyTwoWeekChangePercent` — a 252-session total return from `price_bars`.
      *Check:* archive 43.6% → **37.5%**, 0 stocks with a blank `shortName`. ✅

- [x] **5c-2a. `operatingMargins` from BSE.** `OPM` was already in the company-header
      payload we fetch per stock and was being discarded. Emitted as
      `operating_margin_pct` (percent), aliased to the legacy fraction. 1,186 of 1,353.
      *Check:* archive 37.5% → **35.6%**. ✅

- [x] **5c-2b. Accounting basis is now recorded.** Investigating the P/B gap showed the
      premise was wrong in an instructive way. Measured over 3,994 archived BSE
      responses: `ConPB`, `ConROE`, `ConNPM` are **never** populated by this endpoint —
      0% consolidated, ~90% standalone, ~10% neither. The "prefer consolidated" logic had
      never once fired, and the docstring asserting it was false.
      `_preferred` now returns `(value, basis)` and each figure is emitted with a
      `<field>_basis` marker. Live results:

      | field | consolidated | standalone |
      | --- | ---: | ---: |
      | `price_to_book`, `roe_pct`, `profit_margin_pct`, `operating_margin_pct` | 0 | ~1,200 each |
      | `trailing_pe` | 1,029 | 202 |
      | `trailing_eps` | 1,105 | 189 |

      The markers immediately found what the fixtures could not: `ConPE`/`ConEPS` *are*
      populated, so **`trailing_pe` is genuinely mixed** — 1,029 stocks ranked on
      consolidated P/E against 202 on standalone, in one cross-sectional percentile.
      Standalone runs higher (avg 46.0 vs 42.8, median 31.3 vs 30.0), so those 202 are
      penalised on the valuation factor for an accounting basis, not for being expensive.
      Not yet corrected in scoring — the marker makes it measurable, which is the
      precondition for deciding what to do.

- [x] **5c-2b-fix. Standalone `trailing_pe` / `trailing_eps` excluded.** The provider
      now emits them flagged `is_valid=false` with a `mixed_accounting_basis` issue, and
      `live_metrics` drops them. The percentile is uniformly consolidated across 1,231
      stocks.
      Scoped to those two fields only: `price_to_book`, `roe_pct`, `profit_margin_pct`
      and `operating_margin_pct` come back 100% standalone, so excluding standalone there
      would delete the field for ~1,200 stocks rather than fix anything.
      *Outcome, better than expected:* the 202 do not lose a P/E. They fall through to
      the archive's Yahoo value, which is consolidated — so they get a right-basis stale
      number instead of a wrong-basis fresh one, with no coverage loss. 773 ranks moved,
      6 by 25+ places, max 59.

      Three bugs surfaced on the way, all the same shape — a rule applied in one of two
      places:
      1. Flagging observations invalid did nothing: `metric_observations` is append-only,
         so earlier valid rows still won. The rule had to live in the view to apply to
         history.
      2. The basis marker was joined per instrument, not per provider, so a BSE
         "standalone" label disqualified NSE's separate reading — 248 dropped instead of
         202.
      3. `NOT (field IN (...) AND basis = 'standalone')` is NULL when there is no marker,
         and `WHERE NULL` excludes the row — silently dropping every unmarked reading.
         Needed `coalesce(basis, '')`.
      Fixed structurally by extracting `live_metrics`, one shared definition of "current
      live observation" that `current_metrics` and `legacy_aliased_metrics` both read.
      Previously each had its own copy, so filtering one left the camelCase name the
      scorer reads still serving all 202.

- [ ] **5c-2c. Financial statements.** What actually remains of the old 5c, ~15 fields:
      `bookValue`, `currentRatio`, `quickRatio`, `debtToEquity`, `enterpriseValue`,
      `enterpriseToEbitda`, `freeCashflow`, `grossMargins`, `operatingMargins`,
      `ebitdaMargins`, `returnOnAssets`, `revenueGrowth`, `earningsGrowth`,
      `earningsQuarterlyGrowth`, `totalCashPerShare`, `dividendYield`.
      These need balance sheet / income statement / cash flow — **not** Yahoo `.info`,
      which was the wrong framing.
      **No source the pipeline can reach.** `.env` has no `ALPHA_VANTAGE_API_KEY`, so
      `packages/pipeline` cannot call Alpha Vantage even though an MCP session can. This
      needs either that key (then ~3 calls x 1,353, rate-limit plan required) or a
      different statements provider. Blocked on a credential, not on code.

- [ ] **5c-3. Analyst fields (likely permanent).** `forwardEps`, `forwardPE`,
      `recommendationMean`, `numberOfAnalystOpinions`, `target{High,Low,Mean}Price`,
      `upside_pct`, `pegRatio`. Only exist where a broker covers the stock — ~890 of
      1,353 at best. Not a fetching problem.

- [x] **5d. Shareholding.** "No free bulk feed identified" was wrong. The source is
      Yahoo's `heldPercentInsiders` / `heldPercentInstitutions` — exactly what the legacy
      pipeline used. New `YahooHoldersProvider`:
      `promoter_pct` 1,341, `institutional_pct` 1,338, plus both legacy fractions, out of
      1,353. Only 10 issues in a full run.
      *Check:* archive 35.6% → **29.2%**; observation passed 50% of the dataset. ✅
      Sanity: RELIANCE 51.8%, TCS 71.8%, HDFCBANK 0.15% — the last is right *because*
      HDFC Bank has no promoter. 891 ranks moved, 11 by 25+ places, max 107.

      Two caveats worth carrying:
      * **Provenance deviation.** Every other provider archives the raw HTTP body through
        `ArchivedHttpClient`; this one archives yfinance's already-parsed dict. Yahoo
        gates holder data behind a rotating cookie+crumb: three BSE shareholding
        endpoints returned HTML, and a hand-rolled httpx crumb flow returned 429 and
        could not even be tested, while yfinance kept working through the same
        throttling. The payload is still archived before this module parses it, one layer
        further from the wire than elsewhere.
      * **`promoter_pct` is an approximation.** Yahoo's "insiders" is not identical to the
        promoter category in an Indian shareholding pattern. The legacy pipeline made the
        same substitution and the archived CSV holds its output, so this changes source
        without changing meaning — but it is not a filing-grade number.

- [ ] **5d-b. `institutions_count`** (1,344, still archive). Yahoo's payload has no count
      of institutions, only a percentage. The legacy pipeline did not fetch it either —
      `shareholding_layer.py` carried it forward from a quarterly cache. Needs a real
      shareholding-pattern filing to do properly.

- [x] **5e. `beta` and `price_chg_pct`.** (`fiftyTwoWeekChangePercent` had already
      landed in 5c-1.) Result: `beta` 1,336 observation / 10 archive, was 1,343 archive;
      `price_chg_pct` 1,353 observation, a field that did not previously exist in the
      view at all.

      **The benchmark had to go somewhere.** Beta regresses a stock against a market
      series, and `price_bars` held no index. Migration `011` adds `^NSEI` (NIFTY 50) to
      `instruments` so it reuses the whole existing path — same Yahoo chart provider,
      same raw archive, same recompute-from-storage property. A separate `index_bars`
      table would have meant duplicating all of it for one series.

      **Two guards, because the obvious one is wrong.** The index is `kind = 'INDEX'`,
      not `exchange = 'INDEX'`: NSE *publishes* NIFTY 50 but the index does not trade
      there, and overloading `exchange` to mean "venue, or else kind-of-thing" never
      raises an error — it just hands a wrong answer to whoever next groups by venue.
      And the filter lives in one place, the `stock_instruments` view, rather than being
      pasted into each call site. Pasting would make the *default* query (`FROM
      instruments`) the wrong one and rely on every future author remembering; this repo
      has already paid three times for a rule that lives in two places.

      Note what `011` deliberately breaks: `WHERE exchange = 'NSE'` no longer excludes
      the index, so every such guard became decorative and the readers moved to the view
      in the same commit. Two queries see past the view on purpose —
      `read_dated_closes_bulk` (beta needs a stock and its index side by side) and
      `write_price_bars` (the index has bars to store). Verified: zero `INDEX` rows reach
      `metric_observations`, `current_metrics` or `ranked_stocks`.

      **`beta` is now a different statistic than the archived one, and a better-founded
      one.** Live is 1-year daily against NIFTY 50; the archived value was Yahoo's, and
      the two correlate only 0.32. The archive is the suspect half: its mean beta across
      the universe is 0.48, when an equal-weighted set of 1,353 stocks measured against
      *its own* market should average near or above 1.0. Yahoo's number was not computed
      against NIFTY 50. Live mean is 1.12. The implementation was checked against an
      independent computation — RELIANCE 0.8961, NESTLEIND 0.5457, ADANIENT 1.6026,
      matching the stored values — and neither `beta` nor `price_chg_pct` appears in
      `scoring.GROUPS`, so both are display-only.

      Rank movement was attributed rather than assumed. The same Yahoo fetch also pulled
      three trading sessions that were missing (2026-08-31, 09-01, 09-02; the newest bar
      had been 08-28), which shift `fiftyDayAverage` and therefore `px_vs_50dma`. Scoring
      the identical data before those bars landed moved **0 ranks**; scoring after them
      moved **1,314, max 313 places**. The movement is three days of market data, not the
      new fields.

      **`price_chg_pct` is defined as a fixed 5-session window, not "since the previous
      run".** The since-last-run definition is what produced `price_chg_pct = 0.0` for
      all 1,353 rows in the shipped snapshot: a run compared its own archived output
      against itself. A session count has no baseline to collapse onto.

      Ceiling: the index carries 247 sessions, so beta uses ~1 year. `min_sessions=120`
      means 17 stocks with shorter histories get no beta rather than a noisy one.

- [x] **6a. Legacy `score` dropped.** The archive served `score` for all 1,353 stocks:
      the old screen's 0-10 output from the May run, sitting in the same payload as the
      engine's 0-100 `final_score` — RELIANCE showed 5.0 and 52.7 side by side. It is a
      *model output*, the thing 004 warned about; it is not in `GROUPS`, so this removed
      a loaded gun rather than a live loop. Verified dead first: absent from `types.ts`,
      unread by any component, not in `GROUPS`. `score_vs_staged` deliberately kept — the
      explorer renders it as "Score change", which is why 007 refused to match by suffix.
      Migration `012`. Archive 32,396 -> 31,043 cells, 47 -> 46 fields.

- [ ] **6. Cut the archive loose — BLOCKED, and here is the number.** Cutting it today
      would blank **13 of the 26 scoring inputs**, taking two groups to zero columns:

      | group | would go blank | of |
      |---|---|---|
      | growth | earningsGrowth, revenueGrowth, earningsQuarterlyGrowth | 3 of 4 (4th never existed) |
      | analyst | upside_pct, recommendationMean, numberOfAnalystOpinions | 3 of 3 |
      | quality | returnOnAssets, debtToEquity, currentRatio | 3 of 5 |
      | valuation | pegRatio, enterpriseToEbitda, dividendYield | 3 of 5 |
      | smart_money | institutions_count | 1 of 5 |

      This is not a step that can be taken until 5c-2c (needs `ALPHA_VANTAGE_API_KEY`),
      5c-3 and 5d-b land. Unblocking 5c-2c alone clears 8 of the 13.

      A second, separate question sits underneath it. The archive plays two different
      roles and only one is blocked:

      * **Sole source** — 26 fields where no live value exists at all. Genuinely blocked.
      * **Gap filler** — ~20 fields where live wins for most stocks and the archive
        quietly supplies the rest (`roe_pct` 1,205 live + 148 archived, `trailingPE`
        1,075 + 231, `priceToBook` 1,180 + 173). Those archived values are served
        looking exactly like fresh ones. `field_origins` is already on every stock in the
        API payload — the web app simply does not read it.

      Staleness matters differently per field: a three-month-old `roe_pct` is roughly
      still true, a three-month-old `trailingPE` is wrong because the price moved.
      Deciding that policy is a product call, not a migration step. Resolved by 6b below:
      keep the values, stop them passing as fresh.

- [x] **6b. Archived values are now marked in the UI.** `field_origins` was already on
      every stock in the API payload and the web app simply ignored it, so an archived
      `roe_pct` rendered identically to one fetched minutes ago. `DetailGroup` rows now
      carry the source field name as an optional third tuple element, and any row whose
      origin is `archive` gets a superscript `arch` marker with the tooltip "From the
      imported CSV snapshot, not fetched this run".

      Verified in the browser against a live stock: 12 fields marked — Forward P/E, PEG
      ratio, ROA, Current ratio, Quick ratio, Free cash flow, Revenue growth, Earnings
      growth, Quarterly earnings, EBITDA margin, Analyst target, Analyst opinions — and,
      importantly, the live ones left alone: Profit margin, Operating margin, Beta,
      50-day average, 200-day average, Delivery average. Beta being unmarked is the check
      that this reads real per-field provenance rather than a hardcoded list: it was
      archive-served until 5e landed earlier today.

      Rows without a third element are never marked, so the 52-week range (two fields in
      one row) and the group scores stay clean. The chosen option deliberately keeps
      every value — the alternative considered was blanking stale price-derived fields
      like `trailingPE`, rejected because a marked value beats a missing one.

## Notes

- The archive is not deleted at the end. It keeps the original bytes and SHA-256 of every
  legacy file, which is what lets us prove a ported metric matches what the CSV said.
- Step 4b is worthless before 1-3: ranking on a universe missing its price and ownership
  factors would produce confidently wrong numbers.
