from uuid import uuid4

import pytest
from find_next_pipeline.universe import combine_equities, parse_nse, parse_upstox_equities


def test_small_and_sme_stocks_survive_without_market_cap():
    rows = parse_nse(
        "SYMBOL,NAME OF COMPANY,ISIN NUMBER\nTINY,Tiny Limited,INE002A01018\n", str(uuid4())
    )
    assert rows[0]["ticker"] == "TINY"
    assert "market_cap" not in rows[0]


def test_dual_listings_are_matched_by_isin_not_similar_names():
    nse = [dict(ticker="AAA", exchange="NSE", isin="INE002A01018", shortName="AAA")]
    bse = [
        dict(SCRIP_CD="500001", ISIN_NUMBER="INE002A01018", Status="Active", scrip_id="DIFFERENT"),
        dict(
            SCRIP_CD="500002",
            ISIN_NUMBER="INE003A01018",
            Status="Active",
            scrip_id="AAA",
            Mktcap="0.01",
        ),
        dict(SCRIP_CD="500003", ISIN_NUMBER="INE004A01018", Status="Suspended"),
    ]
    rows = combine_equities(nse, bse, str(uuid4()))
    assert len(rows) == 2
    assert next(r for r in rows if r["ticker"] == "AAA")["bse_code"] == "500001"
    assert next(r for r in rows if r["ticker"] == "500002.BO")["exchange"] == "BSE"


def test_broker_fallback_excludes_etfs_debt_and_indices_but_keeps_sme():
    master = [
        dict(segment="NSE_EQ", instrument_type="SM", isin="INE002A01018", trading_symbol="SMALL"),
        dict(segment="NSE_EQ", instrument_type="EQ", isin="INF002A01018", trading_symbol="FUND"),
        dict(segment="NSE_EQ", instrument_type="N0", isin="INE002A08018", trading_symbol="DEBT"),
        dict(
            segment="NSE_INDEX",
            instrument_type="INDEX",
            isin="INE002A01018",
            trading_symbol="INDEX",
        ),
    ]
    assert [r["ticker"] for r in parse_upstox_equities(master, str(uuid4()))] == ["SMALL"]


def test_bad_master_cannot_silently_empty_universe():
    with pytest.raises(ValueError):
        parse_nse("<html>blocked</html>", str(uuid4()))
    with pytest.raises(ValueError):
        combine_equities([], [], str(uuid4()))
