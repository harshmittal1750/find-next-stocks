from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient


class UpstoxQuoteProvider:
    """Upstox full-market quotes using the official daily instrument master."""

    name = "upstox"
    instruments_endpoint = (
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    )
    quotes_endpoint = "https://api.upstox.com/v2/market-quote/quotes"
    max_instruments_per_request = 450

    def __init__(self, client: ArchivedHttpClient, access_token: str) -> None:
        if not access_token:
            raise ValueError("Upstox access or analytics token is required")
        self.client = client
        self.access_token = access_token

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        try:
            _, instruments = await self.client.get_json(
                provider=self.name,
                endpoint=self.instruments_endpoint,
            )
        except Exception as exc:
            result.issues.append(
                ValidationIssue(
                    code="provider_request_failed",
                    message=f"Upstox instrument master failed: {exc}",
                    field="ticker",
                    raw_value=len(tickers),
                )
            )
            return result

        ticker_set = {ticker.strip().upper() for ticker in tickers}
        by_key = self._instrument_map(instruments, ticker_set)
        if not by_key:
            result.issues.append(
                ValidationIssue(
                    code="provider_schema_invalid",
                    message="Upstox instrument master contained no matching NSE equities",
                    field="ticker",
                    raw_value=len(tickers),
                )
            )
            return result

        keys = list(by_key)
        for start in range(0, len(keys), self.max_instruments_per_request):
            batch = keys[start : start + self.max_instruments_per_request]
            try:
                envelope, payload = await self.client.get_json(
                    provider=self.name,
                    endpoint=self.quotes_endpoint,
                    params={"instrument_key": ",".join(batch)},
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.access_token}",
                    },
                )
            except Exception as exc:
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Upstox quote batch failed: {exc}",
                        field="ticker",
                        raw_value=len(batch),
                    )
                )
                continue
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            for response_key, quote in data.items():
                if not isinstance(quote, dict):
                    continue
                ticker = self._ticker_for_quote(response_key, quote, by_key)
                if ticker is None:
                    continue
                result.observations.extend(
                    self._observations(ticker, quote, envelope.request_id)
                )
        return result

    @staticmethod
    def _instrument_map(payload: Any, tickers: set[str]) -> dict[str, str]:
        if not isinstance(payload, list):
            return {}
        candidates: dict[str, tuple[int, str]] = {}
        for item in payload:
            if not isinstance(item, dict) or item.get("segment") != "NSE_EQ":
                continue
            ticker = str(item.get("trading_symbol") or "").strip().upper()
            key = str(item.get("instrument_key") or "").strip()
            if ticker not in tickers or not key:
                continue
            priority = 0 if item.get("instrument_type") == "EQ" else 1
            current = candidates.get(ticker)
            if current is None or priority < current[0]:
                candidates[ticker] = (priority, key)
        return {key: ticker for ticker, (_, key) in candidates.items()}

    @staticmethod
    def _ticker_for_quote(
        response_key: str,
        quote: dict[str, Any],
        by_key: dict[str, str],
    ) -> str | None:
        instrument_key = str(
            quote.get("instrument_token") or quote.get("instrument_key") or ""
        ).replace(":", "|")
        if instrument_key in by_key:
            return by_key[instrument_key]
        symbol = str(quote.get("symbol") or response_key.rsplit(":", 1)[-1]).strip().upper()
        return symbol if symbol in set(by_key.values()) else None

    @staticmethod
    def _observed_at(quote: dict[str, Any]) -> datetime:
        for field in ("timestamp", "last_trade_time"):
            raw = quote.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
                if value > 10_000_000_000:
                    value /= 1000
                return datetime.fromtimestamp(value, tz=UTC)
            except (TypeError, ValueError, OSError):
                continue
        return datetime.now(UTC)

    def _observations(
        self,
        ticker: str,
        quote: dict[str, Any],
        request_id,
    ) -> list[MetricObservation]:
        ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
        mapping = {
            "current_price": (quote.get("last_price"), "INR"),
            "day_open": (ohlc.get("open"), "INR"),
            "day_high": (ohlc.get("high"), "INR"),
            "day_low": (ohlc.get("low"), "INR"),
            "day_volume": (quote.get("volume"), "shares"),
        }
        observed_at = self._observed_at(quote)
        return [
            MetricObservation(
                ticker=ticker,
                field=field,
                value=value,
                unit=unit,
                provider=self.name,
                endpoint=self.quotes_endpoint,
                observed_at=observed_at,
                raw_request_id=request_id,
            )
            for field, (value, unit) in mapping.items()
            if value is not None
        ]
