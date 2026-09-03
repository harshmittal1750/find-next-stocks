from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.bse import BseFundamentalsProvider

MASTER = [
    {"scrip_id": "RELIANCE", "SCRIP_CD": "500325", "Mktcap": "1777084"},
    # Same symbol listed twice; the larger listing should win.
    {"scrip_id": "TATASTEEL", "SCRIP_CD": "111111", "Mktcap": "10"},
    {"scrip_id": "TATASTEEL", "SCRIP_CD": "500470", "Mktcap": "180000"},
]
HEADER = {
    "SecurityCode": "500325",
    "ConPE": "23.79", "PE": "99.9",
    "ConPB": "1.85", "PB": "9.9",
    "ConROE": "8.93", "ROE": "0",
    "ConNPM": "7.4", "NPM": "0",
    "ConEPS": "55.2", "EPS": "0",
    "Sector": "Energy", "ISubGroup": "Refineries",
}


class FakeClient:
    """Returns the master once, then a company header per stock."""

    def __init__(self, header: dict | None = HEADER) -> None:
        self.header = header
        self.calls: list[str] = []

    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        self.calls.append(endpoint)
        envelope = SimpleNamespace(request_id=uuid4())
        if "ListofScripData" in endpoint:
            return envelope, MASTER
        return envelope, self.header


class SlowClient(FakeClient):
    """Yields to the event loop so concurrent tasks actually queue on the limiter."""

    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        await asyncio.sleep(0.01)
        return await super().get_json(
            provider=provider, endpoint=endpoint, params=params, headers=headers
        )


def _fetch(client, tickers):
    return asyncio.run(BseFundamentalsProvider(client).fetch(tickers))


def test_consolidated_figures_are_preferred_over_standalone() -> None:
    result = _fetch(FakeClient(), ["RELIANCE"])
    values = {o.field: o.value for o in result.observations}
    assert values["trailing_pe"] == 23.79      # ConPE, not PE=99.9
    assert values["price_to_book"] == 1.85
    assert values["roe_pct"] == 8.93           # ConROE, not ROE=0


def test_percentages_are_declared_as_percent_not_fraction() -> None:
    """BSE already reports percentages; mislabelling would rescale by 100."""
    result = _fetch(FakeClient(), ["RELIANCE"])
    units = {o.field: o.unit for o in result.observations}
    assert units["roe_pct"] == "percent"
    assert units["profit_margin_pct"] == "percent"


def test_market_cap_is_converted_from_crore_to_rupees() -> None:
    result = _fetch(FakeClient(), ["RELIANCE"])
    values = {o.field: o.value for o in result.observations}
    assert values["market_cap"] == 1777084 * 1e7


def test_duplicate_listings_resolve_to_the_largest() -> None:
    client = FakeClient()
    _fetch(client, ["TATASTEEL"])
    # The 500470 listing (180,000 cr) must win over the 10 cr shell.
    assert any("scripcode=500470" in o for o in [
        obs.endpoint for obs in _fetch(client, ["TATASTEEL"]).observations
    ])


def test_unlisted_symbol_is_reported_not_silently_dropped() -> None:
    result = _fetch(FakeClient(), ["NOTONBSE"])
    assert not result.observations
    assert [i.code for i in result.issues] == ["symbol_not_listed_on_bse"]
    assert result.issues[0].raw_value == "NOTONBSE"


def test_empty_header_is_reported_as_an_issue() -> None:
    result = _fetch(FakeClient(header={}), ["RELIANCE"])
    assert not result.observations
    assert result.issues[0].code == "provider_empty_payload"


def test_master_is_fetched_once_for_the_whole_batch() -> None:
    client = FakeClient()
    _fetch(client, ["RELIANCE", "TATASTEEL"])
    assert sum("ListofScripData" in c for c in client.calls) == 1


