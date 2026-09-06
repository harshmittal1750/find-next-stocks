import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from find_next_pipeline.providers.upstox_statements import parse_fundamentals

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def parse(data, endpoint="balance-sheet", quarterly=False):
    return parse_fundamentals(
        {"status": "success", "data": data},
        ticker="TEST",
        endpoint="https://api.upstox.com/v2/fundamentals/INE002A01018/" + endpoint,
        request_id=uuid4(),
        observed_at=NOW,
        quarterly=quarterly,
    )


def balance(period="Mar 2026"):
    return {
        "type": "consolidated",
        "time_period": "yearly",
        "units_in": "crore",
        "full_statement": [
            {"particular": "Current Assets", "history": [{"period": period, "value": 200}]},
            {"particular": "Current Liabilities", "history": [{"period": period, "value": 100}]},
        ],
    }


def test_units_period_basis_and_provenance_are_retained():
    observations = {o.field: o for o in parse(balance())}
    assert observations["current_assets"].value == 2_000_000_000
    ratio = observations["current_ratio"]
    assert ratio.value == 2
    assert str(ratio.period_end) == "2026-03-31"
    assert ratio.accounting_basis == "consolidated"
    assert ratio.raw_request_id and ratio.is_valid


def test_old_statement_is_archived_but_not_valid():
    observations = parse(balance("Mar 2018"))
    assert all(not o.is_valid for o in observations)
    assert observations[0].value == 2_000_000_000  # never replace with zero/clamp
    assert observations[0].issues[0].code == "stale_reporting_period"


def test_wrong_currency_and_unexpected_standalone_do_not_enter_canonical_data():
    data = balance()
    data["units_in"] = "USD"
    with pytest.raises(ValueError):
        parse(data)
    data = balance()
    data["type"] = "standalone"
    assert all(not o.is_valid for o in parse(data))


def test_growth_compares_same_quarter_one_year_apart():
    data = {
        "type": "consolidated",
        "time_period": "quarterly",
        "units_in": "crore",
        "income_statement": [
            {
                "category": "net_profit",
                "history": [
                    {"period": "Jun 2026", "value": 120},
                    {"period": "Mar 2026", "value": 50},
                    {"period": "Jun 2025", "value": 100},
                ],
            }
        ],
        "full_statement": balance()["full_statement"],
    }
    obs = parse(data, "income-statement", True)
    fields = {o.field: o for o in obs}
    assert set(fields) == {"quarterly_net_profit", "earnings_quarterly_growth"}
    assert fields["earnings_quarterly_growth"].value == pytest.approx(0.2)
    assert fields["quarterly_net_profit"].value == 120 * 1e7


def holdings():
    return [
        {"category": k, "history": [{"period": "Jun 2026", "value": v}]}
        for k, v in [
            ("promoters", 50),
            ("fii", 15),
            ("other_dii", 10),
            ("mutual_funds", 5),
            ("retail_and_other", 20),
        ]
    ]


def test_ownership_uses_real_categories_without_counting_funds_twice():
    obs = {o.field: o for o in parse(holdings(), "share-holdings")}
    assert obs["promoter_pct"].value == 50
    assert obs["institutional_pct"].value == 30
    assert "institutions_count" not in obs


def test_bad_ownership_and_mixed_periods_are_rejected():
    rows = holdings()
    rows[0]["history"][0]["value"] = 120
    assert all(not o.is_valid for o in parse(rows, "share-holdings"))
    rows = holdings()
    rows[0]["history"][0]["period"] = "Mar 2026"
    with pytest.raises(ValueError):
        parse(rows, "share-holdings")


def test_missing_latest_period_does_not_resurrect_old_value():
    data = balance()
    data['full_statement'][0]['history'].append({'period': 'Jun 2026', 'value': None})
    fields = {o.field for o in parse(data)}
    assert 'current_assets' not in fields
    assert 'current_ratio' not in fields


def test_duplicate_categories_cannot_silently_replace_each_other():
    rows = holdings()
    rows.append(rows[0])
    with pytest.raises(ValueError, match='Duplicate'):
        parse(rows, 'share-holdings')


