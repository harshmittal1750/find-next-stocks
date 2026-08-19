from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient


class NseValuationProvider:
    """Official NSE daily P/E file for the whole equity universe."""

    name = "nse"
    endpoint_template = (
        "https://nsearchives.nseindia.com/content/equities/peDetail/PE_{stamp}.csv"
    )

    def __init__(self, client: ArchivedHttpClient, lookback_days: int = 12) -> None:
        self.client = client
        self.lookback_days = lookback_days

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        ticker_set = {ticker.strip().upper() for ticker in tickers}
        for offset in range(self.lookback_days):
            trade_date = date.today() - timedelta(days=offset)
            if trade_date.weekday() >= 5:
                continue
            endpoint = self.endpoint_template.format(stamp=trade_date.strftime("%d%m%y"))
            try:
                envelope, body = await self.client.get_text(
                    provider=self.name,
                    endpoint=endpoint,
                    headers={"Accept": "text/csv,*/*", "User-Agent": "find-next-stocks/1.0"},
                )
            except Exception:
                continue
            observations = self._parse(body, ticker_set, endpoint, envelope.request_id, trade_date)
            if observations:
                result.observations.extend(observations)
                return result

        result.issues.append(
            ValidationIssue(
                code="provider_request_failed",
                message="NSE did not return a usable daily P/E file in the lookback window",
                field="ticker",
                raw_value=len(ticker_set),
            )
        )
        return result

    @staticmethod
    def _parse(
        body: str,
        tickers: set[str],
        endpoint: str,
        request_id,
        trade_date: date,
    ) -> list[MetricObservation]:
        reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
        if not reader.fieldnames:
            return []
        normalized = {
            "".join(character for character in name.upper() if character.isalnum()): name
            for name in reader.fieldnames
        }
        symbol_column = normalized.get("SYMBOL")
        pe_columns = [
            column
            for key in ("ADJUSTEDPE", "SYMBOLPE")
            if (column := normalized.get(key)) is not None
        ]
        if symbol_column is None or not pe_columns:
            return []

        observed_at = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC)
        observations: list[MetricObservation] = []
        for row in reader:
            ticker = str(row.get(symbol_column) or "").strip().upper()
            if ticker not in tickers:
                continue
            value = None
            for column in pe_columns:
                try:
                    candidate = float(str(row.get(column) or "").replace(",", ""))
                except ValueError:
                    continue
                if candidate > 0:
                    value = candidate
                    break
            if value is None:
                continue
            observations.append(
                MetricObservation(
                    ticker=ticker,
                    field="trailing_pe",
                    value=value,
                    unit="ratio",
                    provider=NseValuationProvider.name,
                    endpoint=endpoint,
                    observed_at=observed_at,
                    raw_request_id=request_id,
                )
            )
        return observations
