from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient


class YahooChartProvider:
    """Unauthenticated Yahoo chart fallback for current price metadata."""

    name = "yahoo"

    def __init__(self, client: ArchivedHttpClient) -> None:
        self.client = client

    async def fetch(self, tickers: list[str]) -> ProviderResult:
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
                result.observations.extend(batch)
        return result

    async def _fetch_one(self, ticker: str) -> list[MetricObservation]:
        symbol = ticker if "." in ticker else f"{ticker}.NS"
        endpoint = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        envelope, payload = await self.client.get_json(
            provider=self.name,
            endpoint=endpoint,
            params={"range": "1y", "interval": "1d", "events": "div,splits"},
        )
        chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
        result = chart.get("result") or []
        if not result:
            raise ValueError(chart.get("error") or "missing chart result")
        meta: dict[str, Any] = result[0].get("meta", {})
        observed_at = datetime.fromtimestamp(
            meta.get("regularMarketTime", int(envelope.received_at.timestamp())), tz=UTC
        )
        mapping = {
            "current_price": (meta.get("regularMarketPrice"), meta.get("currency")),
            "previous_close": (meta.get("chartPreviousClose"), meta.get("currency")),
            "fifty_two_week_high": (meta.get("fiftyTwoWeekHigh"), meta.get("currency")),
            "fifty_two_week_low": (meta.get("fiftyTwoWeekLow"), meta.get("currency")),
        }
        return [
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
