-- Use two bulk sources for every stock; reserve Screener for disagreements.
--
-- Screener's public pages permit individual company reads but rate-limit bulk traffic.
-- Yahoo annual statements and BSE cover the universe cheaply, so compare those first.
-- Only a BSE negative-equity report or a >50x disagreement is sent to Screener. This is
-- data-dependent, not a ticker list: every stock is evaluated by the same rule.

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
      / least(abs(bse.numeric_value), abs(yahoo.numeric_value)) > 50;

COMMENT ON VIEW roe_bse_conflict IS
    'BSE ROE requiring third-source review: negative equity or >50x disagreement with '
    'Yahoo annual-statement ROE, with both values outside the near-zero noise region.';

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
        WHEN field = 'roe_pct' AND provider = 'bse'
             AND instrument_id NOT IN (SELECT instrument_id FROM roe_bse_conflict) THEN 1
        WHEN field = 'roe_pct' AND provider = 'yahoo_roe' THEN 2
        WHEN field = 'roe_pct' AND provider = 'bse' THEN 3
        WHEN field = 'roce_pct' AND provider = 'screener' THEN 0
        ELSE 4
    END,
    observed_at DESC,
    id DESC;

COMMENT ON VIEW live_metrics IS
    'Canonical metrics after validation. ROE uses Screener for reviewed conflicts, BSE '
    'when the two bulk sources do not materially conflict, Yahoo annual statements as '
    'fallback, then conflicted BSE only as a last resort. Source order is deterministic.';
