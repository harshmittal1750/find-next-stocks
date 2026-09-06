"""Structured company ratios from the official Upstox Fundamentals API.

The endpoint is keyed by ISIN, so the provider resolves NSE symbols through Upstox's
official instrument master first.  Every HTTP response is archived before parsing and
the request rate is kept below Upstox's published standard-API limit.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from find_next_pipeline.models import (
    MetricObservation,
    ProviderResult,
    Severity,
    ValidationIssue,
)
from find_next_pipeline.providers.http import ArchivedHttpClient

INSTRUMENTS_ENDPOINT = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)
RATIOS_ENDPOINT = "https://api.upstox.com/v2/fundamentals/{isin}/key-ratios"

# The documented limit for standard APIs is 500/minute.  Starting at most one request
# every 0.13 seconds leaves a little headroom (about 461/minute), while concurrency lets
# slow TLS responses overlap without increasing the request-start rate.
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.13
DEFAULT_CONCURRENCY = 8
DEFAULT_ATTEMPTS = 3

RATIO_FIELDS: dict[str, tuple[str, str]] = {
    "P/E": ("trailing_pe", "ratio"),
    "P/B": ("price_to_book", "ratio"),
    "ROA": ("return_on_assets_pct", "percent"),
    "ROE": ("roe_pct", "percent"),
    "ROCE": ("roce_pct", "percent"),
    "EV/EBITDA": ("enterprise_to_ebitda", "ratio"),
}


def _number(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).replace(",", "").replace("%", "").strip()
    if not text or text.casefold() in {"-", "--", "na", "n/a", "none", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_key_ratios(payload: Any) -> dict[str, tuple[float, str]]:
    """Return canonical field -> (company value, unit); ignore sector benchmarks."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return {}

    values: dict[str, tuple[float, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        mapping = RATIO_FIELDS.get(str(row.get("name") or "").strip().upper())
        value = _number(row.get("company_value"))
        if mapping is None or value is None:
            continue
        field, unit = mapping
        values[field] = (value, unit)
    return values


class UpstoxFundamentalsProvider:
    name = "upstox_fundamentals"
    instruments_endpoint = INSTRUMENTS_ENDPOINT
    ratios_endpoint = RATIOS_ENDPOINT

    def __init__(
        self,
        client: ArchivedHttpClient,
        access_token: str,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        if not access_token:
            raise ValueError("Upstox access or analytics token is required")
        self.client = client
        self.access_token = access_token
        self.concurrency = max(1, concurrency)
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.attempts = max(1, attempts)
        self._instrument_payload: Any | None = None

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        try:
            if self._instrument_payload is None:
                _, self._instrument_payload = await self.client.get_json(
                    provider=self.name,
                    endpoint=self.instruments_endpoint,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced as a provider issue
            result.issues.append(
                ValidationIssue(
                    code="provider_request_failed",
                    message=f"Upstox instrument master failed: {exc}",
                    field="ticker",
                    raw_value=len(tickers),
                )
            )
            return result

        wanted = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker))
        ticker_isins = self._isin_map(self._instrument_payload, set(wanted))
        for ticker in wanted:
            if ticker not in ticker_isins:
                result.issues.append(
                    ValidationIssue(
                        code="symbol_not_listed_on_upstox",
                        message=f"No NSE equity ISIN found for {ticker}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )

        # These synchronization primitives are deliberately local to fetch().  The
        # refresh driver reuses a provider across multiple asyncio.run() event loops;
        # keeping them on the instance would bind later batches to a dead loop.
        limiter = asyncio.Semaphore(self.concurrency)
        rate_lock = asyncio.Lock()
        next_request_at = 0.0

        async def wait_for_request_slot() -> None:
            nonlocal next_request_at
            loop = asyncio.get_running_loop()
            async with rate_lock:
                delay = next_request_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_request_at = loop.time() + self.request_interval_seconds

        async def one(ticker: str, isin: str):
            endpoint = self.ratios_endpoint.format(isin=isin)
            last_error: Exception | None = None
            async with limiter:
                for attempt in range(self.attempts):
                    await wait_for_request_slot()
                    try:
                        envelope, payload = await self.client.get_json(
                            provider=self.name,
                            endpoint=endpoint,
                            headers={
                                "Accept": "application/json",
                                "Authorization": f"Bearer {self.access_token}",
                            },
                        )
                        return ticker, endpoint, envelope.request_id, payload, None
                    except Exception as exc:  # noqa: BLE001 - classified for retry below
                        last_error = exc
                        status = (
                            exc.response.status_code
                            if isinstance(exc, httpx.HTTPStatusError)
                            else None
                        )
                        retryable = status == 429 or (status is not None and status >= 500)
                        if not retryable or attempt + 1 >= self.attempts:
                            break
                        await asyncio.sleep(2**attempt)
            return ticker, endpoint, None, None, last_error

        responses = await asyncio.gather(
            *(one(ticker, ticker_isins[ticker]) for ticker in wanted if ticker in ticker_isins)
        )
        for ticker, endpoint, request_id, payload, error in responses:
            if error is not None:
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Upstox key-ratios request failed for {ticker}: {error}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            values = parse_key_ratios(payload)
            if not values:
                empty_success = (
                    isinstance(payload, dict)
                    and payload.get("status") == "success"
                    and payload.get("data") == []
                )
                result.issues.append(
                    ValidationIssue(
                        code=(
                            "provider_empty_payload"
                            if empty_success
                            else "provider_schema_invalid"
                        ),
                        message=(
                            f"Upstox publishes no key ratios for {ticker}"
                            if empty_success
                            else f"Upstox key-ratios schema was unusable for {ticker}"
                        ),
                        severity=Severity.WARNING if empty_success else Severity.ERROR,
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            observed_at = datetime.now(UTC)
            result.observations.extend(
                MetricObservation(
                    ticker=ticker,
                    field=field,
                    value=value,
                    unit=unit,
                    provider=self.name,
                    endpoint=endpoint,
                    observed_at=observed_at,
                    raw_request_id=request_id,
                )
                for field, (value, unit) in values.items()
            )
        return result

    @staticmethod
    def _isin_map(payload: Any, tickers: set[str]) -> dict[str, str]:
        if not isinstance(payload, list):
            return {}
        candidates: dict[str, tuple[int, str]] = {}
        for item in payload:
            if not isinstance(item, dict) or item.get("segment") != "NSE_EQ":
                continue
            ticker = str(item.get("trading_symbol") or "").strip().upper()
            if ticker not in tickers:
                continue
            isin = str(item.get("isin") or "").strip().upper()
            if not isin:
                key = str(item.get("instrument_key") or "")
                isin = key.partition("|")[2].strip().upper()
            if len(isin) != 12:
                continue
            priority = 0 if item.get("instrument_type") == "EQ" else 1
            current = candidates.get(ticker)
            if current is None or priority < current[0]:
                candidates[ticker] = (priority, isin)
        return {ticker: isin for ticker, (_, isin) in candidates.items()}
