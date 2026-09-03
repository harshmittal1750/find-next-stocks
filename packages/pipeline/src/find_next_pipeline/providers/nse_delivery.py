"""Delivery percentage from the NSE whole-market bhavcopy.

Delivery percentage is the share of a day's traded volume that settled as actual
delivery rather than being squared off intraday. A stock quietly accumulated by people
who intend to hold it shows rising delivery; one churned by traders does not. It is the
closest free proxy this pipeline has for institutional accumulation, which is why the
scoring model's smart-money group leans on it.

One request returns every symbol for a session, so ~20 requests cover the whole universe
for a month — the same bulk-file shape as ``nse.py``, not one call per stock.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import fmean

from find_next_pipeline.models import MetricObservation, ProviderResult, ValidationIssue
from find_next_pipeline.providers.http import ArchivedHttpClient

# Enough sessions to split into a recent window and a prior one and still have a
# meaningful average on each side.
DEFAULT_SESSIONS = 20
RECENT_WINDOW = 5
MIN_SESSIONS = 4


class NseDeliveryProvider:
    """Average delivery percentage and its recent trend, per symbol."""

    name = "nse_delivery"
    endpoint_template = (
        "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv"
    )

    def __init__(
        self,
        client: ArchivedHttpClient,
        sessions: int = DEFAULT_SESSIONS,
        max_lookback_days: int = 45,
    ) -> None:
        self.client = client
        self.sessions = sessions
        self.max_lookback_days = max_lookback_days

    async def fetch(self, tickers: list[str]) -> ProviderResult:
        result = ProviderResult(provider=self.name)
        wanted = {t.strip().upper() for t in tickers if t and t.strip()}
        if not wanted:
            return result

        # {symbol: [(trade_date, delivery_pct, volume)]}
        history: dict[str, list[tuple[date, float, float]]] = defaultdict(list)
        collected = 0
        for offset in range(self.max_lookback_days):
            if collected >= self.sessions:
                break
            trade_date = date.today() - timedelta(days=offset)
            if trade_date.weekday() >= 5:
                continue
            endpoint = self.endpoint_template.format(stamp=trade_date.strftime("%d%m%Y"))
            try:
                _envelope, body = await self.client.get_text(
                    provider=self.name,
                    endpoint=endpoint,
                    headers={
                        "Accept": "text/csv,*/*",
                        "User-Agent": "find-next-stocks/1.0",
                    },
                )
            except Exception:
                # A holiday or a not-yet-published file is ordinary, not an error. Only
                # the total absence of sessions is worth reporting.
                continue
            if self._collect(body, wanted, trade_date, history):
                collected += 1

        if collected < MIN_SESSIONS:
            result.issues.append(
                ValidationIssue(
                    code="insufficient_delivery_sessions",
                    message=(
                        f"Found {collected} usable bhavcopy sessions in the last "
                        f"{self.max_lookback_days} days; need {MIN_SESSIONS}"
                    ),
                    field="ticker",
                    raw_value=collected,
                )
            )
            return result

        observed_at = datetime.now(UTC)
        for symbol, rows in history.items():
            observations = self._summarise(symbol, rows, observed_at)
            result.observations.extend(observations)
        return result

    @staticmethod
    def _collect(
        body: str,
        wanted: set[str],
        trade_date: date,
        history: dict[str, list[tuple[date, float, float]]],
    ) -> bool:
        """Parse one session's file. Returns False if it is unusable."""
        reader = csv.DictReader(io.StringIO(body.lstrip("﻿")))
        if not reader.fieldnames:
            return False
        # The published header carries leading spaces on most columns.
        columns = {name.strip().upper(): name for name in reader.fieldnames}
        symbol_col = columns.get("SYMBOL")
        delivery_col = columns.get("DELIV_PER")
        volume_col = columns.get("TTL_TRD_QNTY")
        series_col = columns.get("SERIES")
        if symbol_col is None or delivery_col is None:
            return False

        found = False
        for row in reader:
            symbol = str(row.get(symbol_col) or "").strip().upper()
            if symbol not in wanted:
                continue
            # Only the rolling-settlement equity series is comparable.
            if series_col and str(row.get(series_col) or "").strip().upper() not in {"EQ", "BE"}:
                continue
            try:
                delivery = float(str(row.get(delivery_col) or "").strip())
            except ValueError:
                continue  # "-" appears for symbols with no delivery data that day
            try:
                volume = float(str(row.get(volume_col) or "0").replace(",", "").strip())
            except (ValueError, AttributeError):
                volume = 0.0
            history[symbol].append((trade_date, delivery, volume))
            found = True
        return found

    @staticmethod
    def _summarise(
        symbol: str,
        rows: list[tuple[date, float, float]],
        observed_at: datetime,
    ) -> list[MetricObservation]:
        # A symbol quoted in more than one series on a day keeps its most-liquid row.
        by_day: dict[date, tuple[float, float]] = {}
        for trade_date, delivery, volume in rows:
            existing = by_day.get(trade_date)
            if existing is None or volume > existing[1]:
                by_day[trade_date] = (delivery, volume)
        if len(by_day) < MIN_SESSIONS:
            return []

        newest_first = [by_day[day][0] for day in sorted(by_day, reverse=True)]
        recent = newest_first[:RECENT_WINDOW]
        prior = newest_first[RECENT_WINDOW:] or newest_first[1:]

        # Positive trend = delivery rising against its own recent past, i.e. accumulation.
        values = {
            "avg_delivery_pct": round(fmean(newest_first), 1),
            "delivery_recent_pct": round(fmean(recent), 1),
            "delivery_trend": round(fmean(recent) - fmean(prior), 1),
        }
        return [
            MetricObservation(
                ticker=symbol,
                field=field,
                value=value,
                # A trend is a difference between two percentages, so it is signed and
                # unbounded — not a share of anything, and not range-checked as one.
                unit="percentage_points" if field == "delivery_trend" else "percent",
                provider=NseDeliveryProvider.name,
                observed_at=observed_at,
            )
            for field, value in values.items()
        ]
