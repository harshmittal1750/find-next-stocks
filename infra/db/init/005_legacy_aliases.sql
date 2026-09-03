-- Serve legacy field names from live observations.
--
-- The archived CSV and the providers describe the same quantity under different names:
-- the CSV carries yfinance's camelCase (`marketCap`), the pipeline emits snake_case
-- (`market_cap`). Everything downstream — scoring's GROUPS, the dashboard columns —
-- still asks for the legacy name, so the archive kept winning on ~10 fields whose live
-- values were sitting right there.
--
-- Two kinds of mapping, and conflating them is a 100x bug:
--
--   * Renames. Same unit, different spelling. `marketCap` is rupees and so is
--     `market_cap`; `trailingPE` is a ratio and so is `trailing_pe`.
--   * Conversions. The legacy column uses a *different unit*. `returnOnEquity` is a
--     fraction (0.0893) where `roe_pct` is a percentage (7.48); `mcap_cr` is crore where
--     `market_cap` is rupees. Aliasing these without the arithmetic would multiply
--     RELIANCE's ROE by 100 and quietly corrupt the quality group.
--
-- Priority 2 alongside `live`: a real observation under the legacy name still wins, and
-- these only ever displace the archive.

CREATE OR REPLACE VIEW legacy_aliased_metrics AS
WITH live AS (
    SELECT DISTINCT ON (o.instrument_id, o.field)
        o.instrument_id, o.field, o.numeric_value, o.text_value, o.provider, o.observed_at
    FROM metric_observations AS o
    WHERE o.is_valid
    ORDER BY o.instrument_id, o.field, o.observed_at DESC, o.id DESC
)
SELECT
    live.instrument_id,
    mapping.legacy_field AS field,
    live.numeric_value * mapping.factor AS numeric_value,
    live.text_value,
    live.provider,
    live.observed_at
FROM live
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
