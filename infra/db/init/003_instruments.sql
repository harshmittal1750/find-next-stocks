-- Instrument types, benchmark seed, and recorded exchange lifecycle changes.

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'EQUITY';

ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_kind_check;
ALTER TABLE instruments
    ADD CONSTRAINT instruments_kind_check CHECK (kind IN ('EQUITY', 'INDEX'));

COMMENT ON COLUMN instruments.kind IS
    'What this row is, as distinct from where it trades. EQUITY rows are the scoreable '
    'universe; INDEX rows carry a benchmark series for beta and nothing else.';

INSERT INTO instruments (ticker, exchange, kind, company_name, currency)
VALUES ('^NSEI', 'NSE', 'INDEX', 'NIFTY 50', 'INR')
ON CONFLICT (ticker, exchange) DO UPDATE SET kind = 'INDEX', updated_at = now();

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

-- Listings retain exchange identity; observations retain reporting period and basis.
ALTER TABLE instruments ADD COLUMN IF NOT EXISTS bse_code TEXT;
ALTER TABLE instruments ADD COLUMN IF NOT EXISTS universe_request_id UUID REFERENCES raw_api_responses(request_id);
ALTER TABLE metric_observations ADD COLUMN IF NOT EXISTS period_end DATE;
ALTER TABLE metric_observations ADD COLUMN IF NOT EXISTS accounting_basis TEXT;

-- Identical bodies from different requests still have different provenance/timestamps.
ALTER TABLE raw_api_responses DROP CONSTRAINT IF EXISTS raw_api_responses_provider_content_sha256_key;
CREATE INDEX IF NOT EXISTS raw_api_responses_content_lookup ON raw_api_responses(provider, content_sha256);
CREATE INDEX IF NOT EXISTS instruments_isin_lookup ON instruments(isin);
