# Find Next Stocks

Indian-equity research dashboard: Python ingestion → PostgreSQL/TimescaleDB → FastAPI →
Next.js. Provider observations and raw payloads retain their provenance; canonical views
choose which values the dashboard and scoring engine use.

## Run locally

Requires Python 3.13, `uv`, Node.js 20.9+, npm, and Docker.

```bash
cp .env.example .env
make install
make db-up
make api                 # terminal 1
make web                 # terminal 2
```

`POSTGRES_PASSWORD` must match the password in `DATABASE_URL`. The database listens on
`127.0.0.1:5434`. Dashboard: <http://127.0.0.1:3000>; API docs:
<http://127.0.0.1:8000/docs>. Set `API_BASE_URL` to change the dashboard's API origin.

Docker applies `infra/db/init/*.sql` in filename order only when initializing an empty
volume. The four files contain base tables, the CSV archive, instrument setup, and current
metric views. Superseded view migrations have been consolidated into these definitions.
For an existing database, apply changed SQL explicitly; restarting Docker does not apply it:

```bash
for sql in infra/db/init/*.sql; do
  docker compose exec -T timescaledb psql -U findstocks -d findstocks -v ON_ERROR_STOP=1 < "$sql" || break
done
```

Use your configured database/user if different. Back up an existing database before schema
changes. `make db-down` stops the database without deleting its volume.

## Refresh data

A fresh database needs data. Use **Refresh all data** in the dashboard, or:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/refresh \
  -H 'Content-Type: application/json' -d '{"providers":["yahoo","derived"]}'
curl http://127.0.0.1:8000/api/v1/refresh
```

Only one refresh runs at a time. Poll `/api/v1/refresh/{job_id}` for a specific job.
`derived` runs after Yahoo because it uses stored price bars.

Public providers: `nse`, `nse_delivery`, `bse`, `yahoo`, `yahoo_holders`, `yahoo_roe`,
`screener`, and `derived`. Optional `.env` credentials enable additional providers:

- `ALPHA_VANTAGE_API_KEY`
- `UPSTOX_ANALYTICS_TOKEN` (quotes and company fundamentals)
- `FMP_API_KEY`

Missing credentials produce a skipped stage; provider failures are reported in job results.

## Data rules

- Archive immutable provider JSON before parsing. Retain source, endpoint, timestamp, raw
  request ID, and validation issues. Source conflicts remain available for audit.
- `current_metrics` is shared by the API and scoring: latest ranking output, valid live
  observations, then imported CSV values. `stock_instruments` includes active equities;
  index bars and inactive instruments remain available for historical analysis.
- Screener has first priority for reported ROE/ROCE, followed by Upstox Fundamentals.
  Upstox's other key ratios stay audit-only. Conflicting ROE and unusable P/E are blocked
  across both live and archive sources. ROCE never substitutes for ROE.
- Ownership outside 0–100% is invalid, never clamped. Yahoo insider ownership is only an
  approximation of promoter ownership, and its holder payload is archived after yfinance
  has decoded the HTTP response.
- Archived fields carry an `arch` marker. `/api/v1/stocks/{ticker}` exposes `field_origins`
  and `field_updated_at`; archive timestamps describe import time, not original collection.
- Database failures return an explicit error; the API does not serve an old JSON fallback.
- Coverage distinguishes recoverable, analyst, undefined, not-applicable, derived, and
  unknown gaps. Inspect `/api/v1/quality/coverage` and `/api/v1/quality/gaps/{ticker}`.

## Repository

| Path | Purpose |
| --- | --- |
| `apps/api/` | HTTP routes, database reads, refresh and scoring jobs |
| `apps/web/` | Dashboard; interactive React stays in leaf client components |
| `packages/pipeline/` | Providers, archiving, normalization, validation, scoring |
| `infra/db/init/` | Database tables, instrument setup, and canonical views |
| `data/raw/` | Immutable JSON envelopes; ignored by Git |
| `data/warehouse/` | Local DuckDB analytical mirror; ignored by Git |
| `data/exports/` | Generated analytical exports; ignored by Git |
| `legacy/` | Preserved original research snapshot |
| [docs/todo.md](docs/todo.md) | Remaining data work |

`make archive-csv` imports legacy CSV bytes, hashes, headers, and ordered rows into
PostgreSQL's `archive` schema. Source files are deleted only after database verification.
Keep the archive while live providers still leave scoring inputs missing.

## Verify

```bash
make test
make lint
npm --prefix apps/web run build
```
