from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Protocol

from find_next_pipeline.derivations import (
    beta_from_closes,
    moving_average,
    rsi,
    rsi_signal,
    window_change_pct,
)
from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue

RSI_PERIOD = 14
RSI_MIN_CLOSES = RSI_PERIOD + 1

# Yahoo's symbol for NIFTY 50, stored as an INDEX row in `instruments`. Beta is measured
# against the broad market, and this is the index the archived CSV used.
BENCHMARK_TICKER = "^NSEI"

# (field, unit, function). Each needs only the close series, so they all come free once
# the bars are read — the expensive part is the query, not the arithmetic.
CLOSE_DERIVATIONS = (
    ("fiftyDayAverage", "INR", lambda c: moving_average(c, 50)),
    ("twoHundredDayAverage", "INR", lambda c: moving_average(c, 200)),
    ("fiftyTwoWeekChangePercent", "percent", window_change_pct),
    # Defined as a fixed 5-session window, not "since the previous run". The
    # since-last-run definition is what produced price_chg_pct = 0.0 for all 1,353
    # stocks in the shipped snapshot: a run compared its own archived output against
    # itself. A session count cannot collapse that way.
    ("price_chg_pct", "percent", lambda c: window_change_pct(c, 6)),
)
# pct_below_52w_high / pct_above_52w_low are deliberately NOT here. Two layers already
# compute them from the live fiftyTwoWeekHigh/Low observations — repository.py for
# display and scoring._derive_extra_factors for the model, the latter applying
# PCT_BELOW_CAP so a falling knife cannot dominate the price-setup group. Emitting a
# third close-based definition just gets overwritten by both and invites the two to
# disagree. `derivations.pct_below_high` stays available for callers that want the
# close-based reading explicitly.


class ClosesReader(Protocol):
    def read_dated_closes_bulk(self, tickers: list[str]) -> dict[str, dict[date, float]]: ...


class DerivedMetricsProvider:
    """Computes indicators from already-archived price history — no HTTP calls.

    Runs after the providers that populate ``price_bars`` (Yahoo today). Reading from
    storage rather than an in-flight response means the indicator can be recomputed over
    history — a formula or period change replays from stored bars instead of re-fetching.
    """

    name = "derived"

    def __init__(self, warehouse: ClosesReader) -> None:
        self.warehouse = warehouse

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        # The benchmark rides along in the same query; beta needs it aligned by date
        # against every stock, and fetching it once beats 1,353 round trips.
        dated = await asyncio.to_thread(
            self.warehouse.read_dated_closes_bulk, [*tickers, BENCHMARK_TICKER]
        )
        benchmark = dated.get(BENCHMARK_TICKER, {})
        result = ProviderResult(provider=self.name)
        observed_at = datetime.now(UTC)
        if not benchmark:
            result.issues.append(
                ValidationIssue(
                    code="missing_benchmark_history",
                    message=(
                        f"No price bars for {BENCHMARK_TICKER}; beta skipped for "
                        f"{len(tickers)} stocks"
                    ),
                    field="ticker",
                    raw_value=BENCHMARK_TICKER,
                )
            )
        for ticker in tickers:
            if ticker == BENCHMARK_TICKER:
                continue  # the index is an input here, not something to emit metrics for
            by_day = dated.get(ticker, {})
            closes = list(by_day.values())
            beta_value = beta_from_closes(by_day, benchmark) if benchmark else None
            if beta_value is not None:
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field="beta",
                        value=round(beta_value, 4),
                        unit="ratio",
                        provider=self.name,
                        observed_at=observed_at,
                    )
                )
            value = rsi(closes, RSI_PERIOD)
            if value is None:
                result.issues.append(
                    ValidationIssue(
                        code="insufficient_price_history",
                        message=(
                            f"Need {RSI_MIN_CLOSES}+ daily closes for RSI; have {len(closes)}"
                        ),
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            result.observations.append(
                MetricObservation(
                    ticker=ticker,
                    field="rsi_14",
                    value=value,
                    unit="rsi",
                    provider=self.name,
                    observed_at=observed_at,
                )
            )
            signal = rsi_signal(value)
            if signal is not None:
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field="rsi_14_signal",
                        value=signal,
                        provider=self.name,
                        observed_at=observed_at,
                    )
                )
            # A stock too short for a 200-day average still gets its RSI and its
            # 52-week figures; each derivation declines on its own.
            for field, unit, compute in CLOSE_DERIVATIONS:
                derived = compute(closes)
                if derived is None:
                    continue
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field=field,
                        value=derived,
                        unit=unit,
                        provider=self.name,
                        observed_at=observed_at,
                    )
                )
        return result
