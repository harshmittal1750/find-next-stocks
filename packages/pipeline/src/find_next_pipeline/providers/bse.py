"""BSE company fundamentals.

The second exchange listing for most of this universe, and the only free source here that
carries P/E, P/B, ROE, net margin and EPS together without credentials. That matters
because NSE's daily file supplies exactly one field (`trailing_pe`), leaving Yahoo as a
single point of failure for everything else — and Yahoo rate-limits hard at this volume.

Two calls are involved. The security master maps an NSE symbol to a BSE scrip code and is
fetched once for the whole run; the company header is then one request per stock.

Consolidated figures are requested before standalone (`ConPE` before `PE`), but measured
across 3,994 archived responses the `Con*` fields are **never** populated by this
endpoint: 0% consolidated, ~90% standalone fallback, ~10% neither. Every value this
provider has ever produced is therefore standalone.

That matters because a holding company's standalone accounts describe the parent shell,
not the group — RELIANCE reads P/B 3.36 standalone against 1.97 consolidated. Each value
is emitted alongside a `*_basis` marker so the mixing is visible: the scoring model ranks
cross-sectionally, and a percentile computed over some standalone and some consolidated
figures compares quantities that are not the same measurement.
Values are emitted with explicit units so `normalize_observation` can range-check them:
BSE reports ROE and net margin as percentages, not fractions.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID

from find_next_pipeline.models import (
    MetricObservation,
    ProviderResult,
    Severity,
    ValidationIssue,
)
from find_next_pipeline.providers.http import ArchivedHttpClient

MASTER_ENDPOINT = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
HEADER_ENDPOINT = "https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w"

# api.bseindia.com rejects requests without a browser-shaped Referer and User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.bseindia.com/",
}

# Fields where this endpoint returns a mix of bases across the universe, so a
# cross-sectional percentile would compare consolidated against standalone readings.
# Standalone P/E runs higher (median 31.3 vs 30.0), which would penalise those names on
# the valuation factor for their accounting basis rather than for being expensive.
#
# Deliberately NOT the other accounting figures: price_to_book, roe_pct,
# profit_margin_pct and operating_margin_pct come back 100% standalone, so their
# percentiles are internally consistent. Excluding standalone there would delete the
# field outright for ~1,200 stocks and lose far more than it fixes.
MIXED_BASIS_FIELDS = frozenset({"trailing_pe", "trailing_eps"})

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _normal(symbol: object) -> str:
    return _NON_ALNUM.sub("", str(symbol).upper())


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if value in {"", "-", "--", "NA", "N/A", "null", "None"}:
            return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


def _preferred(
    payload: dict[str, Any],
    consolidated: str,
    standalone: str,
    *,
    positive: bool = False,
    nonzero: bool = False,
) -> tuple[float, str] | None:
    """Return (value, basis). Consolidated first, then standalone.

    Returns the basis rather than just the number because the caller cannot otherwise
    tell them apart, and they are not interchangeable: in practice this endpoint returns
    only standalone, so a silent value implies a consolidated reading it never had.
    """
    for key, basis in ((consolidated, "consolidated"), (standalone, "standalone")):
        value = _number(payload.get(key))
        if value is None:
            continue
        if positive and value <= 0:
            continue
        if nonzero and value == 0:
            continue
        return value, basis
    return None


def _value(pair: tuple[float, str] | None) -> float | None:
    return None if pair is None else pair[0]


class BseFundamentalsProvider:
    """Per-stock fundamentals from the BSE company header API."""

    name = "bse"

    def __init__(self, client: ArchivedHttpClient, concurrency: int = 4) -> None:
        self.client = client
        # BSE tolerates far less parallelism than a bulk file endpoint.
        self.concurrency = max(1, concurrency)
        self._master: dict[str, dict[str, Any]] | None = None

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        wanted = [t.strip().upper() for t in tickers if t and t.strip()]
        if not wanted:
            return result

        try:
            master = await self._load_master()
        except Exception as exc:
            result.issues.append(
                ValidationIssue(
                    code="provider_request_failed",
                    message=f"BSE security master unavailable: {exc}",
                    field="ticker",
                    raw_value=None,
                )
            )
            return result

        # Created per call, not in __init__: the refresh driver runs each batch under a
        # fresh asyncio.run(), and a Semaphore is bound to the loop that created it.
        # Holding one across batches makes every batch after the first fail with
        # "bound to a different event loop".
        limiter = asyncio.Semaphore(self.concurrency)

        mapped = {t: master[key] for t in wanted if (key := _normal(t)) in master}
        for ticker in wanted:
            if ticker not in mapped:
                result.issues.append(
                    ValidationIssue(
                        code="symbol_not_listed_on_bse",
                        message=f"{ticker} has no active BSE equity listing",
                        field="ticker",
                        raw_value=ticker,
                    )
                )

        fetched = await asyncio.gather(
            *(self._fetch_one(ticker, row, limiter) for ticker, row in mapped.items()),
            return_exceptions=True,
        )
        for (ticker, row), outcome in zip(mapped.items(), fetched, strict=True):
            if isinstance(outcome, BaseException):
                result.issues.append(
                    ValidationIssue(
                        code="provider_request_failed",
                        message=f"BSE request failed for {ticker}: {outcome}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            envelope_id, payload = outcome
            if not payload:
                result.issues.append(
                    ValidationIssue(
                        code="provider_empty_payload",
                        message=f"BSE returned no company header for {ticker}",
                        field="ticker",
                        raw_value=ticker,
                    )
                )
                continue
            result.observations.extend(self._observations(ticker, row, payload, envelope_id))
        return result

    async def _load_master(self) -> dict[str, dict[str, Any]]:
        """Symbol -> scrip row for every active equity, fetched once per run.

        Where a symbol maps to several listings the largest by market cap wins; the
        others are typically suspended or dual-class shells.
        """
        if self._master is not None:
            return self._master
        _envelope, payload = await self.client.get_json(
            provider=self.name,
            endpoint=MASTER_ENDPOINT,
            params={
                "Group": "",
                "Scripcode": "",
                "industry": "",
                "segment": "Equity",
                "status": "Active",
            },
            headers=HEADERS,
        )
        if not isinstance(payload, list):
            raise RuntimeError("security master returned an unexpected schema")

        best: dict[str, dict[str, Any]] = {}
        for row in payload:
            symbol = _normal(row.get("scrip_id"))
            if not symbol or not row.get("SCRIP_CD"):
                continue
            incumbent = best.get(symbol)
            if incumbent is None or (_number(row.get("Mktcap")) or -1) > (
                _number(incumbent.get("Mktcap")) or -1
            ):
                best[symbol] = row
        self._master = best
        return best

    async def _fetch_one(
        self, ticker: str, row: dict[str, Any], limiter: asyncio.Semaphore
    ) -> tuple[UUID | None, dict[str, Any] | None]:
        async with limiter:
            envelope, payload = await self.client.get_json(
                provider=self.name,
                endpoint=HEADER_ENDPOINT,
                params={"quotetype": "EQ", "scripcode": row["SCRIP_CD"], "seriesid": ""},
                headers=HEADERS,
            )
        if not isinstance(payload, dict) or not payload.get("SecurityCode"):
            return envelope.request_id, None
        return envelope.request_id, payload

    @staticmethod
    def _observations(
        ticker: str,
        row: dict[str, Any],
        payload: dict[str, Any],
        request_id: UUID | None,
    ) -> list[MetricObservation]:
        market_cap_cr = _number(row.get("Mktcap"))
        # The master reports market cap in crore; the canonical field is in rupees.
        market_cap = market_cap_cr * 1e7 if market_cap_cr is not None else None

        # Accounting-basis-bearing figures: each yields a value plus a `<field>_basis`
        # marker, so a consolidated and a standalone reading are never silently pooled
        # into the same cross-sectional percentile.
        with_basis: list[tuple[str, tuple[float, str] | None, str | None]] = [
            ("trailing_pe", _preferred(payload, "ConPE", "PE", positive=True), "ratio"),
            ("price_to_book", _preferred(payload, "ConPB", "PB", positive=True), "ratio"),
            # BSE reports these as percentages already; declaring the unit lets
            # normalize_observation range-check rather than silently rescale.
            ("roe_pct", _preferred(payload, "ConROE", "ROE", nonzero=True), "percent"),
            ("profit_margin_pct", _preferred(payload, "ConNPM", "NPM", nonzero=True), "percent"),
            # OPM is already in the payload we fetch and was simply discarded.
            ("operating_margin_pct", _preferred(payload, "ConOPM", "OPM", nonzero=True), "percent"),
            ("trailing_eps", _preferred(payload, "ConEPS", "EPS", nonzero=True), "INR"),
        ]
        candidates: list[tuple[str, Any, str | None]] = [
            *((field, _value(pair), unit) for field, pair, unit in with_basis),
            *(
                (f"{field}_basis", pair[1], None)
                for field, pair, _unit in with_basis
                if pair is not None
            ),
            ("market_cap", market_cap, "INR"),
            ("sector", (payload.get("Sector") or "").strip() or None, None),
            (
                "industry",
                (payload.get("ISubGroup") or payload.get("Industry") or "").strip() or None,
                None,
            ),
        ]

        # Which fields arrived on a non-comparable basis, so the observation can be kept
        # and flagged rather than dropped — the raw reading stays auditable.
        standalone_mixed = {
            field for field, pair, _unit in with_basis
            if pair is not None and pair[1] == "standalone" and field in MIXED_BASIS_FIELDS
        }

        source = f"{HEADER_ENDPOINT}?quotetype=EQ&scripcode={row.get('SCRIP_CD')}"
        return [
            MetricObservation(
                ticker=ticker,
                field=field,
                value=value,
                unit=unit,
                is_valid=field not in standalone_mixed,
                issues=(
                    [
                        ValidationIssue(
                            code="mixed_accounting_basis",
                            message=(
                                f"{field} is standalone while most of the universe is "
                                "consolidated; excluded so the percentile compares like "
                                "with like"
                            ),
                            severity=Severity.WARNING,
                            field=field,
                            raw_value=value,
                        )
                    ]
                    if field in standalone_mixed
                    else []
                ),
                provider=BseFundamentalsProvider.name,
                endpoint=source,
                raw_request_id=request_id,
            )
            for field, value, unit in candidates
            if value is not None
        ]
