"""Annual return on equity calculated from Yahoo financial statements.

Yahoo's profile endpoint exposes ``returnOnEquity`` for only a small fraction of the
Indian universe. Its financial-timeseries endpoint has much better coverage and exposes
the two facts needed for a comparable annual ratio:

    net income attributable to common shareholders / average shareholders' equity

The raw response is archived before any value is parsed. BSE's header-level ``ROE`` is
kept as a fallback, but this statement-derived figure is preferred by the SQL canonical
view. This is a universe-wide source rule; there are no ticker exceptions.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient

BASE_ENDPOINT = (
    "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries"
)
SERIES_TYPES = (
    "annualNetIncomeCommonStockholders,annualNetIncome,"
    "annualStockholdersEquity,annualCommonStockEquity"
)
DEFAULT_CONCURRENCY = 4
HISTORY_SECONDS = 6 * 366 * 24 * 60 * 60


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


def _series(payload: dict[str, Any], preferred_names: tuple[str, ...]) -> list[tuple[str, float]]:
    results = payload.get("timeseries", {}).get("result", [])
    if not isinstance(results, list):
        return []
    by_type: dict[str, list[tuple[str, float]]] = {}
    for block in results:
        if not isinstance(block, dict):
            continue
        types = block.get("meta", {}).get("type", [])
        series_type = types[0] if isinstance(types, list) and types else None
        if not isinstance(series_type, str):
            continue
        points = block.get(series_type, [])
        if not isinstance(points, list):
            continue
        parsed: list[tuple[str, float]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            as_of = point.get("asOfDate")
            value = _number(point.get("reportedValue", {}).get("raw"))
            if isinstance(as_of, str) and value is not None:
                parsed.append((as_of, value))
        if parsed:
            by_type[series_type] = sorted(parsed)
    for name in preferred_names:
        if name in by_type:
            return by_type[name]
    return []


def calculate_annual_roe(payload: dict[str, Any]) -> tuple[float, str] | None:
    """Return (ROE percent, fiscal period) from aligned annual statement rows."""
    incomes = _series(payload, ("annualNetIncomeCommonStockholders", "annualNetIncome"))
    equities = _series(payload, ("annualStockholdersEquity", "annualCommonStockEquity"))
    if not incomes or len(equities) < 2:
        return None

    income_by_date = dict(incomes)
    equity_by_date = dict(equities)
    for period in sorted(set(income_by_date) & set(equity_by_date), reverse=True):
        earlier = [date for date in equity_by_date if date < period]
        if not earlier:
            continue
        previous_period = max(earlier)
        current_equity = equity_by_date[period]
        previous_equity = equity_by_date[previous_period]
        average_equity = (current_equity + previous_equity) / 2
        # A return on non-positive capital is not economically meaningful.
        if current_equity <= 0 or previous_equity <= 0 or average_equity <= 0:
            return None
        return round(income_by_date[period] / average_equity * 100, 6), period
    return None


class YahooRoeProvider:
    """Fetch one compact annual-statement payload per stock and calculate ROE."""

    name = "yahoo_roe"
    endpoint = BASE_ENDPOINT

    def __init__(
        self,
        client: ArchivedHttpClient,
        concurrency: int = DEFAULT_CONCURRENCY,
        now: Any = time.time,
    ) -> None:
        self.client = client
        self.concurrency = max(1, concurrency)
        self._now = now

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        wanted = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
        if not wanted:
            return result

        limiter = asyncio.Semaphore(self.concurrency)

        async def one(ticker: str):
            symbol = ticker if "." in ticker else f"{ticker}.NS"
            period2 = int(self._now())
            try:
                async with limiter:
                    envelope, payload = await self.client.get_json(
                        provider=self.name,
                        endpoint=f"{self.endpoint}/{symbol}",
                        params={
                            "symbol": symbol,
                            "type": SERIES_TYPES,
                            "period1": period2 - HISTORY_SECONDS,
                            "period2": period2,
                        },
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                return ticker, envelope.request_id, payload, None
            except Exception as exc:  # noqa: BLE001 - reported per ticker below
                return ticker, None, None, exc

        observed_at = datetime.now(UTC)
        for ticker, request_id, payload, error in await asyncio.gather(
            *(one(ticker) for ticker in wanted)
        ):
            if error is not None:
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Yahoo ROE statements failed for {ticker}: {error}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            if not isinstance(payload, dict):
                calculated = None
            else:
                calculated = calculate_annual_roe(payload)
            if calculated is None:
                result.issues.append(
                    ValidationIssue(
                        code="insufficient_statement_history",
                        message=(
                            f"Yahoo has no aligned annual net income and two positive "
                            f"equity periods for {ticker}"
                        ),
                        field="roe_pct",
                        raw_value=ticker,
                    )
                )
                continue

            value, fiscal_period = calculated
            result.observations.extend(
                [
                    MetricObservation(
                        ticker=ticker,
                        field="roe_pct",
                        value=value,
                        unit="percent",
                        provider=self.name,
                        endpoint=self.endpoint,
                        observed_at=observed_at,
                        raw_request_id=request_id,
                    ),
                    MetricObservation(
                        ticker=ticker,
                        field="roe_pct_basis",
                        value="annual_net_income_over_average_equity",
                        provider=self.name,
                        endpoint=self.endpoint,
                        observed_at=observed_at,
                        raw_request_id=request_id,
                    ),
                    MetricObservation(
                        ticker=ticker,
                        field="roe_fiscal_period",
                        value=fiscal_period,
                        provider=self.name,
                        endpoint=self.endpoint,
                        observed_at=observed_at,
                        raw_request_id=request_id,
                    ),
                ]
            )
        return result
