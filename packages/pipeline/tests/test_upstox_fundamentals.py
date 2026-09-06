from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.upstox_fundamentals import (
    UpstoxFundamentalsProvider,
    parse_key_ratios,
)

MASTER = [
    {
        "segment": "NSE_EQ",
        "trading_symbol": "KIRIINDUS",
        "instrument_key": "NSE_EQ|INE415I01015",
        "instrument_type": "EQ",
        "isin": "INE415I01015",
    },
    {
        "segment": "NSE_INDEX",
        "trading_symbol": "Nifty 50",
        "instrument_key": "NSE_INDEX|Nifty 50",
    },
]
RATIOS = {
    "status": "success",
    "data": [
        {"name": "P/E", "company_value": "0.62", "sector_value": "-127.22"},
        {"name": "P/B", "company_value": "0.55", "sector_value": "2.3"},
        {"name": "ROA", "company_value": "80.99%", "sector_value": "45.51%"},
        {"name": "ROE", "company_value": "-1.55%", "sector_value": "-657.66%"},
        {"name": "ROCE", "company_value": "-1.42%", "sector_value": "-2.91%"},
        {"name": "EV/EBITDA", "company_value": "0.43", "sector_value": "19.14"},
    ],
}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        self.calls.append((provider, endpoint))
        envelope = SimpleNamespace(request_id=uuid4())
        return envelope, MASTER if endpoint.endswith("NSE.json.gz") else RATIOS


class EmptyRatiosClient(FakeClient):
    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        self.calls.append((provider, endpoint))
        envelope = SimpleNamespace(request_id=uuid4())
        payload = MASTER if endpoint.endswith("NSE.json.gz") else {
            "status": "success",
            "data": [],
        }
        return envelope, payload


def _provider(client: FakeClient) -> UpstoxFundamentalsProvider:
    return UpstoxFundamentalsProvider(
        client,
        "test-token",
        request_interval_seconds=0,
    )


def test_key_ratios_are_parsed_with_canonical_units() -> None:
    assert parse_key_ratios(RATIOS) == {
        "trailing_pe": (0.62, "ratio"),
        "price_to_book": (0.55, "ratio"),
        "return_on_assets_pct": (80.99, "percent"),
        "roe_pct": (-1.55, "percent"),
        "roce_pct": (-1.42, "percent"),
        "enterprise_to_ebitda": (0.43, "ratio"),
    }


def test_provider_resolves_isin_and_preserves_raw_request_link() -> None:
    client = FakeClient()
    result = asyncio.run(_provider(client).fetch(["KIRIINDUS"]))

    by_field = {observation.field: observation for observation in result.observations}
    assert by_field["roe_pct"].value == -1.55
    assert by_field["roe_pct"].unit == "percent"
    assert by_field["roce_pct"].value == -1.42
    assert by_field["price_to_book"].value == 0.55
    assert by_field["roe_pct"].raw_request_id is not None
    assert by_field["roe_pct"].endpoint.endswith("/INE415I01015/key-ratios")
    assert {provider for provider, _ in client.calls} == {"upstox_fundamentals"}
    assert not result.issues


def test_missing_symbol_is_reported_instead_of_silently_dropped() -> None:
    result = asyncio.run(_provider(FakeClient()).fetch(["NOTLISTED"]))

    assert not result.observations
    assert [issue.code for issue in result.issues] == ["symbol_not_listed_on_upstox"]
    assert result.issues[0].raw_value == "NOTLISTED"


def test_empty_company_coverage_is_a_warning_not_a_schema_error() -> None:
    result = asyncio.run(_provider(EmptyRatiosClient()).fetch(["KIRIINDUS"]))

    assert not result.observations
    assert [issue.code for issue in result.issues] == ["provider_empty_payload"]
    assert result.issues[0].severity == "warning"


def test_provider_can_be_reused_across_refresh_event_loops() -> None:
    client = FakeClient()
    provider = _provider(client)

    first = asyncio.run(provider.fetch(["KIRIINDUS"]))
    second = asyncio.run(provider.fetch(["KIRIINDUS"]))

    assert first.observations and second.observations
    # The large instrument master is cached; each batch still archives its ratio body.
    assert sum(endpoint.endswith("NSE.json.gz") for _, endpoint in client.calls) == 1
