"""Period-aware statements and filing-category ownership from Upstox."""

from __future__ import annotations

import asyncio
import calendar
import math
from datetime import date, datetime
from typing import Any

import httpx

from find_next_pipeline.models import MetricObservation, ProviderResult, Severity, ValidationIssue
from find_next_pipeline.providers.rate_limit import ACCOUNT_WINDOW

BASE = "https://api.upstox.com/v2/fundamentals"
STATEMENTS = (
    ("balance-sheet", "yearly"),
    ("cash-flow", "yearly"),
    ("income-statement", "yearly"),
    ("income-statement", "quarterly"),
)
LINE_FIELDS = {
    "Total Assets": "total_assets",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Non-Current Liabilities": "noncurrent_liabilities",
    "Total Revenue": "total_revenue",
    "Revenue": "revenue",
    "Profit After Tax": "net_profit",
    "Profit Before Tax": "profit_before_tax",
    "Tax": "tax_expense",
    "Cash flow from Operations": "operating_cash_flow",
    "Cash flow from Investing": "investing_cash_flow",
    "Cash flow from Financing": "financing_cash_flow",
    "Cash (End of the year)": "cash_and_equivalents",
}


def period_date(value: str) -> date:
    parsed = datetime.strptime(value, "%b %Y").date()
    return parsed.replace(day=calendar.monthrange(parsed.year, parsed.month)[1])


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def history_values(history: list[dict]) -> dict[date, float]:
    result = {}
    latest = None
    for point in history:
        period = period_date(point["period"])
        latest = max(latest, period) if latest else period
        value = number(point.get("value"))
        if value is None:
            continue
        if period in result and result[period] != value:
            raise ValueError("Conflicting values for one reporting period")
        result[period] = value
    # A missing newest value is a gap, not permission to present an older year as current.
    return result if latest in result else {}


def category_histories(rows: list[dict], key: str) -> dict[str, dict[date, float]]:
    result = {}
    for row in rows:
        category = row[key]
        if category in result:
            raise ValueError(f"Duplicate statement category: {category}")
        result[category] = history_values(row.get("history", []))
    return result


def parse_fundamentals(
    payload: Any,
    *,
    ticker: str,
    endpoint: str,
    request_id,
    observed_at: datetime,
    quarterly: bool = False,
) -> list[MetricObservation]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Unsuccessful Upstox statement response")
    data = payload.get("data")
    today = observed_at.date()
    result = []

    def emit(field, value, unit, period, *, basis=None, issues=()):
        problems = list(issues)
        max_age = 200 if quarterly or endpoint.endswith("share-holdings") else 550
        if period > today or (today - period).days > max_age:
            problems.append(
                ValidationIssue(
                    code="stale_reporting_period",
                    field=field,
                    message=f"Reporting period {period} is not current",
                    raw_value=str(period),
                )
            )
        if basis and basis != "consolidated":
            problems.append(
                ValidationIssue(
                    code="mixed_accounting_basis",
                    field=field,
                    message="Consolidated statement was requested",
                    raw_value=basis,
                )
            )
        result.append(
            MetricObservation(
                ticker=ticker,
                field=field,
                value=value,
                unit=unit,
                provider="upstox_shareholding"
                if endpoint.endswith("share-holdings")
                else "upstox_statements",
                endpoint=endpoint,
                raw_request_id=request_id,
                observed_at=observed_at,
                period_end=period,
                accounting_basis=basis,
                is_valid=not problems,
                issues=problems,
            )
        )

    if endpoint.endswith("share-holdings"):
        if not isinstance(data, list) or not data:
            raise ValueError("No shareholding categories returned")
        categories = category_histories(data, "category")
        expected = {"promoters", "fii", "other_dii", "mutual_funds", "retail_and_other"}
        if not expected.issubset(categories) or any(not categories[k] for k in expected):
            raise ValueError("Incomplete shareholding categories")
        # All categories must describe the latest SAME quarter; never mix dates.
        periods = {max(categories[key]) for key in expected}
        if len(periods) != 1:
            raise ValueError("Shareholding categories have inconsistent reporting periods")
        period = periods.pop()
        values = {key: categories[key][period] for key in expected}
        valid = (
            all(0 <= v <= 100 for v in values.values()) and abs(sum(values.values()) - 100) <= 0.5
        )
        issues = (
            []
            if valid
            else [
                ValidationIssue(
                    code="ownership_total_invalid",
                    message="Ownership categories must total 100%",
                    raw_value=values,
                )
            ]
        )
        for key, field in (
            ("promoters", "promoter_pct"),
            ("fii", "fii_pct"),
            ("other_dii", "other_dii_pct"),
            ("mutual_funds", "mutual_funds_pct"),
        ):
            emit(field, values[key], "percent", period, issues=issues)
        emit(
            "institutional_pct",
            sum(values[k] for k in ("fii", "other_dii", "mutual_funds")),
            "percent",
            period,
            issues=issues,
        )
        return result

    if not isinstance(data, dict) or data.get("units_in") != "crore":
        raise ValueError("Missing statement data or unexpected monetary unit")
    basis = data.get("type")
    if basis not in {"consolidated", "standalone"}:
        raise ValueError("Missing accounting basis")
    if data.get("time_period") != ("quarterly" if quarterly else "yearly"):
        raise ValueError("Unexpected reporting frequency")
    # The full_statement section is ALWAYS annual, even on a quarterly request.
    lines = (
        {}
        if quarterly
        else category_histories(data.get("full_statement", []), "particular")
    )
    for label, values in lines.items():
        if not values or label not in LINE_FIELDS:
            continue
        period = max(values)
        emit(LINE_FIELDS[label], values[period] * 1e7, "INR", period, basis=basis)
    summaries = category_histories(data.get("income_statement", []), "category")
    if quarterly:
        for category in ("revenue", "operating_profit", "net_profit"):
            values = summaries.get(category, {})
            if values:
                period = max(values)
                emit(f"quarterly_{category}", values[period] * 1e7, "INR", period, basis=basis)
    elif lines.get("Revenue"):
        # The summary's "revenue" includes other income. Use the explicit revenue
        # line for operating-revenue growth and margins when the statement supplies it.
        summaries["revenue"] = lines["Revenue"]
    if endpoint.endswith("balance-sheet"):
        assets, liabilities = lines.get("Current Assets", {}), lines.get("Current Liabilities", {})
        if assets and liabilities and max(assets) == max(liabilities):
            period = max(assets)
            if assets[period] >= 0 and liabilities[period] > 0:
                emit(
                    "current_ratio",
                    assets[period] / liabilities[period],
                    "ratio",
                    period,
                    basis=basis,
                )
    for category, field in (("revenue", "revenue_growth"), ("net_profit", "earnings_growth")):
        values = summaries.get(category, {})
        if len(values) < 2:
            continue
        period = max(values)
        previous = period.replace(day=1, year=period.year - 1)
        previous = previous.replace(day=calendar.monthrange(previous.year, previous.month)[1])
        # Growth from zero or a loss is not comparable to positive-base growth.
        if previous in values and values[previous] > 0:
            target = (
                "earnings_quarterly_growth" if quarterly and category == "net_profit" else field
            )
            if not quarterly or category == "net_profit":
                emit(target, values[period] / values[previous] - 1, "fraction", period, basis=basis)
    revenues = summaries.get("revenue", {})
    profits = summaries.get("net_profit", {})
    if not quarterly and revenues and profits and max(revenues) == max(profits):
        period = max(revenues)
        if revenues[period] > 0:
            emit(
                "net_profit_margin",
                profits[period] / revenues[period],
                "fraction",
                period,
                basis=basis,
            )
    if not result:
        raise ValueError("No supported financial statement fields returned")
    return result


