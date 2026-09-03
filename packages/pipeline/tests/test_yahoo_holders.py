from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.yahoo_holders import YahooHoldersProvider


class FakeStore:
    def save(self, **kwargs):
        return SimpleNamespace(request_id=uuid4()), None


def factory_for(payloads: dict[str, dict]):
    def make(symbol: str):
        return SimpleNamespace(info=payloads.get(symbol, {}))
    return make


def run(payloads, tickers, **kw):
    provider = YahooHoldersProvider(
        raw_store=FakeStore(), ticker_factory=factory_for(payloads), **kw
    )
    return asyncio.run(provider.fetch(list(tickers)))


def test_fractions_become_percentages_and_keep_the_legacy_names() -> None:
    result = run({"TCS.NS": {"heldPercentInsiders": 0.71794,
                             "heldPercentInstitutions": 0.17609}}, ["TCS"])
    by_field = {o.field: o for o in result.observations}
    # Scoring's smart_money group reads the percent form.
    assert by_field["promoter_pct"].value == 71.794
    assert by_field["promoter_pct"].unit == "percent"
    # The dashboard still carries the raw fraction under Yahoo's own name.
    assert by_field["heldPercentInsiders"].value == 0.71794
    assert by_field["heldPercentInsiders"].unit == "fraction"
    assert by_field["institutional_pct"].value == 17.609


def test_a_company_with_no_promoter_reads_near_zero_not_missing() -> None:
    """HDFCBANK is widely held; 0.15% is the answer, not an absent value."""
    result = run({"HDFCBANK.NS": {"heldPercentInsiders": 0.00153,
                                  "heldPercentInstitutions": 0.6099}}, ["HDFCBANK"])
    by_field = {o.field: o.value for o in result.observations}
    assert by_field["promoter_pct"] == 0.153
    assert by_field["institutional_pct"] == 60.99


def test_symbol_gets_the_nse_suffix() -> None:
    seen: list[str] = []

    def make(symbol: str):
        seen.append(symbol)
        return SimpleNamespace(info={"heldPercentInsiders": 0.5})

    provider = YahooHoldersProvider(raw_store=FakeStore(), ticker_factory=make)
    asyncio.run(provider.fetch(["RELIANCE"]))
    assert seen == ["RELIANCE.NS"]


def test_missing_percentages_are_reported_not_silently_skipped() -> None:
    result = run({"AAA.NS": {"someOtherField": 1}}, ["AAA"])
    assert result.observations == []
    assert result.issues[0].code == "provider_empty_payload"


def test_a_failing_ticker_does_not_stop_the_batch() -> None:
    def make(symbol: str):
        if symbol == "BAD.NS":
            raise RuntimeError("429 Too Many Requests")
        return SimpleNamespace(info={"heldPercentInsiders": 0.4})

    provider = YahooHoldersProvider(raw_store=FakeStore(), ticker_factory=make)
    result = asyncio.run(provider.fetch(["GOOD", "BAD"]))
    assert {o.ticker for o in result.observations} == {"GOOD"}
    assert result.issues[0].code == "provider_request_failed"
    assert result.issues[0].raw_value == "BAD"


def test_provider_survives_reuse_across_event_loops() -> None:
    """Same semaphore-per-loop trap the BSE provider hit; the limiter is per call."""
    payloads = {"AAA.NS": {"heldPercentInsiders": 0.4},
                "BBB.NS": {"heldPercentInsiders": 0.6}}
    provider = YahooHoldersProvider(
        raw_store=FakeStore(), ticker_factory=factory_for(payloads), concurrency=1
    )
    first = asyncio.run(provider.fetch(["AAA", "BBB"]))
    second = asyncio.run(provider.fetch(["AAA", "BBB"]))
    for batch in (first, second):
        assert batch.observations
        assert not [i for i in batch.issues if i.code == "provider_request_failed"]
