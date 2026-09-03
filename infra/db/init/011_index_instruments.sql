-- Make room for a non-equity row in `instruments`, without letting it leak.
--
-- Beta needs a market series to regress against, and NIFTY 50 has to live somewhere.
-- Putting it in `price_bars` reuses the whole ingest path — the same Yahoo chart
-- provider, the same raw archive, the same replay-from-storage property that lets an
-- indicator be recomputed without re-fetching. A separate `index_bars` table would have
-- meant duplicating all of it for one series.
--
-- The cost is that `instruments` stops being "the list of stocks". Two guards, because
-- the obvious one is wrong:
--
--   * `kind`, not `exchange = 'INDEX'`. NSE *publishes* NIFTY 50 but the index does not
--     trade, so the row is honestly exchange='NSE'. Overloading `exchange` to mean
--     "venue, or else kind-of-thing" would never raise an error — it would just hand a
--     wrong answer to whoever next groups by venue.
--   * `stock_instruments`, so the filter has one home. Adding `WHERE kind = 'EQUITY'` to
--     each call site would make the *default* query (`FROM instruments`) the wrong one
--     and rely on every future author remembering. This repo has already paid for that
--     shape three times — LIVE_FIELD_MAP drifting from the view, the repository merge
--     drifting from the view, the duplicated `live` CTE.
--
-- Note what this migration deliberately breaks: `WHERE exchange = 'NSE'` no longer
-- excludes the index. Any such guard still standing after this file is decorative, which
-- is why the readers move to the view in the same commit.

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'EQUITY';

ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_kind_check;
ALTER TABLE instruments
    ADD CONSTRAINT instruments_kind_check CHECK (kind IN ('EQUITY', 'INDEX'));

COMMENT ON COLUMN instruments.kind IS
    'What this row is, as distinct from where it trades. EQUITY rows are the scoreable '
    'universe; INDEX rows carry a benchmark series for beta and nothing else.';

CREATE OR REPLACE VIEW stock_instruments AS
    SELECT * FROM instruments WHERE kind = 'EQUITY';

COMMENT ON VIEW stock_instruments IS
    'The scoreable equity universe. Anything that ranks, scores, counts coverage or '
    'renders the dashboard reads this, never `instruments` directly — the base table '
    'also holds benchmark index rows. read_closes_bulk is the one deliberate exception: '
    'beta needs a stock and its index side by side.';

-- The benchmark itself. `^NSEI` is Yahoo's symbol for NIFTY 50; the chart provider takes
-- it verbatim rather than appending the `.NS` suffix it gives equities.
INSERT INTO instruments (ticker, exchange, kind, company_name, currency)
VALUES ('^NSEI', 'NSE', 'INDEX', 'NIFTY 50', 'INR')
ON CONFLICT (ticker, exchange) DO UPDATE SET kind = 'INDEX', updated_at = now();

