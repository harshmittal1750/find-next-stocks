-- Prefer Upstox's structured, current return ratios over the BSE header values.
--
-- BSE's endpoint reports KIRIINDUS ROE as 1,561.1% and P/B as 9.73.  Upstox's
-- official key-ratios endpoint reports ROE -1.55%, closely matching the current
-- consolidated Screener page (-2.05%).  The BSE values are
-- internally arithmetical but use a different/stale accounting basis and must not enter
-- the same cross-sectional score.  The rule below is provider-wide, never ticker-wide.
--
-- Upstox also returns P/E, P/B, ROA and EV/EBITDA. They are retained in the append-only
-- observation table but deliberately excluded from the canonical view: UEL's Upstox
-- P/E and P/B are off by about 10x and 100x after corporate actions, while its Upstox
-- ROE/ROCE match Screener. Provider trust is field-level, not all-or-nothing.

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

COMMENT ON VIEW roe_bse_conflict IS
    'BSE ROE requiring Screener review: negative equity or >10x disagreement with '
    'Yahoo annual-statement ROE. The measured set is small enough for low-rate review '
    'and includes KIRIINDUS, which the old 50x cutoff missed.';

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

COMMENT ON VIEW roe_unusable IS
    'Cross-source ROE conflicts with no valid Screener or Upstox Fundamentals decision.';

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
      -- Basis rows are validation metadata for the observation they accompany, not
      -- independent user-facing metrics. Selecting them separately can pair Yahoo's
      -- basis label with an Upstox ROE. Keep them in metric_observations, use them above,
      -- and do not publish a mismatched label through current_metrics.
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

COMMENT ON VIEW live_metrics IS
    'Canonical valid observations. Upstox Fundamentals is primary only for ROE and ROCE; '
    'its other key ratios remain audit evidence after measured corporate-action errors. '
    'Existing exchange, statement-derived and Screener readings are deterministic fallbacks. '
    'Basis markers remain audit metadata and are not published as standalone metrics.';

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

COMMENT ON VIEW legacy_aliased_metrics IS
    'Canonical observations under legacy API/scoring names. Percentage conversions are '
    'explicit; ratios are rename-only.';
