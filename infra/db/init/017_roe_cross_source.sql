-- Reject an ROE that a second source contradicts by two orders of magnitude.
--
-- Reported from the dashboard: TATACAP showed ROE of 2,609%; Screener reports ROCE near
-- 8.6%. ROCE and ROE are different ratios and are not substituted here. BSE publishes
-- `ROE: "2609.34"` with `PB: "-"` -- no book value at all, so
-- 016's negative-equity rule cannot see it, and the figure has no internal check.
--
-- Absence of a P/B is NOT the rule, and it was tested rather than assumed. Twelve stocks
-- publish an ROE with no book value; nine of them agree with the archived Yahoo figure
-- within 0.2x-3.0x (FACT 30.7 vs 33.8, KARURVYSYA 21.5 vs 19.3). Only three disagree,
-- and they disagree enormously:
--
--     TATACAP     BSE 2609.34   archive 12.0   217x
--     PIRAMALFIN  BSE 1233.15   archive  5.4   226x
--     HDBFS       BSE 1983.46   archive 12.3   161x
--
-- So the test is disagreement, not absence.
--
-- **The threshold was measured, not guessed.** Over the 1,093 stocks where both sources
-- report a meaningful ROE, 18 differ by more than 5x, 10 by more than 10x, and only 4 by
-- more than 50x -- the three above plus ASIANHOTNR, already excluded by 016. The next
-- largest gap is SIGNATURE at 32.3x, leaving a clean 5x margin on either side of the
-- cutoff. A naive 10x threshold would have rejected 40 stocks, most of them near-zero
-- artifacts (IZMO 0.0 against 12.4 is a 310x "disagreement" about nothing), which is why
-- both sides must clear 2% before the ratio means anything -- the same
-- denominator-near-zero trap this file has now hit three times.
--
-- Scope and shelf life, stated plainly: the archive is a stale independent opinion, fit
-- for catching two-orders-of-magnitude errors and nothing finer. It is not a source of
-- truth and must never be used to adjudicate small differences. When step 6 cuts the
-- archive loose this referee disappears, and these three stocks will need a real second
-- provider instead.

CREATE OR REPLACE VIEW roe_unusable AS
-- 1. Non-positive shareholders' equity: the ratio is arithmetic, not a return.
SELECT DISTINCT instrument_id
FROM metric_observations
WHERE field = 'book_value_sign'
  AND text_value = 'negative'

UNION

-- 2. A second source disagrees by more than 50x, with both readings large enough for the
--    ratio to carry meaning.
SELECT bse.instrument_id
FROM (
    SELECT DISTINCT ON (instrument_id) instrument_id, numeric_value
    FROM metric_observations
    WHERE field = 'roe_pct' AND provider = 'bse' AND is_valid
    ORDER BY instrument_id, observed_at DESC, id DESC
) AS bse
JOIN (
    SELECT i.id AS instrument_id, 100 * (kv.value #>> '{}')::numeric AS numeric_value
    FROM archive.csv_rows AS r
    CROSS JOIN LATERAL jsonb_each(r.record) AS kv(key, value)
    JOIN stock_instruments AS i ON i.ticker = upper(btrim(r.record ->> 'ticker'))
    WHERE kv.key = 'returnOnEquity'
      AND nullif(kv.value #>> '{}', '') IS NOT NULL
) AS archived USING (instrument_id)
WHERE abs(bse.numeric_value) >= 2
  AND abs(archived.numeric_value) >= 2
  AND greatest(abs(bse.numeric_value), abs(archived.numeric_value))
    / least(abs(bse.numeric_value), abs(archived.numeric_value)) > 50;

COMMENT ON VIEW roe_unusable IS
    'Instruments with no believable ROE: shareholders equity is non-positive, or a '
    'second source contradicts the figure by more than 50x. Magnitude alone is never the '
    'test -- KIRIINDUS at 1561% and NESTLEIND at 97% are both real and both kept.';