-- Recreated verbatim from 010 with one change: the identity CTE and the archive join now
-- read `stock_instruments`. No view below touches the base table.
CREATE OR REPLACE VIEW current_metrics AS
WITH latest_run AS (
    SELECT id FROM ranking_runs ORDER BY created_at DESC LIMIT 1
),
ranked AS (
    SELECT
        rs.instrument_id,
        kv.field,
        kv.numeric_value,
        kv.text_value,
        r.created_at AS observed_at
    FROM ranked_stocks AS rs
    JOIN ranking_runs AS r ON r.id = rs.run_id
    CROSS JOIN LATERAL (
        VALUES
            ('rank',         rs.rank::numeric,          NULL::text),
            ('final_score',  rs.score,                  NULL),
            ('data_cov',     rs.data_coverage,          NULL),
            ('score_status', NULL::numeric,             rs.score_status)
    ) AS kv(field, numeric_value, text_value)
    WHERE rs.run_id = (SELECT id FROM latest_run)
      AND (kv.numeric_value IS NOT NULL OR kv.text_value IS NOT NULL)

    UNION ALL

    -- The per-group scores and movement columns live in the factors JSON blob.
    SELECT
        rs.instrument_id,
        f.key AS field,
        CASE WHEN jsonb_typeof(f.value) = 'number' THEN (f.value #>> '{}')::numeric END,
        CASE WHEN jsonb_typeof(f.value) = 'string' THEN f.value #>> '{}' END,
        r.created_at
    FROM ranked_stocks AS rs
    JOIN ranking_runs AS r ON r.id = rs.run_id
    CROSS JOIN LATERAL jsonb_each(rs.factors) AS f(key, value)
    WHERE rs.run_id = (SELECT id FROM latest_run)
      AND jsonb_typeof(f.value) IN ('number', 'string')
),
newest_files AS (
    SELECT DISTINCT ON (source_path) source_path, content_sha256, source_modified_at
    FROM archive.csv_files
    ORDER BY source_path, source_modified_at DESC, imported_at DESC
),
archived AS (
    SELECT DISTINCT ON (i.id, kv.key)
        i.id AS instrument_id,
        kv.key AS field,
        CASE WHEN kv.value #>> '{}' ~ '^-?[0-9]+\.?[0-9]*([eE][-+]?[0-9]+)?$'
             THEN (kv.value #>> '{}')::numeric END AS numeric_value,
        NULLIF(kv.value #>> '{}', '') AS text_value,
        f.source_modified_at AS observed_at
    FROM newest_files AS f
    JOIN archive.csv_rows AS r
      ON r.source_path = f.source_path AND r.content_sha256 = f.content_sha256
    CROSS JOIN LATERAL jsonb_each(r.record) AS kv(key, value)
    JOIN stock_instruments AS i ON i.ticker = upper(btrim(r.record ->> 'ticker'))
    WHERE r.record ? 'ticker'
      AND kv.key <> 'ticker'
      AND kv.key <> ALL (ARRAY[
          -- Dead columns from the old git-based rank tracker, which compared the
          -- working CSV against Git's staged and last-pushed copies. ranked_stocks
          -- replaced it: rank_vs_staged / score_vs_staged / movement_vs_staged now come
          -- from the scoring run itself. Nothing in apps/web reads any of these.
          --
          -- Listed explicitly rather than matched by prefix. "current_%" would take
          -- current_price with it, and "%_vs_staged" would take the three live ones —
          -- the same collision that made current_ratio look like bookkeeping.
          'current_rank', 'current_score', 'current_score_status',
          'staged_rank', 'staged_score', 'staged_score_status',
          'pushed_rank', 'pushed_score', 'pushed_score_status',
          'rank_vs_pushed', 'score_vs_pushed',
          'staged_rank_vs_pushed', 'staged_score_vs_pushed',
          'movement_vs_pushed', 'staged_movement_vs_pushed',
          'ownership_score', 'shareholding_score',
          'reasons', 'shareholding_reasons'
      ])
      AND NULLIF(kv.value #>> '{}', '') IS NOT NULL
    ORDER BY i.id, kv.key, f.source_modified_at DESC
),
identity AS (
    -- The archived shortName column is blank for every row; instruments has the name.
    SELECT id AS instrument_id, 'shortName'::text AS field,
           NULL::numeric AS numeric_value, company_name AS text_value,
           updated_at AS observed_at
    FROM stock_instruments
    WHERE company_name IS NOT NULL AND btrim(company_name) <> ''
),
all_sources AS (
    -- First branch of the UNION, so it names the columns for the whole set.
    SELECT instrument_id, field, numeric_value, text_value,
           NULL::text AS unit, 'instruments'::text AS provider, observed_at,
           'observation'::text AS origin, 2 AS priority
    FROM identity
    UNION ALL
    SELECT instrument_id, field, numeric_value, text_value,
           NULL::text AS unit, 'scoring'::text AS provider, observed_at,
           'ranking'::text AS origin, 1 AS priority
    FROM ranked
    UNION ALL
    SELECT instrument_id, field, numeric_value, text_value,
           unit, provider, observed_at, 'observation', 2
    FROM live_metrics AS live
    UNION ALL
    -- Live values re-expressed under the legacy names the CSV used. Ranks below a real
    -- observation of the legacy name, above the archive.
    SELECT instrument_id, field, numeric_value, text_value,
           NULL, provider, observed_at, 'observation', 3
    FROM legacy_aliased_metrics
    UNION ALL
    SELECT instrument_id, field, numeric_value, text_value,
           NULL, 'legacy_csv', observed_at, 'archive', 4
    FROM archived
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at, origin
FROM all_sources
ORDER BY instrument_id, field, priority;

COMMENT ON VIEW current_metrics IS
    'Current value per (instrument, field). Precedence: the latest ranking run, then a '
    'valid live observation, then the newest archived CSV. Single source of truth for '
    'the API and any job that needs "what is this stock''s X".';
