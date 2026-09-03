# Find Next Stocks

A provenance-first research platform for Indian equities (NSE, 1,353 stocks). Python fetches and
normalizes every provider response, FastAPI exposes the research model, and Next.js renders the
dashboard.

Raw observations are never overwritten by a preferred value: every candidate is retained, and the
canonical choice records which source and validation rule produced it.

**This is a research tool, not investment advice.**

---

## Run it

### 1. Prerequisites

| Need | Version | Notes |
| --- | --- | --- |
| Python | 3.13 | with [`uv`](https://docs.astral.sh/uv/) |
| Node.js | 20.9+ | with npm |
| Docker | any | Colima works on macOS: `colima start` |

### 2. Configure and install

```bash
cp .env.example .env
make install
```

The defaults in `.env.example` work as-is for local development. `POSTGRES_PASSWORD` must match
the password inside `DATABASE_URL` — Docker Compose reads the same `.env` file to *create* the
database that the API then logs into.

### 3. Start the database

```bash
make db-up
```

TimescaleDB comes up on `127.0.0.1:5434`. On the **first** start — and only the first — Docker
runs every file in `infra/db/init/` in numeric order to build the schema and views.

> **Adding a migration later?** The init scripts do not re-run on an existing volume. Apply new
> files yourself, in order:
>
> ```bash
> docker exec -i find-next-stocks-timescaledb-1 psql -U findstocks -d findstocks -v ON_ERROR_STOP=1 < infra/db/init/012_drop_legacy_score.sql
> ```
>
> To rebuild from scratch instead, `make db-down && docker volume rm find-next-stocks_timescale-data && make db-up`. This deletes all stored data.

### 4. Start the app

Two terminals:

```bash
make api
```

```bash
make web
```

| What | Where |
| --- | --- |
| Dashboard | <http://127.0.0.1:3000> |
| API docs | <http://127.0.0.1:8000/docs> |
| Health | <http://127.0.0.1:8000/health> |

A fresh database has no stocks in it yet — run a refresh next.

---

## Refresh provider data

Click **Refresh all data** in the dashboard header, or drive it over HTTP:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/refresh -H 'Content-Type: application/json' -d '{"providers":["yahoo","derived"]}'
```

```bash
curl http://127.0.0.1:8000/api/v1/refresh
```

One refresh runs at a time; starting another while one is active returns the existing job. The
response carries a `job_id` you can poll at `/api/v1/refresh/{job_id}`.

Providers that need no credentials: **nse**, **nse_delivery**, **bse**, **yahoo**,
**yahoo_holders**, **derived**. These are added when their key is in `.env`:

```bash
ALPHA_VANTAGE_API_KEY=your-key
UPSTOX_ANALYTICS_TOKEN=your-token   # or UPSTOX_ACCESS_TOKEN
FMP_API_KEY=your-key
```

A provider without credentials shows as a **skipped stage**, never as a silent success.

Order matters in one place: `derived` computes RSI, beta and moving averages *from stored price
bars*, so it has to run after `yahoo` has written them. It is appended last for that reason.

---

## Everyday commands

```bash
make test
```

```bash
make lint
```

```bash
make db-down
```

---

## Where data comes from

Every value the API serves resolves through the `current_metrics` view, which picks **one**
source per (stock, field) by precedence:

| Priority | Origin | Meaning |
| --- | --- | --- |
| 1 | `ranking` | this run's own scoring output (`rank`, `final_score`, group scores) |
| 2 | `observation` | a live provider fetch, validated |
| 3 | `archive` | the imported legacy CSV snapshot |

Roughly 53% of served cells are live observations, 20% scoring output, and 27% still archive —
mostly financial-statement and analyst fields no free provider covers. Archived values are marked
`arch` in the dashboard so they never pass as freshly fetched. See `docs/csv-migration.md` for
what remains.

**There is no fallback.** If TimescaleDB cannot answer, `/api/v1/dashboard` returns **503** and
`/health` fails. This is deliberate: a tracked JSON snapshot used to sit behind a bare `except`,
and on 2026-09-04 a broken query quietly served six-week-old data as `200 OK` for all 1,353
stocks. An outage you can see beats a success you can't trust.

### Per-field timestamps

`/api/v1/stocks/{ticker}` returns `field_updated_at` and `field_origins` — one entry per field:

```bash
curl -s http://127.0.0.1:8000/api/v1/stocks/RELIANCE | python3 -m json.tool | grep -A3 field_updated_at
```

They are on the single-stock endpoint rather than the 6 MB bulk payload because they are only
read for a stock someone expanded.

> For `archive` fields the timestamp is when the CSV was **imported**, not when the data was
> collected — the underlying figures are older than the date shown. Check `field_origins` before
> trusting a timestamp.

---

## Repository map

| Path | What |
| --- | --- |
| `apps/api/` | FastAPI routes, repository, refresh jobs, scoring jobs |
| `apps/web/` | Next.js dashboard |
| `packages/pipeline/` | provider contracts, raw JSON archive, normalization, validation, scoring |
| `infra/db/init/` | schema and view migrations, applied in numeric order |
| `data/raw/` | immutable provider payloads, archived before parsing |
| `data/exports/` | analytical exports |
| `legacy/` | read-only copy of the original research |
| `docs/architecture.md` | source selection, conflict handling, storage decisions |
| `docs/csv-migration.md` | migration checklist and what is still archive-served |

---

## Ownership sanity rule

Some APIs return ownership as fractions. Yahoo's `heldPercentInsiders=1.07481` means **107.481%**
after conversion — not 1.07481%. Values outside 0–100 are kept in the raw observation log but
rejected from the canonical record. If promoter and institutional buckets together exceed 100.5%,
the lower-priority institutional observation is flagged rather than shown as valid.

## Legacy CSV archive

Legacy CSVs are stored losslessly in PostgreSQL. `archive.csv_files` keeps the original bytes,
SHA-256 digest, ordered header, source path and row count; `archive.csv_rows` stores each ordered
cell array plus a header-keyed JSONB record. The importer deletes source files only after the
database independently verifies bytes, sizes, headers and row counts.

```bash
make archive-csv
```