class UpstoxStatementsProvider:
    name = "upstox_statements"
    endpoints = STATEMENTS

    def __init__(
        self, client, access_token: str, instrument_reader, *, rate_limits=True,
        max_age_days: int = 30,
    ):
        self.client, self.access_token, self.instrument_reader = (
            client,
            access_token,
            instrument_reader,
        )
        self.rate_limits = rate_limits
        # Statements are filed quarterly, so anything fetched inside this window is still
        # current. Set to 0 to force a full refetch.
        self.max_age_days = max_age_days
        self._requests = ACCOUNT_WINDOW
        self._instruments = None

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        if self._instruments is None:
            self._instruments = {r["ticker"]: r for r in self.instrument_reader.read_instruments()}
        result = ProviderResult(provider=self.name)

        # Resume rather than restart. Without this a killed run loses everything it did.
        if self.max_age_days > 0 and hasattr(self.instrument_reader, "fresh_tickers"):
            fresh = self.instrument_reader.fresh_tickers(self.name, self.max_age_days)
            tickers = [ticker for ticker in tickers if ticker not in fresh]
            if not tickers:
                return result

        limiter, lock = asyncio.Semaphore(6), asyncio.Lock()

        async def request(ticker, endpoint_name, frequency):
            instrument = self._instruments.get(ticker, {})
            isin = instrument.get("isin")
            if not isin:
                result.issues.append(
                    ValidationIssue(
                        code="missing_isin", message="No verified ISIN", raw_value=ticker
                    )
                )
                return
            endpoint = f"{BASE}/{isin}/{endpoint_name}"
            params = (
                {} if endpoint_name == "share-holdings" else {"type": "consolidated", "fs": "true"}
            )
            if endpoint_name == "income-statement":
                params["time_period"] = frequency
            async with limiter:
                for attempt in range(3):
                    if self.rate_limits:
                        async with lock:
                            await self._requests.acquire()
                    try:
                        envelope, payload = await self.client.get_json(
                            provider=self.name,
                            endpoint=endpoint,
                            params=params,
                            headers={
                                "Accept": "application/json",
                                "Authorization": f"Bearer {self.access_token}",
                            },
                        )
                        observations = parse_fundamentals(
                            payload,
                            ticker=ticker,
                            endpoint=endpoint,
                            request_id=envelope.request_id,
                            observed_at=envelope.received_at,
                            quarterly=frequency == "quarterly",
                        )
                        result.observations.extend(observations)
                        for observation in observations:
                            if not observation.is_valid:
                                result.issues.extend(observation.issues)
                        return
                    except Exception as exc:
                        retry = isinstance(exc, httpx.HTTPStatusError) and (
                            exc.response.status_code == 429 or exc.response.status_code >= 500
                        )
                        if retry and attempt < 2:
                            await asyncio.sleep(2**attempt)
                            continue
                        result.issues.append(
                            ValidationIssue(
                                code="statement_unavailable",
                                severity=Severity.WARNING,
                                message=f"{endpoint_name}: {type(exc).__name__}: {exc}",
                                raw_value=ticker,
                            )
                        )
                        return

        await asyncio.gather(
            *(
                request(ticker, endpoint, frequency)
                for ticker in tickers
                for endpoint, frequency in self.endpoints
            )
        )
        return result


class UpstoxShareholdingProvider(UpstoxStatementsProvider):
    name = "upstox_shareholding"
    endpoints = (("share-holdings", "quarterly"),)
