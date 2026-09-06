"""Reported ROE and ROCE from public Screener consolidated company pages.

Screener has no public bulk API, so this adapter deliberately stays small: one public
company page, two headline ratios, low concurrency, raw HTML archived before parsing.
If the page shape changes, the provider emits a schema issue and the canonical view falls
back to structured Upstox return ratios. It never silently parses a nearby number.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Protocol

import httpx

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient

BASE_ENDPOINT = "https://www.screener.in/company"
DEFAULT_CONCURRENCY = 2
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.75
DEFAULT_ATTEMPTS = 3
_RATIO = re.compile(
    r'<span\s+class="name">\s*(ROCE|ROE)\s*</span>.*?'
    r'<span\s+class="number">\s*([^<]+?)\s*</span>',
    re.DOTALL,
)
FIELDS = {"ROE": "roe_pct", "ROCE": "roce_pct"}


class RoeReviewReader(Protocol):
    def roe_review_tickers(self, tickers: list[str]) -> list[str]: ...


def parse_headline_returns(page: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for label, raw in _RATIO.findall(page):
        try:
            values[FIELDS[label]] = float(raw.replace(",", "").strip())
        except ValueError:
            continue
    return values


class ScreenerReturnsProvider:
    name = "screener"
    endpoint = BASE_ENDPOINT

    def __init__(
        self,
        client: ArchivedHttpClient,
        concurrency: int = DEFAULT_CONCURRENCY,
        review_reader: RoeReviewReader | None = None,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        self.client = client
        self.concurrency = max(1, concurrency)
        self.review_reader = review_reader
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.attempts = max(1, attempts)

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        wanted = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
        if self.review_reader is not None:
            wanted = await asyncio.to_thread(self.review_reader.roe_review_tickers, wanted)
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

        async def one(ticker: str):
            endpoint = f"{self.endpoint}/{ticker}/consolidated/"
            last_error: Exception | None = None
            async with limiter:
                for attempt in range(self.attempts):
                    await wait_for_request_slot()
                    try:
                        envelope, page = await self.client.get_text(
                            provider=self.name,
                            endpoint=endpoint,
                            headers={
                                "User-Agent": "find-next-stocks/0.1 data-quality refresh",
                                "Accept": "text/html",
                            },
                        )
                        return ticker, endpoint, envelope.request_id, page, None
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

        observed_at = datetime.now(UTC)
        for ticker, endpoint, request_id, page, error in await asyncio.gather(
            *(one(ticker) for ticker in wanted)
        ):
            if error is not None:
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Screener returns request failed for {ticker}: {error}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            values = parse_headline_returns(page or "")
            if not values:
                result.issues.append(
                    ValidationIssue(
                        code="provider_schema_changed",
                        message=f"Screener page contained no headline ROE/ROCE for {ticker}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            for field, value in values.items():
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field=field,
                        value=value,
                        unit="percent",
                        provider=self.name,
                        endpoint=endpoint,
                        observed_at=observed_at,
                        raw_request_id=request_id,
                    )
                )
        return result
