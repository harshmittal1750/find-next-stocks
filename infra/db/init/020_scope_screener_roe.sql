-- A stopped bulk experiment left valid Screener observations for 21 ordinary stocks.
-- Keep them for audit, but do not give a random subset different canonical precedence.
-- Screener ROE adjudicates only rows selected by the universe-wide conflict view.

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
      AND NOT (
        a.field = 'roe_pct'
        AND a.provider = 'bse'
        AND a.instrument_id IN (SELECT instrument_id FROM bse_negative_equity)
    )
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY
    instrument_id,
    field,
    CASE
        WHEN field = 'roe_pct' AND provider = 'screener'
             AND instrument_id IN (SELECT instrument_id FROM roe_bse_conflict) THEN 0
        WHEN field = 'roe_pct' AND provider = 'bse'
             AND instrument_id NOT IN (SELECT instrument_id FROM roe_bse_conflict) THEN 1
        WHEN field = 'roe_pct' AND provider = 'yahoo_roe' THEN 2
        WHEN field = 'roe_pct' AND provider = 'bse' THEN 3
        WHEN field = 'roe_pct' AND provider = 'screener' THEN 4
        WHEN field = 'roce_pct' AND provider = 'screener' THEN 0
        ELSE 5
    END,
    observed_at DESC,
    id DESC;

COMMENT ON VIEW live_metrics IS
    'Canonical metrics after validation. ROE uses Screener only for automatically '
    'reviewed conflicts, BSE for non-conflicts, Yahoo annual statements as fallback, '
    'and retains all other source candidates solely for audit.';
