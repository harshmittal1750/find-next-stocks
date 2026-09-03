from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from typing import Any

import httpx

from find_next_pipeline.models import (
    MetricObservation,
    PriceBar,
    ProviderResult,
    RawEnvelope,
    ValidationIssue,
)
from find_next_pipeline.providers.http import ArchivedHttpClient


class YahooChartProvider:
    """Unauthenticated Yahoo chart fallback for current price metadata and daily history.

    Every archived response from the last full run was a 429 (4133/4133) — not real rate
    limiting: Yahoo bot-blocks the client's default User-Agent outright, confirmed by curl
    returning 429 with no UA and 200 with a browser one. A concurrency cap and retry stay
    in place regardless, since a real per-IP limit is still plausible once this is no
    longer hitting the bot filter on every request.
    """

    name = "yahoo"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # ponytail: fixed concurrency cap + retry budget, not an adaptive limiter. Raise the
    # ceiling only after confirming Yahoo tolerates it at higher volume.
    max_concurrent_requests = 4
    max_attempts = 3

    def __init__(self, client: ArchivedHttpClient) -> None:
        self.client = client

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        # Built fresh per call, not in __init__: the caller runs one batch of tickers
        # per asyncio.run(), each spinning up its own event loop. A semaphore built once
        # and reused across those loops binds to the first and raises "bound to a
        # different event loop" on every batch after it — which is what silently failed
        # 1,113 of 1,353 tickers on the first run of this fix.
        self._semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        batches = await asyncio.gather(
            *(self._fetch_one(ticker) for ticker in tickers), return_exceptions=True
        )
        result = ProviderResult(provider=self.name)
        for ticker, batch in zip(tickers, batches, strict=True):
            if isinstance(batch, Exception):
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Yahoo request failed for {ticker}: {batch}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
            else:
                observations, price_bars = batch
                result.observations.extend(observations)
                result.price_bars.extend(price_bars)
        return result

    async def _fetch_one(self, ticker: str) -> tuple[list[MetricObservation], list[PriceBar]]:
        # Yahoo names Indian equities RELIANCE.NS but indices ^NSEI, with no suffix.
        # Appending .NS to a caret symbol asks for a stock that does not exist.
        symbol = ticker if ("." in ticker or ticker.startswith("^")) else f"{ticker}.NS"
        endpoint = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        envelope, payload = await self._get_with_retry(
            endpoint, {"range": "1y", "interval": "1d", "events": "div,splits"}
        )
        chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
        result = chart.get("result") or []
        if not result:
            raise ValueError(chart.get("error") or "missing chart result")
        chart_result = result[0]
        meta: dict[str, Any] = chart_result.get("meta", {})
        observed_at = datetime.fromtimestamp(
            meta.get("regularMarketTime", int(envelope.received_at.timestamp())), tz=UTC
        )
        mapping = {
            "current_price": (meta.get("regularMarketPrice"), meta.get("currency")),
            "previous_close": (meta.get("chartPreviousClose"), meta.get("currency")),
            "fifty_two_week_high": (meta.get("fiftyTwoWeekHigh"), meta.get("currency")),
            "fifty_two_week_low": (meta.get("fiftyTwoWeekLow"), meta.get("currency")),
        }
        observations = [
            MetricObservation(
                ticker=ticker,
                field=field,
                value=value,
                unit=unit,
                provider=self.name,
                endpoint=endpoint,
                observed_at=observed_at,
                raw_request_id=envelope.request_id,
            )
            for field, (value, unit) in mapping.items()
            if value is not None
        ]
        price_bars = self._parse_price_bars(chart_result, ticker, envelope.request_id)
        return observations, price_bars

    async def _get_with_retry(
        self, endpoint: str, params: dict[str, Any]
    ) -> tuple[RawEnvelope, Any]:
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with self._semaphore:
                    return await self.client.get_json(
                        provider=self.name,
                        endpoint=endpoint,
                        params=params,
                        headers={"User-Agent": self.user_agent},
                    )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == self.max_attempts:
                    raise
                last_exc = exc
            await asyncio.sleep((2**attempt) + random.uniform(0, 1))
        raise last_exc  # pragma: no cover - loop always returns or raises above

    @staticmethod
    def _parse_price_bars(
        chart_result: dict[str, Any], ticker: str, request_id: Any
    ) -> list[PriceBar]:
        """Yahoo's 1y daily series was already being fetched and thrown away.

        The same response that supplies current_price/52w-high/low carries a full
        timestamp+OHLCV series; extracting it here means RSI and other history-based
        derivations cost nothing extra to fetch.
        """
        timestamps = chart_result.get("timestamp") or []
        indicators = chart_result.get("indicators", {})
        quotes = indicators.get("quote") or [{}]
        quote = quotes[0] if quotes else {}
        adjclose_series = indicators.get("adjclose") or [{}]
        adjcloses = (adjclose_series[0] if adjclose_series else {}).get("adjclose") or []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[PriceBar] = []
        for index, ts in enumerate(timestamps):
            close = closes[index] if index < len(closes) else None
            if close is None:
                continue
            bars.append(
                PriceBar(
                    ticker=ticker,
                    ts=datetime.fromtimestamp(ts, tz=UTC),
                    open=opens[index] if index < len(opens) else None,
                    high=highs[index] if index < len(highs) else None,
                    low=lows[index] if index < len(lows) else None,
                    close=close,
                    adjusted_close=adjcloses[index] if index < len(adjcloses) else None,
                    volume=volumes[index] if index < len(volumes) else None,
                    provider=YahooChartProvider.name,
                    raw_request_id=request_id,
                )
            )
        return bars
