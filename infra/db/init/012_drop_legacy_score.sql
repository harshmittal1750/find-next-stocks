-- Stop serving the legacy `score` column.
--
-- The archive carries `score` for all 1,353 stocks: the old screen's 0-10 output from
-- the May run. The engine writes `final_score` on a 0-100 scale, so RELIANCE was being
-- served `score` 5.0 and `final_score` 52.7 side by side in the same payload — two
-- numbers, two scales, two dates, one obvious way to confuse them.
--
-- It is a *model output*, which is the specific thing 004 warned about: nothing that
-- feeds scoring may read scoring's own answer back in. `score` is not in GROUPS today,
-- so no loop exists — this removes the loaded gun rather than fixing a live bug.
--
-- Verified dead before removal: absent from apps/web `types.ts`, unread by any
-- component, and not in `scoring.GROUPS`. Note `score_vs_staged` is NOT dropped — the
-- explorer renders it as "Score change", which is exactly why 007 refused to match
-- these by suffix.
--
-- This is the archive-side half of step 6. The rest of step 6 is blocked, and the number
-- is worth writing down: cutting the archive today would blank 13 of the 26 scoring
-- inputs, taking the `growth` and `analyst` groups to zero columns each.

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
          'reasons', 'shareholding_reasons',
          -- the old screen's 0-10 score; the engine's own number is final_score
          'score'
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
