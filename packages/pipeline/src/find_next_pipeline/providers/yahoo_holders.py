"""Ownership percentages from Yahoo.

The only free source found for promoter and institutional holding. NSE and BSE publish a
quarterly shareholding pattern, but neither exposes it through the JSON endpoints this
pipeline can reach — the BSE paths tried all return HTML.

**Provenance deviation, deliberately.** Every other provider archives the raw HTTP body
through `ArchivedHttpClient` before parsing. This one archives yfinance's already-parsed
dict instead. Yahoo gates holder data behind a rotating cookie+crumb scheme; a hand-rolled
httpx implementation returned 429 and could not even be verified, while yfinance kept
working through the same throttling. The payload is still archived before this module
reads it, so the audit trail survives — it is one layer further from the wire than
elsewhere, and that is the trade.

**`promoter_pct` is an approximation.** Yahoo reports `heldPercentInsiders`, which is not
identical to the promoter category in an Indian shareholding pattern. The legacy pipeline
made the same substitution and the archived CSV carries its output, so switching to it
changes source but not meaning. Sanity-checkable: TCS reads 71.8% (promoter holding is
~71.8%) and HDFCBANK 0.15% (it has no promoter).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.raw_store import RawJsonStore

# Yahoo throttles hard at this volume; the whole universe is 1,353 calls.
DEFAULT_CONCURRENCY = 4

# Yahoo fraction -> canonical field and unit. Emitted twice on purpose: the percent
# fields are what scoring's smart_money group reads, the fractions are the legacy
# camelCase names the dashboard still carries.
FIELDS = (
    ("heldPercentInsiders", "promoter_pct", 100.0, "percent"),
    ("heldPercentInstitutions", "institutional_pct", 100.0, "percent"),
    ("heldPercentInsiders", "heldPercentInsiders", 1.0, "fraction"),
    ("heldPercentInstitutions", "heldPercentInstitutions", 1.0, "fraction"),
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


class YahooHoldersProvider:
    """Promoter and institutional holding, one call per stock."""

    name = "yahoo_holders"
    endpoint = "yfinance:Ticker.info"

    def __init__(
        self,
        raw_store: RawJsonStore | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        ticker_factory: Any = None,
    ) -> None:
        self.raw_store = raw_store or RawJsonStore()
        self.concurrency = max(1, concurrency)
        # Injected so tests never touch the network.
        self._ticker_factory = ticker_factory

    def _info(self, ticker: str) -> dict[str, Any]:
        factory = self._ticker_factory
        if factory is None:
            import yfinance  # imported lazily: heavy, and only this provider needs it

            factory = yfinance.Ticker
        symbol = ticker if "." in ticker else f"{ticker}.NS"
        return factory(symbol).info or {}

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        wanted = [t.strip().upper() for t in tickers if t and t.strip()]
        if not wanted:
            return result

        limiter = asyncio.Semaphore(self.concurrency)

        async def one(ticker: str) -> tuple[str, dict[str, Any] | BaseException]:
            async with limiter:
                try:
                    return ticker, await asyncio.to_thread(self._info, ticker)
                except Exception as exc:  # noqa: BLE001 - reported per ticker below
                    return ticker, exc

        observed_at = datetime.now(UTC)
        for ticker, payload in await asyncio.gather(*(one(t) for t in wanted)):
            if isinstance(payload, BaseException):
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"Yahoo holders failed for {ticker}: {payload}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue

            envelope, _ = self.raw_store.save(
                provider=self.name,
                endpoint=self.endpoint,
                requested_at=observed_at,
                received_at=datetime.now(UTC),
                payload=payload,
                status_code=200,
                request_params={"symbol": ticker},
            )

            emitted = False
            for source_key, field, factor, unit in FIELDS:
                value = _number(payload.get(source_key))
                if value is None:
                    continue
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field=field,
                        value=round(value * factor, 6),
                        unit=unit,
                        provider=self.name,
                        endpoint=self.endpoint,
                        observed_at=observed_at,
                        raw_request_id=envelope.request_id,
                    )
                )
                emitted = True
            if not emitted:
                result.issues.append(
                    ValidationIssue(
                        code="provider_empty_payload",
                        message=f"Yahoo returned no holder percentages for {ticker}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
        return result
