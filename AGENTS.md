# AGENTS.md

## Data invariants

- Archive provider payloads as immutable JSON before parsing them.
- Never replace an observation without retaining its provider, endpoint, timestamp, and raw
  request ID.
- Treat percentage fields as invalid outside 0–100. Do not clamp impossible values.
- Keep source conflicts visible; use field-level provider priority only for the canonical view.
- Do not commit API keys, raw credentials, DuckDB files, generated exports, or provider caches.

## Project boundaries

- Python ingestion and normalization live in `packages/pipeline/`.
- HTTP routes live in `apps/api/`; do not put parsing logic in routes.
- The dashboard reads JSON from the API. Keep interactive React code in leaf client components.
- PostgreSQL/TimescaleDB is the shared system of record; DuckDB is the local analytical mirror.
- The migrated folder under `legacy/` is a preserved snapshot. Replace it incrementally rather
  than editing it as the new implementation.

## Verification

Run `make test`, `make lint`, and `npm --prefix apps/web run build` after material changes.
