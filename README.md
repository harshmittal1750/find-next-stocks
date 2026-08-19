# Find Next Stocks

A provenance-first research platform for Indian equities. Python fetches and normalizes every
provider response, FastAPI exposes the research model, and Next.js renders the dashboard. Raw
observations are never overwritten by a preferred value: every candidate is retained and the
canonical choice records which source and validation rule produced it.

This is a research tool, not investment advice.

## Data layout

| Purpose | Storage |
| --- | --- |
| Historical prices | PostgreSQL + TimescaleDB; DuckDB for local work |
| Fundamentals and watchlists | PostgreSQL |
| Raw API responses | Immutable JSON envelopes under `data/raw/` |
| Reviewable dashboard state | Tracked JSON under `data/snapshots/` |
| Reviewable exports and sharing | JSON snapshots under `data/snapshots/` |
| ML and analytical datasets | Parquet under `data/exports/` |
| Migrated legacy tabular data | PostgreSQL `archive.csv_files` and `archive.csv_rows` |

## Start locally

Requirements: Python 3.13, `uv`, Node.js 20.9+, npm, and optionally Docker.

```bash
cp .env.example .env
make install
```

Run the two development processes in separate terminals:

```bash
make api
make web
```

Open <http://127.0.0.1:3000>. The API documentation is at
<http://127.0.0.1:8000/docs>.

## Refresh provider data

Use **Refresh all data** in the dashboard header to start one exclusive backend refresh job. The
panel polls the job status and reports progress for database preparation, each provider, and
dashboard publication. Starting another refresh while one is active returns the existing job.

The refresh uses NSE and Yahoo without credentials. Upstox and Alpha Vantage are enabled when
their credentials are present in `.env`:

```bash
UPSTOX_ANALYTICS_TOKEN=your-token
# Or: UPSTOX_ACCESS_TOKEN=your-oauth-access-token
ALPHA_VANTAGE_API_KEY=your-key
```

Provider payloads are written to immutable JSON envelopes under `data/raw/`; normalized
observations and their provenance are appended to PostgreSQL. The dashboard reads the latest
valid observation for each field. An unavailable or unsupported provider remains visible as a
skipped stage rather than being silently treated as refreshed.

The same workflow can be controlled over HTTP:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/refresh \
  -H 'Content-Type: application/json' \
  -d '{"providers":["nse","yahoo"]}'
curl http://127.0.0.1:8000/api/v1/refresh
curl http://127.0.0.1:8000/api/v1/refresh/JOB_ID
```

For the shared PostgreSQL/TimescaleDB backend:

```bash
make db-up
```

Then set `STORAGE_BACKEND=postgres` in `.env`. The local DuckDB workflow remains available for
analysis and offline development.

## Repository map

- `apps/api/` — FastAPI routes and response models.
- `apps/web/` — Next.js dashboard.
- `packages/pipeline/` — provider contracts, raw JSON archive, normalization, validation, and
  local analytical storage.
- `infra/db/init/` — PostgreSQL/TimescaleDB schema.
- `data/` — generated raw envelopes, exports, and local DuckDB files.
- `data/snapshots/` — normalized JSON tracked in Git so metric changes are reviewable.
- `data/migrations/` — tracked database migration manifests with source hashes and row counts.
- `legacy/` — read-only snapshot of the original research, excluding caches and virtualenvs.
- `docs/architecture.md` — source selection, conflict handling, and storage decisions.

## Ownership sanity rule

Some APIs return ownership as fractions. For example, Yahoo's `heldPercentInsiders=1.07481`
means 107.481% after conversion; it does **not** mean 1.07481%. Values outside 0–100 are retained
in the raw observation log but are rejected from the canonical stock record. If promoter and
institutional buckets exceed 100.5% together, the lower-priority institutional observation is
flagged instead of being shown as valid.

## Legacy CSV archive

Legacy CSV data is stored losslessly in PostgreSQL. `archive.csv_files` retains the original file
bytes, SHA-256 digest, ordered header, source path, and row count. `archive.csv_rows` stores each
ordered cell array plus a header-keyed JSONB record for querying. The migration deletes source
files only after the database independently verifies its stored bytes, sizes, headers, and row
counts.

```bash
make archive-csv
```