def test_provider_survives_being_reused_across_event_loops() -> None:
    """Regression: the refresh driver calls asyncio.run() once per batch.

    An asyncio.Semaphore is bound to the loop that created it, so holding one on the
    instance made every batch after the first fail with "bound to a different event
    loop". A real run lost 1,183 of 1,353 stocks to this; every unit test passed,
    because they each used a single asyncio.run().
    """
    # Contention is what binds a Semaphore to a loop: an uncontended acquire() returns
    # without ever creating a future, so a batch smaller than `concurrency` passes even
    # with the bug present. Force more in-flight work than the limiter allows.
    # `mapped` is keyed by ticker, so repeating a symbol does NOT create more work.
    # Two distinct tickers against a limit of one is what forces the second to wait,
    # and waiting is what binds the semaphore to a loop.
    client = SlowClient()
    provider = BseFundamentalsProvider(client, concurrency=1)
    tickers = ["RELIANCE", "TATASTEEL"]

    first = asyncio.run(provider.fetch(tickers))
    second = asyncio.run(provider.fetch(tickers))

    for batch in (first, second):
        assert batch.observations, "a later batch produced nothing"
        failures = [i.message for i in batch.issues if i.code == "provider_request_failed"]
        assert not failures, failures

    # The master is cached on the instance and must survive reuse too.
    assert sum("ListofScripData" in c for c in client.calls) == 1


def test_operating_margin_is_emitted_as_percent() -> None:
    """OPM was already in the payload we fetch and was being discarded."""
    result = _fetch(FakeClient({**HEADER, "OPM": "14.24", "ConOPM": "0.00"}), ["RELIANCE"])
    by_field = {o.field: o for o in result.observations}
    # ConOPM is 0.00 here, which _preferred skips as not-meaningful, so standalone wins.
    assert by_field["operating_margin_pct"].value == 14.24
    assert by_field["operating_margin_pct"].unit == "percent"


def test_basis_marker_accompanies_each_accounting_figure() -> None:
    """Measured over 3,994 archived responses, Con* is never populated by this endpoint.

    Every value is standalone, and a standalone P/B is not the same measurement as a
    consolidated one — RELIANCE reads 3.36 vs 1.97. Without a marker the two pool into
    one percentile silently.
    """
    result = _fetch(FakeClient(), ["RELIANCE"])
    by_field = {o.field: o.value for o in result.observations}
    assert by_field["price_to_book_basis"] == "consolidated"  # HEADER sets ConPB
    assert by_field["trailing_pe_basis"] == "consolidated"


def test_standalone_fallback_is_labelled_as_such() -> None:
    # The fixture's standalone ROE is "0", so give it a real one to fall back to.
    header = {**HEADER, "ConPB": None, "ConROE": "0", "ROE": "8.93", "ConNPM": "0"}
    result = _fetch(FakeClient(header), ["RELIANCE"])
    by_field = {o.field: o.value for o in result.observations}
    assert by_field["price_to_book"] == 9.9            # fell through to standalone PB
    assert by_field["price_to_book_basis"] == "standalone"
    assert by_field["roe_pct_basis"] == "standalone"


def test_absent_figure_emits_no_basis_marker() -> None:
    header = {**HEADER, "ConPB": None, "PB": None}
    result = _fetch(FakeClient(header), ["RELIANCE"])
    fields = {o.field for o in result.observations}
    assert "price_to_book" not in fields
    assert "price_to_book_basis" not in fields


def test_standalone_pe_is_flagged_not_dropped() -> None:
    """A standalone P/E is kept as an observation but excluded from `current_metrics`.

    Retaining it means the raw reading stays auditable; marking it invalid keeps it out
    of a percentile where the other 1,029 stocks are on a consolidated basis.
    """
    header = {**HEADER, "ConPE": None, "PE": "99.9", "ConEPS": None, "EPS": "12.0"}
    result = _fetch(FakeClient(header), ["RELIANCE"])
    by_field = {o.field: o for o in result.observations}

    pe = by_field["trailing_pe"]
    assert pe.value == 99.9            # the reading is preserved
    assert pe.is_valid is False        # but will not reach current_metrics
    assert pe.issues[0].code == "mixed_accounting_basis"
    assert by_field["trailing_eps"].is_valid is False


def test_uniformly_standalone_fields_are_not_excluded() -> None:
    """P/B and ROE come back 100% standalone, so excluding them deletes the field."""
    header = {**HEADER, "ConPB": None, "PB": "3.36", "ConROE": None, "ROE": "8.93"}
    by_field = {o.field: o for o in _fetch(FakeClient(header), ["RELIANCE"]).observations}
    assert by_field["price_to_book"].is_valid is True
    assert by_field["roe_pct"].is_valid is True


def test_consolidated_pe_stays_valid() -> None:
    by_field = {o.field: o for o in _fetch(FakeClient(), ["RELIANCE"]).observations}
    assert by_field["trailing_pe"].is_valid is True
