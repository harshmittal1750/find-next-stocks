"""Filed quarterly and annual results from BSE.

`bse.py` reads BSE's company header, which carries ratios BSE computed for us -- and
those ratios are where the worst data in this project has come from. GODAVARIB's header
says `ConEPS: "0.04"` and `ConPE: "5550.15"`; TATACAP's says `ROE: "2609.34"` where the
real return is near 8.6%. This endpoint is different in kind: it returns the revenue,
profit and EPS a company actually filed, not a ratio derived from them. TATACAP reports
EPS 7.77 for FY25-26 here, which is what a P/E near 30 is built from.

Tested against six randomly chosen stocks carrying no statement data at all -- including
two listed only on BSE, with no NSE symbol -- six responded and five were current to the
June 2026 quarter. It costs one unauthenticated request per stock and, unlike the Upstox
statement endpoints, draws on no rate-limit budget that anything else needs.

**Field names are deliberately prefixed `results_`.** The response never says whether the
figures are consolidated or standalone. `bse.py` learned that the hard way: across 3,994
archived company headers, BSE's `Con*` fields were populated 0% of the time, so every
value that provider has produced is standalone. Writing these into `revenue` or
`net_profit` beside Upstox's explicitly consolidated figures would pool two different
measurements into one cross-sectional percentile -- the exact defect that
`mixed_accounting_basis` exists to prevent. They stay in their own namespace until
something establishes the basis.

The one field emitted into the shared namespace is `earnings_quarterly_growth`, because
it is a ratio of two consecutive quarters from this same response: whatever basis BSE
used, both sides of the division used it too. Only 24 stocks in the universe currently
have that field at all.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.bse import HEADERS
from find_next_pipeline.providers.http import ArchivedHttpClient

ENDPOINT = "https://api.bseindia.com/BseIndiaAPI/api/TabResults_PAR/w"

# BSE quotes these in crore; every monetary field in the warehouse is in rupees.
CRORE = 1e7

# (BSE row title, emitted field, index into the row). v1 is the latest quarter, v2 the
# one before it, v3 the full financial year -- the columns are labelled in col2/col3/col4.
LEVELS = (
    ("Revenue", "results_quarterly_revenue", "v1"),
    ("Net Profit", "results_quarterly_net_profit", "v1"),
    ("Revenue", "results_revenue", "v3"),
    ("Net Profit", "results_net_profit", "v3"),
)


def _number(value: Any) -> float | None:
    """BSE writes '6,334.12' for a number and '--' for a blank."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-", "NA"}:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    return None if numeric != numeric else numeric


def parse_results(payload: Any) -> tuple[dict[str, float], dict[str, str]]:
    """Return (field -> value, column labels). Empty when the stock has filed nothing.

    The endpoint answers with a JSON *string* containing JSON, so the body is decoded
    twice. Callers get a plain dict either way.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, str):            # double-encoded, as BSE actually sends it
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return {}, {}

    rows = {row.get("title"): row for row in payload.get("resultinCr", []) if isinstance(row, dict)}
    values: dict[str, float] = {}
    for title, field, column in LEVELS:
        amount = _number(rows.get(title, {}).get(column))
        if amount is not None:
            values[field] = round(amount * CRORE, 2)

    # EPS is per share, not in crore, so it is not scaled.
    for field, column in (("results_eps", "v3"), ("results_quarterly_eps", "v1")):
        eps = _number(rows.get("EPS", {}).get(column))
        if eps is not None:
            values[field] = eps

    # Quarter on quarter, from two figures BSE computed on the same basis.
    latest = _number(rows.get("Net Profit", {}).get("v1"))
    previous = _number(rows.get("Net Profit", {}).get("v2"))
    if latest is not None and previous is not None and previous != 0:
        values["earnings_quarterly_growth"] = round((latest - previous) / abs(previous), 6)

    labels = {
        "latest_quarter": str(payload.get("col2") or ""),
        "previous_quarter": str(payload.get("col3") or ""),
        "financial_year": str(payload.get("col4") or ""),
    }
    return values, labels


UNITS = {
    "results_eps": "INR",
    "results_quarterly_eps": "INR",
    "earnings_quarterly_growth": "fraction",
}


class BseResultsProvider:
    """Filed results, one request per stock, keyed by BSE scrip code."""

    name = "bse_results"
    endpoint = ENDPOINT

    def __init__(
        self, client: ArchivedHttpClient, concurrency: int = 4, *, instrument_reader=None
    ) -> None:
        self.client = client
        # Matches bse.py: this host tolerates far less parallelism than a bulk file.
        self.concurrency = max(1, concurrency)
        self.instrument_reader = instrument_reader
        self._codes: dict[str, str] | None = None

    def _scrip_codes(self) -> dict[str, str]:
        if self._codes is None:
            rows = self.instrument_reader.read_instruments() if self.instrument_reader else []
            self._codes = {
                row["ticker"]: str(row["bse_code"]) for row in rows if row.get("bse_code")
            }
        return self._codes

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        codes = self._scrip_codes()
        # Bound to this call: the driver reuses a provider across asyncio.run() loops, and
        # a Semaphore belongs to the loop that made it.
        limiter = asyncio.Semaphore(self.concurrency)

        async def one(ticker: str) -> None:
            code = codes.get(ticker)
            if not code:
                result.issues.append(
                    ValidationIssue(
                        code="missing_bse_code",
                        message=f"{ticker} has no BSE scrip code",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                return
            async with limiter:
                try:
                    envelope, payload = await self.client.get_json(
                        provider=self.name,
                        endpoint=ENDPOINT,
                        params={"scripcode": code, "tabtype": "RESULTS"},
                        headers=HEADERS,
                    )
                except Exception as exc:  # noqa: BLE001 - reported per ticker
                    result.issues.append(
                        ValidationIssue(
                            code="provider_request_failed",
                            message=f"BSE results failed for {ticker}: {exc}",
                            field="ticker",
                            raw_value=ticker,
                        )
                    )
                    return

            values, labels = parse_results(payload)
            if not values:
                result.issues.append(
                    ValidationIssue(
                        code="no_filed_results",
                        message=f"BSE has no filed results for {ticker}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                return

            for field, value in values.items():
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field=field,
                        value=value,
                        unit=UNITS.get(field, "INR"),
                        provider=self.name,
                        endpoint=ENDPOINT,
                        observed_at=envelope.received_at,
                        raw_request_id=envelope.request_id,
                    )
                )
            # Which quarter the figures describe, so a stale filing is visible rather
            # than silently treated as current.
            if labels.get("latest_quarter"):
                result.observations.append(
                    MetricObservation(
                        ticker=ticker,
                        field="results_period",
                        value=labels["latest_quarter"],
                        provider=self.name,
                        endpoint=ENDPOINT,
                        observed_at=envelope.received_at,
                        raw_request_id=envelope.request_id,
                    )
                )

        await asyncio.gather(*(one(ticker) for ticker in tickers))
        return result
