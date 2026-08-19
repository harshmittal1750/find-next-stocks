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
