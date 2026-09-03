import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from find_next_pipeline.models import RawEnvelope
from find_next_pipeline.providers.nse import NseValuationProvider
from find_next_pipeline.providers.upstox import UpstoxQuoteProvider
from find_next_pipeline.providers.yahoo import YahooChartProvider


def test_nse_daily_pe_parser_selects_requested_symbols() -> None:
    body = "SYMBOL,ADJUSTED P/E,SYMBOL P/E\nGALLANTT,32.4,33.1\nOTHER,10,11\n"

    observations = NseValuationProvider._parse(
        body,
        {"GALLANTT"},
        "https://nsearchives.nseindia.com/example.csv",
        uuid4(),
        date(2026, 7, 23),
    )

    assert len(observations) == 1
    assert observations[0].ticker == "GALLANTT"
    assert observations[0].field == "trailing_pe"
    assert observations[0].value == 32.4


def test_upstox_instrument_and_quote_mapping() -> None:
    instruments = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE297H01019",
            "trading_symbol": "GALLANTT",
        },
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "instrument_key": "NSE_FO|123",
            "trading_symbol": "GALLANTT",
        },
    ]

    mapped = UpstoxQuoteProvider._instrument_map(instruments, {"GALLANTT"})

    assert mapped == {"NSE_EQ|INE297H01019": "GALLANTT"}
    assert (
        UpstoxQuoteProvider._ticker_for_quote(
            "NSE_EQ:GALLANTT",
            {"instrument_token": "NSE_EQ|INE297H01019", "symbol": "GALLANTT"},
            mapped,
        )
        == "GALLANTT"
    )


def test_yahoo_extracts_the_daily_series_it_already_fetches() -> None:
    """The chart response was being fetched in full and thrown away but for 4 fields.

    Same payload shape as Yahoo's v8 chart endpoint: one timestamp array, parallel
    OHLCV arrays under indicators.quote[0], and an adjclose series alongside it.
    """
    chart_result = {
        "timestamp": [1755561000, 1755647400, 1755733800],
        "indicators": {
            "quote": [
                {
                    "open": [100.0, 101.5, None],
                    "high": [102.0, 103.0, 104.0],
                    "low": [99.0, 100.5, 101.0],
                    "close": [101.0, 102.5, None],
                    "volume": [10_000, 12_000, 0],
                }
            ],
            "adjclose": [{"adjclose": [100.8, 102.3, None]}],
        },
    }

    bars = YahooChartProvider._parse_price_bars(chart_result, "GALLANTT", uuid4())

    # The third day has no close (a gap/non-trading day padding) and is dropped.
    assert len(bars) == 2
    assert bars[0].ticker == "GALLANTT"
    assert bars[0].close == 101.0
    assert bars[0].adjusted_close == 100.8
    assert bars[1].close == 102.5
    assert bars[1].open == 101.5


class _FakeYahooClient:
    """Returns a canned successful chart payload — no network."""

    async def get_json(self, *, provider, endpoint, params=None, headers=None):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {"regularMarketPrice": 100.0, "regularMarketTime": 1755561000},
                        "timestamp": [1755561000],
                        "indicators": {"quote": [{"close": [100.0]}]},
                    }
                ]
            }
        }
        envelope = RawEnvelope(
            provider=provider,
            endpoint=endpoint,
            requested_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            status_code=200,
            payload=payload,
            content_sha256="0" * 64,
        )
        return envelope, payload


def test_yahoo_fetch_survives_one_asyncio_run_per_batch() -> None:
    """Regression: refresh_jobs.py calls asyncio.run(provider.fetch(batch)) per batch,
    each spinning up its own event loop, on the same provider instance. A semaphore built
    once in __init__ binds to the first loop and raises "bound to a different event loop"
    on every batch after it — silently failing 1,113 of 1,353 tickers on the first run.
    """
    provider = YahooChartProvider(_FakeYahooClient())

    first = asyncio.run(provider.fetch(["AAA"]))
    second = asyncio.run(provider.fetch(["BBB"]))

    assert not first.issues
    assert not second.issues
    assert len(first.price_bars) == 1
    assert len(second.price_bars) == 1
