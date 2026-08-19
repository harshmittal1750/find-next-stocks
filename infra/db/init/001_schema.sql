CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS instruments (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL,
    isin TEXT,
    company_name TEXT,
    sector TEXT,
    currency TEXT NOT NULL DEFAULT 'INR',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, exchange)
);

CREATE TABLE IF NOT EXISTS price_bars (
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    ts TIMESTAMPTZ NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    adjusted_close NUMERIC,
    volume NUMERIC,
    provider TEXT NOT NULL,
    raw_request_id UUID,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, ts, interval, provider)
);

SELECT create_hypertable(
    'price_bars',
    by_range('ts'),
    if_not_exists => TRUE
);

CREATE TABLE IF NOT EXISTS raw_api_responses (
    request_id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    status_code INTEGER,
    content_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    request_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider, content_sha256)
);

CREATE TABLE IF NOT EXISTS metric_observations (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    field TEXT NOT NULL,
    numeric_value NUMERIC,
    text_value TEXT,
    unit TEXT,
    provider TEXT NOT NULL,
    endpoint TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    raw_request_id UUID REFERENCES raw_api_responses(request_id),
    is_valid BOOLEAN NOT NULL,
    validation_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, field, provider, observed_at)
);

CREATE INDEX IF NOT EXISTS metric_observations_lookup
    ON metric_observations (instrument_id, field, observed_at DESC);

CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    as_of DATE NOT NULL,
    period_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    metrics JSONB NOT NULL,
    raw_request_id UUID REFERENCES raw_api_responses(request_id),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, as_of, period_type, provider)
);

CREATE TABLE IF NOT EXISTS ranking_runs (
    id UUID PRIMARY KEY,
    model_version TEXT NOT NULL,
    input_as_of TIMESTAMPTZ NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ranked_stocks (
    run_id UUID NOT NULL REFERENCES ranking_runs(id) ON DELETE CASCADE,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    rank INTEGER,
    score NUMERIC,
    score_status TEXT NOT NULL,
    factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_coverage NUMERIC,
    PRIMARY KEY (run_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS app_users (
    id UUID PRIMARY KEY,
    email TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlists (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    note TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, instrument_id)
);
