-- Keep exchange lifecycle events out of the live scoring universe.
--
-- A provider miss is not always a provider failure. Two symbols left by the imported
-- universe changed state in July 2026:
--
--   * GUJGASLTD was renamed GUJENERGY effective 2026-07-01. This is the same legal
--     instrument and ISIN, so update the identity in place; every observation and price
--     bar remains attached through instrument_id.
--   * JBCHEPHARM was suspended effective 2026-07-17 after its amalgamation into Torrent
--     Pharmaceuticals. Historical rows stay queryable, but it is not scoreable.
--
-- Sources are recorded on the rows so this is an auditable identity decision, not a
-- ticker-specific numerical override:
--   https://noticeblue.com/circulars/e44d1580-0989-446f-8d56-3b45cc08ec43
--   https://noticeblue.com/circulars/644d5591-9ea0-4f0f-8e42-647e6cb0c2c1

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS inactive_at DATE,
    ADD COLUMN IF NOT EXISTS lifecycle_note TEXT,
    ADD COLUMN IF NOT EXISTS lifecycle_source TEXT;

CREATE TABLE IF NOT EXISTS instrument_ticker_aliases (
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL,
    valid_until DATE,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, ticker, exchange)
);

COMMENT ON TABLE instrument_ticker_aliases IS
    'Historical exchange symbols. Provider observations remain linked by instrument_id; '
    'aliases preserve the identity trail across ticker changes.';

DO $$
DECLARE
    old_id BIGINT;
    conflicting_id BIGINT;
BEGIN
    SELECT id INTO old_id
    FROM instruments
    WHERE ticker = 'GUJGASLTD' AND exchange = 'NSE';

    SELECT id INTO conflicting_id
    FROM instruments
    WHERE ticker = 'GUJENERGY' AND exchange = 'NSE';

    IF old_id IS NOT NULL AND conflicting_id IS NOT NULL AND old_id <> conflicting_id THEN
        RAISE EXCEPTION
            'Cannot rename GUJGASLTD: GUJENERGY already belongs to instrument %',
            conflicting_id;
    END IF;

    IF old_id IS NOT NULL THEN
        INSERT INTO instrument_ticker_aliases (
            instrument_id, ticker, exchange, valid_until, source
        ) VALUES (
            old_id,
            'GUJGASLTD',
            'NSE',
            DATE '2026-06-30',
            'https://noticeblue.com/circulars/e44d1580-0989-446f-8d56-3b45cc08ec43'
        )
        ON CONFLICT (instrument_id, ticker, exchange) DO NOTHING;

        UPDATE instruments
        SET ticker = 'GUJENERGY',
            isin = 'INE844O01030',
            company_name = 'GUJARAT ENERGY LIMITED',
            active = TRUE,
            inactive_at = NULL,
            lifecycle_note = 'NSE symbol renamed from GUJGASLTD effective 2026-07-01',
            lifecycle_source =
                'https://noticeblue.com/circulars/e44d1580-0989-446f-8d56-3b45cc08ec43',
            updated_at = now()
        WHERE id = old_id;
    END IF;
END $$;

UPDATE instruments
SET active = FALSE,
    inactive_at = DATE '2026-07-17',
    lifecycle_note =
        'Trading suspended after amalgamation into Torrent Pharmaceuticals Limited',
    lifecycle_source =
        'https://noticeblue.com/circulars/644d5591-9ea0-4f0f-8e42-647e6cb0c2c1',
    updated_at = now()
WHERE ticker = 'JBCHEPHARM' AND exchange = 'NSE';

-- `active` already existed, but the scoreable-universe view did not enforce it. API
-- reads and scoring writes both join this view, so one rule governs DB -> API -> web.
CREATE OR REPLACE VIEW stock_instruments AS
    SELECT * FROM instruments WHERE kind = 'EQUITY' AND active;

COMMENT ON VIEW stock_instruments IS
    'The active, scoreable equity universe. Anything that ranks, scores, counts coverage '
    'or renders the dashboard reads this view. Inactive equities and benchmark indexes '
    'remain available historically through the base instruments table.';

-- Resolve imported snapshots through both the current ticker and a recorded historical
-- alias. GUJGASLTD's newest imported snapshot is 2026-08-31, so discarding its 88 fields
-- merely because the exchange renamed the symbol would create a false data gap. Fresh
-- observations still have higher priority and every inherited field remains origin=archive.
CREATE OR REPLACE VIEW stock_instrument_symbols AS
SELECT id AS instrument_id, ticker, exchange
FROM stock_instruments
UNION ALL
SELECT aliases.instrument_id, aliases.ticker, aliases.exchange
FROM instrument_ticker_aliases AS aliases
JOIN stock_instruments AS active ON active.id = aliases.instrument_id;

COMMENT ON VIEW stock_instrument_symbols IS
    'Current and historical symbols for active equities. Used only for identity resolution; '
    'the canonical ticker always comes from stock_instruments.';

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
  -- These rules must cover the archive as well as live observations. Otherwise a
  -- rejected value is immediately refilled by an older CSV row under either the
  -- current ticker or an alias.
  AND NOT (
      field IN ('trailingPE', 'trailing_pe')
      AND instrument_id IN (SELECT instrument_id FROM pe_unusable)
  )
  AND NOT (
      field IN ('roe_pct', 'returnOnEquity')
      AND instrument_id IN (SELECT instrument_id FROM roe_unusable)
  )
ORDER BY instrument_id, field, priority;

COMMENT ON VIEW current_metrics IS
    'Current value per active instrument and field. Precedence: latest ranking run, valid '
    'live observation, then newest imported snapshot resolved through current/historical '
    'tickers. The API and scoring engine share this single canonical view.';
