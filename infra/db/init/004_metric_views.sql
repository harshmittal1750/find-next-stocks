-- Canonical views in dependency order. Update definitions here when source policy changes.

CREATE OR REPLACE VIEW stock_instruments AS
    SELECT * FROM instruments WHERE kind = 'EQUITY' AND active;

CREATE OR REPLACE VIEW stock_instrument_symbols AS
SELECT id AS instrument_id, ticker, exchange
FROM stock_instruments
UNION ALL
SELECT aliases.instrument_id, aliases.ticker, aliases.exchange
FROM instrument_ticker_aliases AS aliases
JOIN stock_instruments AS active ON active.id = aliases.instrument_id;

CREATE OR REPLACE VIEW pe_unusable AS
WITH newest_pe AS (
    SELECT DISTINCT ON (instrument_id, provider) instrument_id, provider, numeric_value
    FROM metric_observations
    WHERE field = 'trailing_pe' AND provider IN ('bse', 'nse') AND numeric_value > 0
    ORDER BY instrument_id, provider, observed_at DESC, id DESC
),
by_exchange AS (
    SELECT instrument_id,
           max(numeric_value) FILTER (WHERE provider = 'bse') AS bse_pe,
           max(numeric_value) FILTER (WHERE provider = 'nse') AS nse_pe
    FROM newest_pe GROUP BY instrument_id
),
newest_eps AS (
    SELECT DISTINCT ON (instrument_id) instrument_id, numeric_value
    FROM metric_observations
    WHERE field = 'trailing_eps'
    ORDER BY instrument_id, observed_at DESC, id DESC
)
SELECT instrument_id FROM by_exchange
WHERE bse_pe IS NOT NULL AND nse_pe IS NOT NULL
  AND greatest(bse_pe, nse_pe) / least(bse_pe, nse_pe) > 3.0
UNION
SELECT instrument_id FROM newest_eps WHERE numeric_value < 0;

CREATE OR REPLACE VIEW bse_negative_equity AS
WITH latest_sign AS (
    SELECT DISTINCT ON (instrument_id)
        instrument_id, text_value
    FROM metric_observations
    WHERE field = 'book_value_sign'
      AND provider = 'bse'
      AND is_valid
    ORDER BY instrument_id, observed_at DESC, id DESC
)
SELECT instrument_id
FROM latest_sign
WHERE text_value = 'negative';

CREATE OR REPLACE VIEW roe_bse_conflict AS
WITH latest_bse AS (
    SELECT DISTINCT ON (instrument_id) instrument_id, numeric_value
    FROM metric_observations
    WHERE field = 'roe_pct' AND provider = 'bse' AND is_valid
    ORDER BY instrument_id, observed_at DESC, id DESC
),
latest_yahoo AS (
    SELECT DISTINCT ON (instrument_id) instrument_id, numeric_value
    FROM metric_observations
    WHERE field = 'roe_pct' AND provider = 'yahoo_roe' AND is_valid
    ORDER BY instrument_id, observed_at DESC, id DESC
)
SELECT instrument_id FROM bse_negative_equity
UNION
SELECT bse.instrument_id
FROM latest_bse AS bse
JOIN latest_yahoo AS yahoo USING (instrument_id)
WHERE abs(bse.numeric_value) >= 2
  AND abs(yahoo.numeric_value) >= 2
  AND greatest(abs(bse.numeric_value), abs(yahoo.numeric_value))
      / least(abs(bse.numeric_value), abs(yahoo.numeric_value)) > 10;

