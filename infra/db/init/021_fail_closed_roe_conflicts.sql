-- A disagreement is not resolved merely by choosing the less extreme source.
--
-- If BSE and Yahoo statements trigger roe_bse_conflict, neither enters scoring until
-- Screener supplies the third-source decision. TATACAP has that evidence and remains
-- populated. The other currently blocked reviews stay blank, and coverage handles the
-- missing input. This is safer than scoring a value we already know is disputed.

CREATE OR REPLACE VIEW roe_unusable AS
SELECT conflict.instrument_id
FROM roe_bse_conflict AS conflict
WHERE NOT EXISTS (
    SELECT 1
    FROM metric_observations AS decision
    WHERE decision.instrument_id = conflict.instrument_id
      AND decision.field = 'roe_pct'
      AND decision.provider = 'screener'
      AND decision.is_valid
);

COMMENT ON VIEW roe_unusable IS
    'Cross-source ROE conflicts with no valid Screener decision. Current_metrics uses '
    'this to prevent the legacy archive from silently refilling a disputed value.';

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
        AND a.instrument_id IN (SELECT instrument_id FROM roe_bse_conflict)
        AND a.provider <> 'screener'
    )
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY
    instrument_id,
    field,
    CASE
        WHEN field = 'roe_pct' AND provider = 'screener' THEN 0
        WHEN field = 'roe_pct' AND provider = 'bse' THEN 1
        WHEN field = 'roe_pct' AND provider = 'yahoo_roe' THEN 2
        WHEN field = 'roce_pct' AND provider = 'screener' THEN 0
        ELSE 3
    END,
    observed_at DESC,
    id DESC;

COMMENT ON VIEW live_metrics IS
    'Canonical metrics after validation. Non-conflicting ROE prefers BSE then Yahoo '
    'statements. A conflict admits only Screener; without it the field fails closed and '
    'coverage, not an unverified number, controls scoring.';
