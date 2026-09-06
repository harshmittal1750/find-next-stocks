-- Trust a P/E when two independent exchanges agree on it, whatever its magnitude.
--
-- A first attempt bounded P/E at 300 on the theory that a huge ratio means earnings
-- rounded to nothing. That was wrong, and UEL is the counter-example: BSE reports EPS
-- 0.29 against a price of 230.10, so a P/E near 793 is arithmetically right and Screener
-- independently shows 820. The bound deleted a correct figure. Magnitude cannot separate
-- the two cases -- GODAVARIB's EPS is 0.04 and wrong, UEL's is 0.29 and right, and both
-- are tiny.
--
-- What does separate them is whether the exchanges agree:
--
--     UEL         BSE  744.4   NSE  744.4    ratio 1.0    Screener  820   -> trust
--     GODAVARIB   BSE 5550.2   NSE   38.6    ratio 144    Screener 42.6   -> trust neither
--
-- Across the 1,182 stocks quoted on both, 1,006 agree within 10% and 1,119 within 50%.
-- Only 27 differ by more than 3x, and that set is exactly the suspect one: GODAVARIB
-- 144x, CAMLINFINE 72x, BLKASHYAP 60x, RPSGVENT 31x, WINDMACHIN 15x.
--
-- When they disagree that far, neither value is usable and we do not know which is
-- wrong -- BSE was right for ROLEXRINGS (31.34 against NSE's 1.58) and wrong for
-- GODAVARIB, so there is no provider to prefer. The honest output is no P/E at all:
-- coverage-adjusted scoring already handles a missing input, and a blank is true where
-- either number would be a guess.
--
-- Deliberately not bounded on either side. A low P/E is a value signal (PFC 3.4x,
-- KIRIINDUS 0.65x on an EPS of 897) and a high one can be real (UEL 793x). Only
-- disagreement is evidence of an error.

CREATE OR REPLACE VIEW pe_unusable AS
-- Instruments whose trailing P/E cannot be believed from any source. Defined once here
-- and consumed by both `live_metrics` and `current_metrics`: suppressing only the live
-- value lets the archive step straight in with a worse one (GODAVARIB read 4,916 from
-- CSV the moment the disputed observations were removed).
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
-- 1. The exchanges disagree by more than 3x, so neither figure is usable and there is no
--    provider to prefer: BSE was right for ROLEXRINGS and wrong for GODAVARIB.
SELECT instrument_id FROM by_exchange
WHERE bse_pe IS NOT NULL AND nse_pe IS NOT NULL
  AND greatest(bse_pe, nse_pe) / least(bse_pe, nse_pe) > 3.0
UNION
-- 2. Earnings are negative, so a positive P/E is not a large number but an impossible
--    one. 44 stocks carried one, NETWORK18 at 5,027.7 on an EPS of -0.21.
SELECT instrument_id FROM newest_eps WHERE numeric_value < 0;

COMMENT ON VIEW pe_unusable IS
    'Instruments with no believable trailing P/E: the two exchanges disagree beyond 3x, '
    'or earnings are negative so the ratio is undefined. Magnitude alone is never a '
    'reason to reject -- UEL trades at 793x and Screener agrees.';

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
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY instrument_id, field, observed_at DESC, id DESC;

COMMENT ON VIEW live_metrics IS
    'Newest valid observation per (instrument, field). Standalone-basis trailing_pe / '
    'trailing_eps are removed before the pick, and trailing_pe is dropped for anything '
    'listed in pe_unusable.';
