-- Add the scoring engine's own output as the top-precedence source.
--
-- `rank`, `final_score`, `data_cov` and the seven `g_*` group scores are computed here,
-- by scoring.py, and written to ranked_stocks on every run. Until now current_metrics
-- still resolved them from the archived CSV, so the dashboard showed July's ranks while
-- the engine's own numbers sat in a table beside them.
--
-- Restructured from nested FULL OUTER JOINs to UNION ALL + DISTINCT ON. Three sources
-- was the point where the join form stopped being readable, and adding a fourth is now
-- one more branch with a priority number rather than another join arm.
--
-- These are model *outputs*. Nothing that feeds scoring may read them, or a run would
-- take its own previous answer as an input. scoring.py reads only fundamentals, so the
-- loop does not exist today — keep it that way.

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
live AS (
    SELECT DISTINCT ON (o.instrument_id, o.field)
        o.instrument_id, o.field, o.numeric_value, o.text_value,
        o.unit, o.provider, o.observed_at
    FROM metric_observations AS o
    WHERE o.is_valid
    ORDER BY o.instrument_id, o.field, o.observed_at DESC, o.id DESC
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
    JOIN instruments AS i ON i.ticker = upper(btrim(r.record ->> 'ticker'))
    WHERE r.record ? 'ticker'
      AND kv.key <> 'ticker'
      AND NULLIF(kv.value #>> '{}', '') IS NOT NULL
    ORDER BY i.id, kv.key, f.source_modified_at DESC
),
all_sources AS (
    SELECT instrument_id, field, numeric_value, text_value,
           NULL::text AS unit, 'scoring'::text AS provider, observed_at,
           'ranking'::text AS origin, 1 AS priority
    FROM ranked
    UNION ALL
    SELECT instrument_id, field, numeric_value, text_value,
           unit, provider, observed_at, 'observation', 2
    FROM live
    UNION ALL
    SELECT instrument_id, field, numeric_value, text_value,
           NULL, 'legacy_csv', observed_at, 'archive', 3
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
