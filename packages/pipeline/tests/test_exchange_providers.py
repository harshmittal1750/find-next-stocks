from datetime import date
from uuid import uuid4

from find_next_pipeline.providers.nse import NseValuationProvider
from find_next_pipeline.providers.upstox import UpstoxQuoteProvider


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
