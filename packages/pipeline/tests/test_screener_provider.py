from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.screener import (
    ScreenerReturnsProvider,
    parse_headline_returns,
)

PAGE = """
<li class="flex flex-space-between"><span class="name">ROCE</span>
<span class="nowrap value"><span class="number">8.58</span> %</span></li>
<li class="flex flex-space-between"><span class="name">ROE</span>
<span class="nowrap value"><span class="number">12.4</span> %</span></li>
"""


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    async def get_text(self, **kwargs):
        ticker = kwargs["endpoint"].split("/")[-3]
        page = self.pages[ticker]
        if isinstance(page, Exception):
            raise page
        return SimpleNamespace(request_id=uuid4()), page


class FakeReviewReader:
    def roe_review_tickers(self, tickers):
        assert tickers == ["GOOD", "SKIP"]
        return ["GOOD"]


def test_parser_reads_only_named_headline_returns() -> None:
    assert parse_headline_returns(PAGE) == {"roce_pct": 8.58, "roe_pct": 12.4}


def test_tatacap_emits_distinct_roe_and_roce() -> None:
    result = asyncio.run(ScreenerReturnsProvider(FakeClient({"TATACAP": PAGE})).fetch(["TATACAP"]))
    by_field = {item.field: item for item in result.observations}
    assert by_field["roe_pct"].value == 12.4
    assert by_field["roce_pct"].value == 8.58
    assert by_field["roe_pct"].raw_request_id is not None


def test_schema_change_is_reported_instead_of_parsing_another_number() -> None:
    result = asyncio.run(ScreenerReturnsProvider(FakeClient({"AAA": "<b>42.6</b>"})).fetch(["AAA"]))
    assert result.observations == []
    assert result.issues[0].code == "provider_schema_changed"


def test_one_failed_page_does_not_stop_the_batch() -> None:
    client = FakeClient({"GOOD": PAGE, "BAD": RuntimeError("429")})
    provider = ScreenerReturnsProvider(client, request_interval_seconds=0)
    result = asyncio.run(provider.fetch(["GOOD", "BAD"]))
    assert {item.ticker for item in result.observations} == {"GOOD"}
    assert result.issues[0].code == "provider_request_failed"


def test_production_reader_limits_requests_to_cross_source_conflicts() -> None:
    client = FakeClient({"GOOD": PAGE})
    provider = ScreenerReturnsProvider(client, review_reader=FakeReviewReader())
    result = asyncio.run(provider.fetch(["GOOD", "SKIP"]))
    assert {item.ticker for item in result.observations} == {"GOOD"}
