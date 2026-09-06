-- Suppress return on equity where the equity is negative.
--
-- Reported from the dashboard: ASIANHOTNR showed an ROE of 3,974% where Screener puts
-- its return near 3%. The archived BSE payload reads `ROE: "3974.28"` and, in the same
-- object, `PB: "-707.41"` -- a negative price-to-book, meaning negative shareholders'
-- equity. A loss divided by negative equity is a positive number, and it is not a
-- return. `bse.py` already refused the P/B for being non-positive while accepting the
-- ROE built on that identical denominator; the inconsistency was ours, not BSE's.
--
-- 14 stocks are in this state, and they are exactly who you would expect: Vodafone Idea,
-- MTNL, GTL Infra, Unitech, Alok Industries, Asian Hotels. Companies with genuinely
-- negative net worth, where no ratio against equity can be recovered.
--
-- SPARC belongs to this set too, at -1989%. An earlier note in this repo cited that
-- figure as proof a real ROE can be enormous, and used it to justify removing the 0-100
-- range check. Removing the check was still right -- it was discarding 413 correct
-- values -- but the example was wrong: SPARC's P/B is -82.65.
--
-- Magnitude is explicitly NOT the test, the sign of the denominator is. KIRIINDUS
-- reports 1,561% against a P/B of 10.08, consistent with its own EPS and book value;
-- NESTLEIND earns ~97%; COALINDIA ~102%. All real, all kept. This is the same lesson as
-- the UEL P/E: a big number is not evidence of an error.
--
-- Absence of a P/B is likewise not evidence. 12 stocks publish an ROE with no book value
-- at all, and 9 of them are perfectly ordinary (FACT 30.7%, KARURVYSYA 21.5%). Only
-- TATACAP, HDBFS and PIRAMALFIN look wrong there, and they need a different test -- see
-- the open item in docs/csv-migration.md rather than a guess here.

CREATE OR REPLACE VIEW roe_unusable AS
SELECT DISTINCT instrument_id
FROM metric_observations
WHERE field = 'book_value_sign'
  AND text_value = 'negative';

COMMENT ON VIEW roe_unusable IS
    'Instruments whose provider reported non-positive shareholders equity, so any ROE '
    'quoted against it is arithmetic rather than a return. Keyed off the book_value_sign '
    'marker bse.py emits; magnitude is never the test.';

CREATE OR REPLACE VIEW live_metrics AS
WITH all_valid AS (
    SELECT o.id, o.instrument_id, o.field, o.numeric_value, o.text_value,
           o.unit, o.provider, o.observed_at
    FROM metric_observations AS o
    WHERE o.is_valid
),
basis AS (
    SELECT DISTINCT ON (instrument_id, provider, metric)
        instrument_id, provider,
        left(field, length(field) - 6) AS metric,
        text_value AS basis
    FROM all_valid
    WHERE right(field, 6) = '_basis'
    ORDER BY instrument_id, provider, metric, observed_at DESC, id DESC
),
eligible AS (
    SELECT a.*
    FROM all_valid AS a
    LEFT JOIN basis AS b
      ON b.instrument_id = a.instrument_id
     AND b.metric = a.field
     AND b.provider = a.provider
    WHERE NOT (
        a.field = ANY (ARRAY['trailing_pe', 'trailing_eps'])
        AND coalesce(b.basis, '') = 'standalone'
    )
      AND NOT (
        a.field = 'trailing_pe'
        AND a.instrument_id IN (SELECT instrument_id FROM pe_unusable)
    )
      -- legacy_aliased_metrics derives returnOnEquity from this row, so dropping it here
      -- removes both spellings at once.
      AND NOT (
        a.field = 'roe_pct'
        AND a.instrument_id IN (SELECT instrument_id FROM roe_unusable)
    )
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY instrument_id, field, observed_at DESC, id DESC;

COMMENT ON VIEW live_metrics IS
    'Newest valid observation per (instrument, field). Standalone-basis trailing_pe / '
    'trailing_eps are removed before the pick, and trailing_pe is dropped for anything '
    'listed in pe_unusable.';

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
WHERE NOT (
    field IN ('trailingPE', 'trailing_pe')
    AND instrument_id IN (SELECT instrument_id FROM pe_unusable)
)
  -- Archived cells never pass through the provider, so the same rule has to be applied
  -- to them here or the CSV simply refills the gap.
  AND NOT (
    field IN ('roe_pct', 'returnOnEquity')
    AND instrument_id IN (SELECT instrument_id FROM roe_unusable)
)
ORDER BY instrument_id, field, priority;

COMMENT ON VIEW current_metrics IS
    'Current value per (instrument, field). Precedence: the latest ranking run, then a '
    'valid live observation, then the newest archived CSV. Single source of truth for '
    'the API and any job that needs "what is this stock''s X".';
