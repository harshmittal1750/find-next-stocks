from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient


class AlphaVantageProvider:
    """Optional fundamentals provider. Disabled unless an API key is configured."""

    name = "alpha_vantage"
    endpoint = "https://www.alphavantage.co/query"

    def __init__(self, client: ArchivedHttpClient, api_key: str) -> None:
        if not api_key:
            raise ValueError("Alpha Vantage API key is required")
        self.client = client
        self.api_key = api_key

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
                        message=f"Alpha Vantage request failed for {ticker}: {batch}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
            else:
                result.observations.extend(batch)
        return result

    async def _fetch_one(self, ticker: str) -> list[MetricObservation]:
        envelope, payload = await self.client.get_json(
            provider=self.name,
            endpoint=self.endpoint,
            params={"function": "OVERVIEW", "symbol": ticker, "apikey": self.api_key},
        )
        if not isinstance(payload, dict) or payload.get("Note") or payload.get("Information"):
            raise ValueError(payload.get("Note") or payload.get("Information") or "invalid payload")

        observed_at = datetime.now(UTC)
        mapping: dict[str, tuple[Any, str | None]] = {
            "market_cap": (payload.get("MarketCapitalization"), payload.get("Currency")),
            "trailing_pe": (payload.get("PERatio"), "ratio"),
            "price_to_book": (payload.get("PriceToBookRatio"), "ratio"),
            "roe_pct": (payload.get("ReturnOnEquityTTM"), "fraction"),
            "profit_margin_pct": (payload.get("ProfitMargin"), "fraction"),
            "sector": (payload.get("Sector"), None),
        }
        observations: list[MetricObservation] = []
        for field, (raw_value, unit) in mapping.items():
            if raw_value in {None, "", "None", "-"}:
                continue
            value: str | float = raw_value
            if field != "sector":
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
            observations.append(
                MetricObservation(
                    ticker=ticker,
                    field=field,
                    value=value,
                    unit=unit,
                    provider=self.name,
                    endpoint=self.endpoint,
                    observed_at=observed_at,
                    raw_request_id=envelope.request_id,
                )
            )
        return observations
