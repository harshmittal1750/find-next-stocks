from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from find_next_pipeline.models import MetricObservation, PriceBar, RawEnvelope
from find_next_pipeline.paths import ROOT


@dataclass(frozen=True)
class PersistSummary:
    raw_responses: int = 0
    observations: int = 0


class PostgresObservationWarehouse:
    """Append-only provider observations for the shared PostgreSQL store."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def ensure_instruments(self, stocks: Iterable[dict[str, Any]]) -> int:
        rows = [
            (
                str(stock.get("ticker") or "").strip().upper(),
                str(stock.get("shortName") or stock.get("ticker") or "").strip(),
                stock.get("sector"),
                stock.get("exchange", "NSE"),
            )
            for stock in stocks
            if stock.get("ticker")
        ]
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO instruments (ticker, company_name, sector, exchange)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ticker, exchange) DO UPDATE SET
                        company_name = COALESCE(EXCLUDED.company_name, instruments.company_name),
                        sector = COALESCE(EXCLUDED.sector, instruments.sector),
                        updated_at = now()
                    """,
                    rows,
                )
        return len(rows)

    def sync_universe(self, stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply a complete, archived exchange snapshot without deleting history."""
        if not stocks:
            raise ValueError("Cannot synchronize an empty universe")
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM stock_instruments")
                existing_count = cursor.fetchone()[0]
                if len(stocks) < existing_count * 0.9:
                    raise ValueError(
                        "Universe shrank by more than 10%; review sources before applying"
                    )
                seen = []
                for stock in stocks:
                    cursor.execute(
                        "SELECT id, ticker, exchange, isin FROM instruments "
                        "WHERE kind = 'EQUITY' AND (isin = %s OR (ticker = %s AND exchange = %s))",
                        (stock["isin"], stock["ticker"], stock["exchange"]),
                    )
                    matches = cursor.fetchall()
                    if len(matches) > 1:
                        raise ValueError(f"Conflicting instrument identities for {stock['ticker']}")
                    if matches:
                        instrument_id, old_ticker, old_exchange, old_isin = matches[0]
                        if old_isin and old_isin != stock["isin"]:
                            raise ValueError(
                                f"ISIN changed for {stock['ticker']}; identity review required"
                            )
                        if (old_ticker, old_exchange) != (stock["ticker"], stock["exchange"]):
                            cursor.execute(
                                "INSERT INTO instrument_ticker_aliases "
                                "(instrument_id,ticker,exchange,source) "
                                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (instrument_id, old_ticker, old_exchange, stock["raw_request_id"]),
                            )
                        cursor.execute(
                            "UPDATE instruments SET ticker=%s, exchange=%s, isin=%s, "
                            "company_name=%s, "
                            "bse_code=%s, universe_request_id=%s, active=true, "
                            "inactive_at=NULL, updated_at=now() "
                            "WHERE id=%s",
                            (
                                stock["ticker"],
                                stock["exchange"],
                                stock["isin"],
                                stock["shortName"],
                                stock.get("bse_code"),
                                stock["raw_request_id"],
                                instrument_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO instruments "
                            "(ticker,exchange,isin,company_name,bse_code,universe_request_id) "
                            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                            (
                                stock["ticker"],
                                stock["exchange"],
                                stock["isin"],
                                stock["shortName"],
                                stock.get("bse_code"),
                                stock["raw_request_id"],
                            ),
                        )
                        instrument_id = cursor.fetchone()[0]
                    seen.append(instrument_id)
                cursor.execute(
                    "UPDATE instruments SET active=false, inactive_at=CURRENT_DATE, "
                    "lifecycle_note='Absent from complete NSE/BSE active equity snapshot', "
                    "updated_at=now() "
                    "WHERE kind='EQUITY' AND active AND NOT (id=ANY(%s))",
                    (seen,),
                )
        return self.read_instruments()

    def read_instruments(self) -> list[dict[str, Any]]:
        with psycopg.connect(self.database_url, row_factory=psycopg.rows.dict_row) as connection:
            return connection.execute(
                'SELECT ticker, exchange, isin, bse_code, company_name AS "shortName", sector '
                "FROM stock_instruments ORDER BY ticker"
            ).fetchall()

    def fresh_tickers(self, provider: str, max_age_days: int) -> set[str]:
        """Tickers this provider already covered within `max_age_days`.

        Financial statements are quarterly facts. Refetching all 5,428 of them on every
        run spends 21,712 requests -- roughly six hours of an account limited to 1,900
        per 30 minutes -- to re-learn numbers that cannot have changed. It also means a
        job killed at stock 3,000 restarts at zero, which is how a four-hour run produced
        307 stocks.
        """
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT instruments.ticker
                    FROM metric_observations AS o
                    JOIN instruments ON instruments.id = o.instrument_id
                    WHERE o.provider = %s
                      AND o.observed_at > now() - make_interval(days => %s)
                    """,
                    (provider, max_age_days),
                )
                return {row[0] for row in cursor}

    def last_refreshes(self, providers: Iterable[str]) -> dict[str, str]:
        names = sorted(set(providers))
        if not names:
            return {}
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT provider, max(received_at)
                    FROM raw_api_responses
                    WHERE provider = ANY(%s)
                      AND status_code BETWEEN 200 AND 299
                    GROUP BY provider
                    """,
                    (names,),
                )
                return {
                    provider: refreshed_at.isoformat()
                    for provider, refreshed_at in cursor
                    if refreshed_at is not None
                }

    def write(
        self,
        saved_envelopes: Iterable[tuple[RawEnvelope, Path]],
        observations: Iterable[MetricObservation],
    ) -> PersistSummary:
        envelopes = list(saved_envelopes)
        candidates = list(observations)
        request_ids: dict[UUID, UUID] = {}
        raw_count = 0
        observation_count = 0
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                for envelope, path in envelopes:
                    cursor.execute(
                        """
                        INSERT INTO raw_api_responses (
                            request_id, provider, endpoint, requested_at, received_at,
                            status_code, content_sha256, storage_path, request_params
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (request_id) DO UPDATE SET
                            request_id = EXCLUDED.request_id
                        RETURNING request_id
                        """,
                        (
                            envelope.request_id,
                            envelope.provider,
                            envelope.endpoint,
                            envelope.requested_at,
                            envelope.received_at,
                            envelope.status_code,
                            envelope.content_sha256,
                            self._storage_path(path),
                            Jsonb(envelope.request_params),
                        ),
                    )
                    stored_id = cursor.fetchone()[0]
                    request_ids[envelope.request_id] = stored_id
                    raw_count += 1

                tickers = sorted({item.ticker for item in candidates})
                instrument_ids: dict[str, int] = {}
                if tickers:
                    cursor.execute(
                        """
                        SELECT ticker, id
                        FROM stock_instruments
                        WHERE ticker = ANY(%s)
                        """,
                        (tickers,),
                    )
                    instrument_ids = {ticker: instrument_id for ticker, instrument_id in cursor}

                for item in candidates:
                    instrument_id = instrument_ids.get(item.ticker)
                    if instrument_id is None:
                        continue
                    numeric_value, text_value = self._split_value(item.value)
                    cursor.execute(
                        """
                        INSERT INTO metric_observations (
                            instrument_id, field, numeric_value, text_value, unit,
                            provider, endpoint, observed_at, raw_request_id,
                            is_valid, validation_issues, period_end, accounting_basis
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (instrument_id, field, provider, observed_at) DO NOTHING
                        """,
                        (
                            instrument_id,
                            item.field,
                            numeric_value,
                            text_value,
                            item.unit,
                            item.provider,
                            item.endpoint,
                            item.observed_at,
                            request_ids.get(item.raw_request_id, item.raw_request_id),
                            item.is_valid,
                            Jsonb([issue.model_dump(mode="json") for issue in item.issues]),
                            item.period_end,
                            item.accounting_basis,
                        ),
                    )
                    observation_count += max(cursor.rowcount, 0)
        return PersistSummary(raw_responses=raw_count, observations=observation_count)

    def write_price_bars(self, price_bars: Iterable[PriceBar]) -> int:
        """Persist daily OHLC history so derived indicators can be recomputed from it.

        Unlike ``metric_observations`` (an append-only provenance log — never replace an
        observation), a price bar for a given trading day is a single fact that a later
        same-day fetch may report more precisely (e.g. a still-forming last bar). Refetching
        it should update the row, not accumulate duplicates or silently no-op.

        Resolves against `instruments`, not `stock_instruments`: benchmark index bars are
        stored here too, and beta needs them. This and `read_dated_closes_bulk` are the
        only two places that see past the equity view.
        """
        bars = list(price_bars)
        if not bars:
            return 0
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                tickers = sorted({bar.ticker for bar in bars})
                cursor.execute(
                    """
                    SELECT ticker, id
                    FROM instruments
                    WHERE ticker = ANY(%s)
                    """,
                    (tickers,),
                )
                instrument_ids = {ticker: instrument_id for ticker, instrument_id in cursor}

                written = 0
                for bar in bars:
                    instrument_id = instrument_ids.get(bar.ticker)
                    if instrument_id is None:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO price_bars (
                            instrument_id, ts, interval, open, high, low, close,
                            adjusted_close, volume, provider
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (instrument_id, ts, interval, provider) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            adjusted_close = EXCLUDED.adjusted_close,
                            volume = EXCLUDED.volume,
                            ingested_at = now()
                        """,
                        (
                            instrument_id,
                            bar.ts,
                            bar.interval,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.adjusted_close,
                            bar.volume,
                            bar.provider,
                        ),
                    )
                    written += 1
        return written

    def read_dated_closes_bulk(
        self, tickers: Iterable[str], limit: int = 260
    ) -> dict[str, dict[date, float]]:
        """Daily closes per ticker, keyed by trading day.

        Reads `instruments` rather than `stock_instruments` — the one deliberate
        exception in the codebase. Beta regresses a stock against a benchmark, so this
        is the single query that has to see an equity and an index side by side.
        Everything that ranks, scores or counts coverage uses the view instead.

        Dates are the key, not a position in a list: a stock that was suspended or newly
        listed has gaps, and zipping two ragged series by index compares one day's return
        against another's. `beta_from_closes` intersects on these keys.
        """
        names = sorted({ticker.strip().upper() for ticker in tickers if ticker})
        if not names:
            return {}
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT instruments.ticker, price_bars.ts, price_bars.close
                    FROM price_bars
                    JOIN instruments ON instruments.id = price_bars.instrument_id
                    WHERE instruments.ticker = ANY(%s)
                      AND price_bars.interval = '1d'
                      AND price_bars.close IS NOT NULL
                    ORDER BY instruments.ticker, price_bars.ts
                    """,
                    (names,),
                )
                closes: dict[str, dict[date, float]] = defaultdict(dict)
                for ticker, ts, close in cursor:
                    # A day may carry bars from more than one provider; last write wins,
                    # and ORDER BY ts makes that deterministic.
                    closes[ticker][ts.date() if hasattr(ts, "date") else ts] = float(close)
        return {ticker: dict(sorted(by_day.items())[-limit:]) for ticker, by_day in closes.items()}

    def read_closes_bulk(self, tickers: Iterable[str], limit: int = 260) -> dict[str, list[float]]:
        """Chronological daily closes per ticker, for RSI and other history derivations."""
        return {
            ticker: list(by_day.values())
            for ticker, by_day in self.read_dated_closes_bulk(tickers, limit).items()
        }

    def roe_review_tickers(
        self,
        tickers: Iterable[str],
        *,
        ratio_threshold: float = 10,
        minimum_absolute_return: float = 2,
    ) -> list[str]:
        """Stocks whose BSE ROE needs a third-source decision.

        A company enters review when BSE reports negative equity, or when BSE and the
        independently calculated Yahoo annual-statement ROE differ by more than the
        measured 10x separation. Near-zero values are excluded from ratio comparisons;
        dividing by 0.01% creates an enormous multiplier without useful evidence.
        """
        names = sorted({ticker.strip().upper() for ticker in tickers if ticker})
        if not names:
            return []
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH latest_bse AS (
                        SELECT DISTINCT ON (instrument_id)
                            instrument_id, numeric_value
                        FROM metric_observations
                        WHERE field = 'roe_pct' AND provider = 'bse' AND is_valid
                        ORDER BY instrument_id, observed_at DESC, id DESC
                    ),
                    latest_yahoo AS (
                        SELECT DISTINCT ON (instrument_id)
                            instrument_id, numeric_value
                        FROM metric_observations
                        WHERE field = 'roe_pct' AND provider = 'yahoo_roe' AND is_valid
                        ORDER BY instrument_id, observed_at DESC, id DESC
                    ),
                    latest_sign AS (
                        SELECT DISTINCT ON (instrument_id)
                            instrument_id, text_value
                        FROM metric_observations
                        WHERE field = 'book_value_sign' AND provider = 'bse' AND is_valid
                        ORDER BY instrument_id, observed_at DESC, id DESC
                    )
                    SELECT instruments.ticker
                    FROM stock_instruments AS instruments
                    LEFT JOIN latest_bse AS bse ON bse.instrument_id = instruments.id
                    LEFT JOIN latest_yahoo AS yahoo ON yahoo.instrument_id = instruments.id
                    LEFT JOIN latest_sign AS sign ON sign.instrument_id = instruments.id
                    WHERE instruments.ticker = ANY(%s)
                      AND (
                        sign.text_value = 'negative'
                        OR (
                            abs(bse.numeric_value) >= %s
                            AND abs(yahoo.numeric_value) >= %s
                            AND greatest(abs(bse.numeric_value), abs(yahoo.numeric_value))
                                / least(abs(bse.numeric_value), abs(yahoo.numeric_value)) > %s
                        )
                      )
                    ORDER BY instruments.ticker
                    """,
                    (
                        names,
                        minimum_absolute_return,
                        minimum_absolute_return,
                        ratio_threshold,
                    ),
                )
                return [ticker for (ticker,) in cursor]

    def latest_ranking_run(self) -> dict[str, dict[str, Any]] | None:
        """{ticker: {"rank", "score"}} for the most recent scoring run, or None if
        no run has ever been written — the dashboard falls back to the archived CSV
        ranking in that case rather than failing outright."""
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM ranking_runs ORDER BY created_at DESC LIMIT 1")
                row = cursor.fetchone()
                if row is None:
                    return None
                run_id = row[0]
                cursor.execute(
                    """
                    SELECT instruments.ticker, ranked_stocks.rank, ranked_stocks.score
                    FROM ranked_stocks
                    JOIN instruments ON instruments.id = ranked_stocks.instrument_id
                    WHERE ranked_stocks.run_id = %s
                    """,
                    (run_id,),
                )
                return {
                    ticker: {
                        "rank": rank,
                        "score": float(score) if score is not None else None,
                    }
                    for ticker, rank, score in cursor
                }

    def write_ranking_run(
        self,
        *,
        model_version: str,
        parameters: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> UUID:
        """One row per scoring run (append-only — every run is a real point-in-time
        measurement, not metadata to overwrite), plus one ranked_stocks row per ticker.

        ``rows``: [{"ticker", "rank", "score", "score_status", "data_coverage",
        "factors": dict}, ...].
        """
        run_id = uuid4()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ranking_runs (id, model_version, input_as_of, parameters)
                    VALUES (%s, %s, now(), %s)
                    """,
                    (run_id, model_version, Jsonb(parameters)),
                )

                tickers = sorted({row["ticker"] for row in rows})
                instrument_ids: dict[str, int] = {}
                if tickers:
                    cursor.execute(
                        """
                        SELECT ticker, id
                        FROM stock_instruments
                        WHERE ticker = ANY(%s)
                        """,
                        (tickers,),
                    )
                    instrument_ids = {ticker: instrument_id for ticker, instrument_id in cursor}

                for row in rows:
                    instrument_id = instrument_ids.get(row["ticker"])
                    if instrument_id is None:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO ranked_stocks (
                            run_id, instrument_id, rank, score, score_status,
                            factors, data_coverage
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_id,
                            instrument_id,
                            row["rank"],
                            row["score"],
                            row["score_status"],
                            Jsonb(row["factors"]),
                            row["data_coverage"],
                        ),
                    )
        return run_id

    @staticmethod
    def _storage_path(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT))
        except ValueError:
            return str(path.resolve())

    @staticmethod
    def _split_value(value: float | int | str | None) -> tuple[float | int | None, str | None]:
        if isinstance(value, bool):
            return None, str(value).lower()
        if isinstance(value, (int, float)):
            return value, None
        if value is None:
            return None, None
        return None, str(value)
