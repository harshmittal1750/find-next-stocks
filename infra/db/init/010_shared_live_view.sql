-- One definition of "the current live observation", shared by every consumer.
--
-- This existed twice: current_metrics had its own `live` CTE and legacy_aliased_metrics
-- had another. Adding the mixed-basis filter to one left the other serving all 202
-- standalone readings under the camelCase name the scorer actually reads — so the
-- exclusion appeared to work and changed nothing. Same shape as the LIVE_FIELD_MAP
-- allow-list and the repository merge: a rule in two places is a rule that drifts.

CREATE OR REPLACE VIEW live_metrics AS
WITH live_raw AS (
    SELECT DISTINCT ON (o.instrument_id, o.field)
        o.instrument_id, o.field, o.numeric_value, o.text_value,
        o.unit, o.provider, o.observed_at
    FROM metric_observations AS o
    WHERE o.is_valid
    ORDER BY o.instrument_id, o.field, o.observed_at DESC, o.id DESC
),
-- Current accounting basis per metric, from the `<field>_basis` markers the BSE
-- provider emits alongside each figure.
basis AS (
    SELECT instrument_id, provider,
           left(field, length(field) - 6) AS metric, text_value AS basis
    FROM live_raw
    WHERE right(field, 6) = '_basis'
),
live AS (
    -- Drop readings whose basis differs from the rest of the universe. trailing_pe and
    -- trailing_eps come back mixed (1,029 consolidated vs 202 standalone), and a
    -- cross-sectional percentile over both compares different measurements — standalone
    -- P/E runs higher, so those names would score as expensive for their accounting
    -- basis rather than their price. Coverage-adjustment absorbs the gap.
    --
    -- Enforced here, not only in the provider: metric_observations is append-only, so
    -- flagging new rows invalid leaves earlier valid ones winning. The view is the only
    -- place the exclusion applies to history as well as to the next run.
    SELECT l.*
    FROM live_raw AS l
    LEFT JOIN basis AS b
      ON b.instrument_id = l.instrument_id
     AND b.metric = l.field
     -- Matched on provider too: the marker describes that provider's own reading. A
     -- BSE "standalone" label must not disqualify NSE's separate value for the same
     -- stock, which is what happened when the join ignored provider (248 dropped
     -- instead of 202).
     AND b.provider = l.provider
    -- coalesce, not a bare comparison: with no marker b.basis is NULL, and
    -- NOT (TRUE AND NULL) is NULL, which WHERE treats as false. That silently dropped
    -- every unmarked reading (NSE's, here) instead of only the standalone ones.
    WHERE NOT (
        l.field IN ('trailing_pe', 'trailing_eps')
        AND coalesce(b.basis, '') = 'standalone'
    )
)
SELECT * FROM live;

COMMENT ON VIEW live_metrics IS
    'Current valid observation per (instrument, field), with mixed-accounting-basis '
    'readings excluded. The single live source for current_metrics and legacy aliases.';

CREATE OR REPLACE VIEW legacy_aliased_metrics AS
SELECT
    live.instrument_id,
    mapping.legacy_field AS field,
    live.numeric_value * mapping.factor AS numeric_value,
    live.text_value,
    live.provider,
    live.observed_at
FROM live_metrics AS live
JOIN (
    VALUES
        -- renames: identical units
        ('current_price',       'currentPrice',      1.0),
        ('market_cap',          'marketCap',         1.0),
        ('fifty_two_week_high', 'fiftyTwoWeekHigh',  1.0),
        ('fifty_two_week_low',  'fiftyTwoWeekLow',   1.0),
        ('price_to_book',       'priceToBook',       1.0),
        ('trailing_pe',         'trailingPE',        1.0),
        ('trailing_eps',        'trailingEps',       1.0),
        -- conversions: the legacy column is in a different unit
        ('roe_pct',             'returnOnEquity',    0.01),        -- percent -> fraction
        ('profit_margin_pct',   'profitMargins',     0.01),        -- percent -> fraction
        ('profit_margin_pct',   'margin_pct',        1.0),         -- same quantity, percent
        ('operating_margin_pct','operatingMargins',  0.01),        -- percent -> fraction
        ('market_cap',          'mcap_cr',           0.0000001)    -- rupees  -> crore
) AS mapping(live_field, legacy_field, factor)
  ON mapping.live_field = live.field
WHERE live.numeric_value IS NOT NULL;

COMMENT ON VIEW legacy_aliased_metrics IS
    'Live observations re-expressed under the legacy CSV field names and units, so '
    'downstream code asking for marketCap or returnOnEquity gets fresh data instead of '
    'the archive. Renames carry factor 1.0; conversions carry the real factor.';

-- Rebuilt in 009: excludes mixed-accounting-basis readings from the live source.
-- Kept here as the current full definition of current_metrics.
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
    FROM instruments
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
