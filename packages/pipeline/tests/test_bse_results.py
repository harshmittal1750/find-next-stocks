import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from find_next_pipeline.providers.bse_results import BseResultsProvider, parse_results

# The literal body BSE returned for TATACAP (scripcode 544574) on 2026-09-06, including
# its double-encoding: the JSON payload is itself a JSON string.
TATACAP = json.dumps(json.dumps({
    "col1": "(in Cr.)", "col2": "Jun-26", "col3": "Mar-26", "col4": "FY25-26",
    "resultinCr": [
        {"title": "Revenue", "v1": "6,334.12", "v2": "6,109.65", "v3": "23,051.50"},
        {"title": "Net Profit", "v1": "999.44", "v2": "1,182.60", "v3": "3,201.14"},
        {"title": "EPS", "v1": "2.35", "v2": "2.79", "v3": "7.77"},
    ],
}))


def test_crore_is_converted_to_rupees() -> None:
    """Every monetary field in the warehouse is rupees; BSE quotes crore.

    upstox_statements stores revenue near 8.9e10 for a large company. Emitting 23051.50
    beside that would not look wrong, it would look like a microcap.
    """
    values, labels = parse_results(json.loads(TATACAP))

    assert values["results_revenue"] == 23051.50 * 1e7
    assert values["results_quarterly_revenue"] == 6334.12 * 1e7
    assert values["results_net_profit"] == 3201.14 * 1e7
    assert labels["latest_quarter"] == "Jun-26"


def test_eps_is_per_share_and_not_scaled() -> None:
    """TATACAP's filed EPS is 7.77 -- the number BSE's own header field got wrong.

    That header reported ROE 2609% for this company; the filed results are the check on
    figures like it.
    """
    values, _ = parse_results(json.loads(TATACAP))
    assert values["results_eps"] == 7.77
    assert values["results_quarterly_eps"] == 2.35


def test_quarterly_growth_divides_two_figures_of_the_same_basis() -> None:
    """999.44 against 1,182.60 is a decline of about 15.5%.

    The response never states consolidated or standalone, so levels stay in their own
    namespace -- but a ratio of two quarters BSE computed the same way is safe to share.
    """
    values, _ = parse_results(json.loads(TATACAP))
    assert values["earnings_quarterly_growth"] == round((999.44 - 1182.60) / 1182.60, 6)
    assert values["earnings_quarterly_growth"] < 0


def test_blank_and_missing_figures_are_dropped_not_zeroed() -> None:
    """SAICAPI files a profit but no revenue: BSE writes '--'.

    Zero revenue is a claim about the business. Absent revenue is a claim about the
    filing, and only the second one is true.
    """
    body = json.dumps({
        "col2": "Jun-26", "col3": "Mar-26", "col4": "FY25-26",
        "resultinCr": [
            {"title": "Revenue", "v1": "--", "v2": "--", "v3": "--"},
            {"title": "Net Profit", "v1": "-0.68", "v2": "-0.40", "v3": "-2.10"},
            {"title": "EPS", "v1": "-2.37", "v2": "--", "v3": "--"},
        ],
    })
    values, _ = parse_results(body)

    assert "results_revenue" not in values
    assert "results_quarterly_revenue" not in values
    assert values["results_net_profit"] == -2.10 * 1e7
    assert "results_eps" not in values          # annual EPS was '--'
    assert values["results_quarterly_eps"] == -2.37


def test_levels_never_collide_with_the_consolidated_upstox_fields() -> None:
    """Basis is unknown here, so these must not pool with explicitly consolidated data.

    bse.py measured BSE's Con* fields as populated 0% of the time across 3,994 responses.
    Writing these into `revenue` would recreate mixed_accounting_basis by another route.
    """
    values, _ = parse_results(json.loads(TATACAP))
    for shared in ("revenue", "net_profit", "quarterly_revenue", "quarterly_net_profit",
                   "trailing_eps", "total_revenue"):
        assert shared not in values, f"{shared} must stay in the results_ namespace"


class FakeClient:
    def __init__(self, body):
        self.body = body
        self.calls = []

    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        self.calls.append(params)
        return SimpleNamespace(request_id=uuid4(), received_at=datetime.now(UTC)), self.body


class Reader:
    def read_instruments(self):
        return [{"ticker": "TATACAP", "bse_code": "544574"}, {"ticker": "NOCODE", "bse_code": None}]


def test_a_stock_without_a_scrip_code_is_reported_not_skipped_silently() -> None:
    provider = BseResultsProvider(
        FakeClient(json.loads(TATACAP)), instrument_reader=Reader()
    )
    result = asyncio.run(provider.fetch(["TATACAP", "NOCODE"]))

    fields = {o.field for o in result.observations if o.ticker == "TATACAP"}
    assert "results_eps" in fields and "results_period" in fields
    assert [i.code for i in result.issues] == ["missing_bse_code"]
    # Only the stock with a code costs a request.
    assert provider.client.calls == [{"scripcode": "544574", "tabtype": "RESULTS"}]
