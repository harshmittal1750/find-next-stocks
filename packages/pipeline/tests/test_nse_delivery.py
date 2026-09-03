from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.nse_delivery import NseDeliveryProvider

HEADER = "SYMBOL, SERIES, DELIV_PER, TTL_TRD_QNTY"


def session(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows])


class FakeClient:
    """Serves the same file for every session date."""

    def __init__(self, body: str, fail_after: int | None = None) -> None:
        self.body = body
        self.fail_after = fail_after
        self.calls = 0

    async def get_text(self, *, provider, endpoint, params=None, headers=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("404 not found")
        return SimpleNamespace(request_id=uuid4()), self.body


def run(client, tickers=("AAA",), **kw):
    return asyncio.run(NseDeliveryProvider(client, **kw).fetch(list(tickers)))


def test_emits_average_and_trend() -> None:
    result = run(FakeClient(session(["AAA, EQ, 55.0, 1000"])))
    values = {o.field: o.value for o in result.observations}
    assert values["avg_delivery_pct"] == 55.0
    # Every session identical, so recent and prior match: no trend.
    assert values["delivery_trend"] == 0.0


def test_trend_is_signed_percentage_points_not_a_percentage() -> None:
    result = run(FakeClient(session(["AAA, EQ, 55.0, 1000"])))
    units = {o.field: o.unit for o in result.observations}
    # A difference of two percentages is unbounded and signed; labelling it "percent"
    # would put it under the 0-100 ownership range check.
    assert units["delivery_trend"] == "percentage_points"
    assert units["avg_delivery_pct"] == "percent"


def test_non_equity_series_is_ignored() -> None:
    body = session(["AAA, EQ, 55.0, 1000", "AAA, N1, 99.0, 5"])
    values = {o.field: o.value for o in run(FakeClient(body)).observations}
    assert values["avg_delivery_pct"] == 55.0  # the N1 debt series must not pull it up


def test_most_liquid_row_wins_on_a_duplicated_day() -> None:
    body = session(["AAA, EQ, 20.0, 10", "AAA, BE, 80.0, 9000"])
    values = {o.field: o.value for o in run(FakeClient(body)).observations}
    assert values["avg_delivery_pct"] == 80.0


def test_missing_delivery_cell_is_skipped_not_zeroed() -> None:
    # NSE writes "-" when a symbol has no delivery data; treating that as 0% would
    # invent distribution that did not happen.
    body = session(["AAA, EQ, -, 1000"])
    assert run(FakeClient(body)).observations == []


def test_too_few_sessions_reports_an_issue() -> None:
    result = run(FakeClient(session(["AAA, EQ, 55.0, 1000"]), fail_after=2))
    assert result.observations == []
    assert result.issues[0].code == "insufficient_delivery_sessions"


def test_unrequested_symbols_are_not_returned() -> None:
    body = session(["AAA, EQ, 55.0, 1000", "ZZZ, EQ, 10.0, 1000"])
    tickers = {o.ticker for o in run(FakeClient(body)).observations}
    assert tickers == {"AAA"}


def test_one_request_per_session_not_per_stock() -> None:
    client = FakeClient(session(["AAA, EQ, 55.0, 1000", "BBB, EQ, 40.0, 1000"]))
    run(client, tickers=("AAA", "BBB"), sessions=20)
    assert client.calls <= 30  # ~20 sessions plus weekends skipped, never 2 x 1353
