from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.yahoo_roe import YahooRoeProvider, calculate_annual_roe


def payload(*, income=48_461_000_000, current=458_614_900_000, previous=330_464_900_000):
    return {
        "timeseries": {
            "result": [
                {
                    "meta": {"type": ["annualStockholdersEquity"]},
                    "annualStockholdersEquity": [
                        {"asOfDate": "2025-03-31", "reportedValue": {"raw": previous}},
                        {"asOfDate": "2026-03-31", "reportedValue": {"raw": current}},
                    ],
                },
                {
                    "meta": {"type": ["annualNetIncomeCommonStockholders"]},
                    "annualNetIncomeCommonStockholders": [
                        {"asOfDate": "2026-03-31", "reportedValue": {"raw": income}}
                    ],
                },
            ],
            "error": None,
        }
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get_json(self, **kwargs):
        self.calls.append(kwargs)
        symbol = kwargs["params"]["symbol"]
        response = self.responses[symbol]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(request_id=uuid4()), response


def test_tatacap_uses_net_income_over_average_equity() -> None:
    calculated = calculate_annual_roe(payload())
    assert calculated == (12.282915, "2026-03-31")


def test_non_positive_equity_is_not_reported_as_a_return() -> None:
    assert calculate_annual_roe(payload(current=-100)) is None


def test_one_equity_period_is_insufficient() -> None:
    value = payload()
    value["timeseries"]["result"][0]["annualStockholdersEquity"] = value[
        "timeseries"
    ]["result"][0]["annualStockholdersEquity"][-1:]
    assert calculate_annual_roe(value) is None


def test_provider_emits_value_basis_period_and_archivable_request_id() -> None:
    client = FakeClient({"TATACAP.NS": payload()})
    result = asyncio.run(YahooRoeProvider(client, now=lambda: 1_800_000_000).fetch(["TATACAP"]))
    by_field = {item.field: item for item in result.observations}
    assert by_field["roe_pct"].value == 12.282915
    assert by_field["roe_pct"].unit == "percent"
    assert by_field["roe_pct_basis"].value == "annual_net_income_over_average_equity"
    assert by_field["roe_fiscal_period"].value == "2026-03-31"
    assert by_field["roe_pct"].raw_request_id is not None
    assert client.calls[0]["params"]["symbol"] == "TATACAP.NS"


def test_one_failed_stock_does_not_stop_the_batch() -> None:
    client = FakeClient({"GOOD.NS": payload(), "BAD.NS": RuntimeError("429")})
    result = asyncio.run(YahooRoeProvider(client).fetch(["GOOD", "BAD"]))
    assert {item.ticker for item in result.observations} == {"GOOD"}
    assert result.issues[0].code == "provider_request_failed"
    assert result.issues[0].raw_value == "BAD"