CREATE OR REPLACE VIEW roe_unusable AS
SELECT conflict.instrument_id
FROM roe_bse_conflict AS conflict
WHERE NOT EXISTS (
    SELECT 1
    FROM metric_observations AS decision
    WHERE decision.instrument_id = conflict.instrument_id
      AND decision.field = 'roe_pct'
      AND decision.provider IN ('screener', 'upstox_fundamentals')
      AND decision.is_valid
);

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
      AND right(a.field, 6) <> '_basis'
      AND NOT (
        a.provider = 'upstox_fundamentals'
        AND a.field NOT IN ('roe_pct', 'roce_pct')
    )
      AND NOT (
        a.field = 'trailing_pe'
        AND a.instrument_id IN (SELECT instrument_id FROM pe_unusable)
    )
      AND NOT (
        a.field = 'roe_pct'
        AND a.instrument_id IN (SELECT instrument_id FROM roe_bse_conflict)
        AND a.provider NOT IN ('screener', 'upstox_fundamentals')
    )
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY
    instrument_id,
    field,
    CASE
        WHEN field IN ('roe_pct', 'roce_pct') AND provider = 'screener' THEN 0
        WHEN field IN ('roe_pct', 'roce_pct')
             AND provider = 'upstox_fundamentals' THEN 1
        WHEN field = 'roe_pct' AND provider = 'bse' THEN 2
        WHEN field = 'roe_pct' AND provider = 'yahoo_roe' THEN 3
        WHEN field = 'roce_pct' AND provider = 'screener' THEN 1
        ELSE 4
    END,
    observed_at DESC,
    id DESC;

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
        ('current_price',        'currentPrice',       1.0),
        ('market_cap',           'marketCap',          1.0),
        ('fifty_two_week_high',  'fiftyTwoWeekHigh',   1.0),
        ('fifty_two_week_low',   'fiftyTwoWeekLow',    1.0),
        ('price_to_book',        'priceToBook',        1.0),
        ('trailing_pe',          'trailingPE',         1.0),
        ('trailing_eps',         'trailingEps',        1.0),
        ('roe_pct',              'returnOnEquity',     0.01),
        ('profit_margin_pct',    'profitMargins',      0.01),
        ('profit_margin_pct',    'margin_pct',         1.0),
        ('operating_margin_pct', 'operatingMargins',   0.01),
        ('market_cap',           'mcap_cr',             0.0000001)
) AS mapping(live_field, legacy_field, factor)
  ON mapping.live_field = live.field
WHERE live.numeric_value IS NOT NULL;

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
    SELECT DISTINCT ON (symbols.instrument_id, kv.key)
        symbols.instrument_id,
        kv.key AS field,
        CASE WHEN kv.value #>> '{}' ~ '^-?[0-9]+\.?[0-9]*([eE][-+]?[0-9]+)?$'
             THEN (kv.value #>> '{}')::numeric END AS numeric_value,
        NULLIF(kv.value #>> '{}', '') AS text_value,
        f.source_modified_at AS observed_at
    FROM newest_files AS f
    JOIN archive.csv_rows AS r
      ON r.source_path = f.source_path AND r.content_sha256 = f.content_sha256
    CROSS JOIN LATERAL jsonb_each(r.record) AS kv(key, value)
    JOIN stock_instrument_symbols AS symbols
      ON symbols.ticker = upper(btrim(r.record ->> 'ticker'))
    WHERE r.record ? 'ticker'
      AND kv.key <> 'ticker'
      AND kv.key <> ALL (ARRAY[
          'current_rank', 'current_score', 'current_score_status',
          'staged_rank', 'staged_score', 'staged_score_status',
          'pushed_rank', 'pushed_score', 'pushed_score_status',
          'rank_vs_pushed', 'score_vs_pushed',
          'staged_rank_vs_pushed', 'staged_score_vs_pushed',
          'movement_vs_pushed', 'staged_movement_vs_pushed',
          'ownership_score', 'shareholding_score',
          'reasons', 'shareholding_reasons',
          'score'
      ])
      AND NULLIF(kv.value #>> '{}', '') IS NOT NULL
    ORDER BY symbols.instrument_id, kv.key, f.source_modified_at DESC
),
identity AS (
    SELECT id AS instrument_id, 'shortName'::text AS field,
           NULL::numeric AS numeric_value, company_name AS text_value,
           updated_at AS observed_at
    FROM stock_instruments
    WHERE company_name IS NOT NULL AND btrim(company_name) <> ''
),
all_sources AS (
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
    FROM live_metrics
    UNION ALL
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
WHERE instrument_id IN (SELECT id FROM stock_instruments)
  AND NOT (
      field IN ('trailingPE', 'trailing_pe')
      AND instrument_id IN (SELECT instrument_id FROM pe_unusable)
  )
  AND NOT (
      field IN ('roe_pct', 'returnOnEquity')
      AND instrument_id IN (SELECT instrument_id FROM roe_unusable)
  )
ORDER BY instrument_id, field, priority;