def test_four_quarters_preserve_amounts_without_inventing_year_on_year_growth():
    data = {
        'type': 'consolidated', 'time_period': 'quarterly', 'units_in': 'crore',
        'income_statement': [{
            'category': 'net_profit',
            'history': [
                {'period': p, 'value': 100}
                for p in ['Jun 2026', 'Mar 2026', 'Dec 2025', 'Sep 2025']
            ],
        }],
    }
    obs = parse(data, 'income-statement', True)
    assert [o.field for o in obs] == ['quarterly_net_profit']
    assert obs[0].is_valid


def test_every_upstox_provider_draws_from_one_budget() -> None:
    """Upstox meters per account, so a private window per caller multiplies the rate.

    Before this was shared, upstox_statements kept a window per endpoint and each Upstox
    provider built its own: a full pass produced 30 HTTP 429s and the statements stage
    returned data for 37 of 5,428 stocks.
    """
    from find_next_pipeline.providers import rate_limit
    from find_next_pipeline.providers.upstox_fundamentals import UpstoxFundamentalsProvider
    from find_next_pipeline.providers.upstox_statements import (
        UpstoxShareholdingProvider,
        UpstoxStatementsProvider,
    )

    statements = UpstoxStatementsProvider(None, "token", None)
    shareholding = UpstoxShareholdingProvider(None, "token", None)
    fundamentals = UpstoxFundamentalsProvider(None, "token")

    assert statements._requests is rate_limit.ACCOUNT_WINDOW
    assert shareholding._requests is rate_limit.ACCOUNT_WINDOW
    assert fundamentals._request_windows is rate_limit.ACCOUNT_WINDOW
    # And not a defaultdict keyed by endpoint: one budget, whatever is being fetched.
    assert not hasattr(statements._requests, "default_factory")


def test_the_shared_window_actually_throttles() -> None:
    """A budget nobody enforces is decoration.

    Upstox's per-second cap is the one that actually bites: every 429 this project has
    seen fired at 56-58 requests inside one second, while the per-minute and 30-minute
    counts were nowhere near their limits.
    """
    from find_next_pipeline.providers.rate_limit import LIMITS, RequestWindows

    per_second = LIMITS[0][0]
    window = RequestWindows()
    for _ in range(per_second):
        window.requests.append(0.0)          # a full second's worth at t=0
    assert window.delay(0.5) > 0, "one more inside the same second must wait"
    assert window.delay(1.5) == 0, "once the second has passed it must not"


def test_a_restarted_run_resumes_instead_of_starting_over() -> None:
    """Statements are quarterly facts, so refetching them daily buys nothing.

    A full pass costs 21,712 requests against an account capped at 1,900 per 30 minutes.
    Without this a run killed at stock 3,000 restarts at zero -- which is how four hours
    of fetching produced 307 stocks.
    """
    from find_next_pipeline.providers.upstox_statements import UpstoxStatementsProvider

    requested: list[str] = []

    class Warehouse:
        def read_instruments(self):
            return [{"ticker": t, "isin": f"INE{t}0101"} for t in ("AAA", "BBB", "CCC")]

        def fresh_tickers(self, provider, max_age_days):
            requested.append(provider)
            return {"AAA", "BBB"}          # already fetched inside the window

    class NoClient:
        async def get_json(self, **kwargs):
            raise AssertionError("a fresh ticker must not be fetched again")

    provider = UpstoxStatementsProvider(NoClient(), "token", Warehouse(), rate_limits=False)
    provider.endpoints = ()                # no endpoints: prove the filter, not the HTTP
    result = asyncio.run(provider.fetch(["AAA", "BBB", "CCC"]))

    assert requested == ["upstox_statements"]
    assert result.observations == []


def test_max_age_zero_forces_a_full_refetch() -> None:
    """The escape hatch: a corrected parser needs to re-read every stock."""
    from find_next_pipeline.providers.upstox_statements import UpstoxStatementsProvider

    provider = UpstoxStatementsProvider(None, "t", None, max_age_days=0)
    assert provider.max_age_days == 0
    default = UpstoxStatementsProvider(None, "t", None)
    assert default.max_age_days == 30
