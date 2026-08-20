# Architecture

## Flow

```text
Provider APIs
    -> immutable JSON envelope (request, response, hash, timestamp)
    -> provider observations (value + unit + source + as-of time)
    -> validation issues (never silently discarded)
    -> field-level canonical selection
    -> PostgreSQL/TimescaleDB or local DuckDB
    -> tracked JSON / Parquet snapshots
    -> FastAPI
    -> Next.js
```

## Why both PostgreSQL and DuckDB

PostgreSQL is the system of record for a shared application: it supports users, watchlists,
transactions, constraints, and concurrent writes. The TimescaleDB extension makes append-heavy
price bars easier to partition and query over time. DuckDB is the local analytical mirror: it
has no server, reads Parquet directly, and is convenient for notebooks and model experiments.
It is not the production user database.

## Provider policy

The pipeline does not merge sources with `first non-null wins`. Each provider produces an
observation with `provider`, `endpoint`, `observed_at`, `unit`, and raw value. Validation creates
explicit issues. A field-level preference policy selects a canonical observation only from valid
candidates, using recency as a tie-breaker. This preserves disagreements for review.

Initial priorities:

1. Exchange or company filing for shareholding and issued-share facts.
2. Licensed fundamentals provider for normalized financial statements.
3. Market-data provider for daily prices.
4. Aggregators such as Yahoo as a fallback, with strict unit and range checks.

## Versioning and reproducibility

Each raw response is stored in a content-addressed JSON envelope. Each ranking run records its
input snapshot, model version, and generated timestamp. The normalized dashboard JSON under
`data/snapshots/` is tracked so Git shows field-level changes. Parquet exports are reproducible ML
inputs, not the primary database. Historical CSV files are stored exactly in PostgreSQL under the
`archive` schema, with queryable JSONB rows and a file-level byte archive. Secrets are read from
environment variables and are never written into raw request headers.

The local Compose file follows Timescale's supported PostgreSQL 17 image family. Pin the exact
TimescaleDB image digest before production deployment rather than using the moving local-dev tag.


## Why a value is missing

`select_canonical_metrics` drops a `(ticker, field)` pair when no candidate observation is
valid. That is correct, but it records no reason, and downstream a bare dash reads as a
fetching failure. `find_next_pipeline.coverage` supplies the reason.

| Reason | Meaning | Act on it? |
| --- | --- | --- |
| `recoverable` | A value exists; we have not fetched it | **Yes — the only actionable bucket** |
| `analyst` | Exists only if a broker covers the stock | No. 463 names have no coverage at all |
| `undefined` | The company's own arithmetic leaves it undefined — a loss-maker has no P/E | No |
| `not_applicable` | The concept does not apply — banks do not report current ratios | No |
| `derived` | Our own rank/score bookkeeping, never fetched from anyone | No |
| `unknown` | We lack the figures needed to decide | Feed `trailing_eps` / `profit_margin_pct` through |

`unknown` exists so the classifier never guesses. Deciding whether a blank P/E is
"undefined" needs the company's EPS; the dashboard snapshot does not carry one, and an
earlier version silently defaulted those to `recoverable`, reporting **3,300** actionable
gaps where there are **260**. Unknown cells are excluded from the coverage denominator
rather than assumed either way, and surfaced as `unclassified_gaps`.

On the current snapshot: raw coverage 94.41%, obtainable coverage **98.88%**, 1,027
analyst gaps, 260 recoverable, 73 unclassified.

Exposed as `GET /api/v1/quality/coverage` and `GET /api/v1/quality/gaps/{ticker}`.

### Field naming

Two vocabularies are live: the pipeline emits snake_case (`trailing_pe`), the legacy
dashboard snapshot carries provider camelCase (`trailingPE`). `canonical_field()` maps
both onto one name. Classification is by explicit field name, not prefix — a `current_`
prefix rule would swallow `current_ratio` and `current_price`, which are real metrics.


## Derived metrics

`find_next_pipeline.derivations` computes what a provider will not give us, from data
already held:

- **`trailing_peg`** — Yahoo's `pegRatio` is built from analyst *forward* growth, so it is
  blank for ~94% of the universe: the same names no broker covers. A trailing PEG is a
  different number and must be stored as `provider="derived"`, never as the provider's own.
  `growth_is_fraction` is explicit because 0.11 and 11.0 differ by 100x.
- **`beta_from_closes`** — one index download serves the whole universe. Series are aligned
  on shared dates, since a suspended or newly listed stock has gaps and zipping unaligned
  series compares a Tuesday's return against a Thursday's. Under 120 overlapping sessions
  it returns None rather than a number nobody should trust.

### Price change: one series, two dates

`price_change_pct` takes a single close series and two dates, **not** two prices. This is
a deliberate constraint, because the alternative failed twice in the previous generation:

1. **Same-day baseline.** A run compared its own archived output against itself and
   reported `price_chg_pct = 0.0` for all 1,353 stocks — a column that looks populated and
   means nothing. The shipped snapshot in `data/snapshots/` still carries those zeros.
2. **Split-unadjusted comparison.** Snapshot prices are as-quoted and never restated,
   while history is rewritten after a corporate action. Dividing one by the other turned a
   1:5 split into a phantom crash: one stock was reported at **-79.6% on a day it rose
   1.8%**.

Passing one adjusted series (`price_bars.adjusted_close`) makes both impossible: there is
no second base to mix, and a same-day baseline returns None instead of a fake zero.

## Failure reporting

`find_next_pipeline.diagnostics.FailureTally` folds repeated failures into counted kinds.
A refresh stage previously overwrote its message on each failure, so twenty rate-limit
errors and one schema error reported only whichever landed last — losing both the
frequency and the rarer failure that actually needs fixing. Messages are normalised
(digits, URLs, paths) before grouping, so "timed out after 30s" and "after 45s" fold into
one kind seen twice.
