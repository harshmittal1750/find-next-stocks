-- Resolve ROE from a real current provider instead of deleting a bad value.
--
-- 017 used an archived CSV value only to identify BSE readings that were wrong by two
-- orders of magnitude, then blanked the field. That prevented scoring corruption but
-- did not repair the data. This migration replaces that stop-gap with a field-level,
-- universe-wide source policy:
--
--   1. Screener's reported consolidated ROE
--   2. ROE calculated from Yahoo annual net income / average shareholders' equity
--   3. BSE's header ratio, provided BSE does not report non-positive equity
--
-- All candidates remain in metric_observations. Only the canonical view has priority,
-- so disagreements remain queryable and a missing preferred source falls through.

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

COMMENT ON VIEW bse_negative_equity IS
    'Latest BSE book-value sign is negative. Positive markers supersede historical '
    'negative markers when a company recovers.';

-- Compatibility guard consumed by current_metrics from 016. It blocks archive fallback
-- only when BSE says equity is negative and neither replacement provider has a valid ROE.
-- A valid reported/calculated replacement is not suppressed merely because BSE disagrees.
CREATE OR REPLACE VIEW roe_unusable AS
SELECT negative.instrument_id
FROM bse_negative_equity AS negative
WHERE NOT EXISTS (
    SELECT 1
    FROM metric_observations AS replacement
    WHERE replacement.instrument_id = negative.instrument_id
      AND replacement.field = 'roe_pct'
      AND replacement.provider IN ('screener', 'yahoo_roe')
      AND replacement.is_valid
);

COMMENT ON VIEW roe_unusable IS
    'No usable ROE: BSE reports non-positive equity and no valid Screener or Yahoo '
    'statement-derived replacement exists.';

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
        WHEN field = 'roe_pct' AND provider = 'screener' THEN 0
        WHEN field = 'roe_pct' AND provider = 'yahoo_roe' THEN 1
        WHEN field = 'roe_pct' AND provider = 'bse' THEN 2
        WHEN field = 'roce_pct' AND provider = 'screener' THEN 0
        ELSE 3
    END,
    observed_at DESC,
    id DESC;

COMMENT ON VIEW live_metrics IS
    'Newest valid observation per field after basis validation. ROE has deterministic '
    'field-level precedence: Screener, Yahoo annual statements, then eligible BSE. '
    'Provider refresh order cannot change that choice.';
