-- Exclude standalone-basis rows BEFORE deduplication, not after.
--
-- 009 added the mixed-accounting-basis guard and 010 moved it into `live_metrics`, but
-- both applied it in the wrong order:
--
--     live_raw : SELECT DISTINCT ON (instrument_id, field) ... ORDER BY observed_at DESC
--     live     : SELECT ... FROM live_raw WHERE NOT (standalone)
--
-- `DISTINCT ON` picked one winner per field first, and the filter then deleted it. When
-- BSE's standalone trailing_pe was the newest observation it won deduplication and was
-- promptly removed -- and NSE's older, valid, consolidated trailing_pe never got a look,
-- because dedup had already discarded it. The guard did not fall through to the next
-- best source; it just left a hole, and `current_metrics` filled that hole from the
-- months-old CSV archive.
--
-- Measured cost: 202 stocks served an archived trailingPE and 189 an archived
-- trailingEps, every one of them while a valid NSE observation sat in the table. P/E
-- moves with price daily, so an archived one is not merely stale, it is wrong -- and
-- trailingPE is an input to the valuation scoring group.
--
-- The fix is ordering only: filter first, then let DISTINCT ON pick the newest row that
-- survived. A filter that runs after a pick can only remove; a filter that runs before
-- it can choose.

CREATE OR REPLACE VIEW live_metrics AS
WITH all_valid AS (
    SELECT o.id, o.instrument_id, o.field, o.numeric_value, o.text_value,
           o.unit, o.provider, o.observed_at
    FROM metric_observations AS o
    WHERE o.is_valid
),
basis AS (
    -- One basis marker per (instrument, provider, metric). Markers need their own
    -- dedup now that `all_valid` is no longer deduplicated upstream.
    SELECT DISTINCT ON (instrument_id, provider, metric)
        instrument_id,
        provider,
        left(field, length(field) - 6) AS metric,
        text_value AS basis
    FROM all_valid
    WHERE right(field, 6) = '_basis'
    ORDER BY instrument_id, provider, metric, observed_at DESC, id DESC
),
eligible AS (
    -- Joined on provider as well as metric: a basis marker describes the accounting
    -- basis of *that provider's* figure, so BSE's marker must not disqualify NSE's row.
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
)
SELECT DISTINCT ON (instrument_id, field)
    instrument_id, field, numeric_value, text_value, unit, provider, observed_at
FROM eligible
ORDER BY instrument_id, field, observed_at DESC, id DESC;

COMMENT ON VIEW live_metrics IS
    'Newest valid observation per (instrument, field), after standalone-basis '
    'trailing_pe/trailing_eps rows are removed. The exclusion runs before the pick so a '
    'consolidated figure from another provider can win, rather than leaving a hole for '
    'the archive to fill.';
