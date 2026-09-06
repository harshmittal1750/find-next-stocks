"""Discover exchange-listed equities without a market-cap or liquidity cutoff."""

from __future__ import annotations

import csv
import io
from typing import Any

import httpx

from find_next_pipeline.providers.bse import HEADERS, MASTER_ENDPOINT
from find_next_pipeline.providers.http import ArchivedHttpClient

NSE_LISTS = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
)


def equity_isin(value: str) -> bool:
    return len(value) == 12 and value.startswith("INE") and value[7:9] == "01"


def parse_nse(body: str, request_id: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    if not reader.fieldnames or "SYMBOL" not in [x.strip() for x in reader.fieldnames]:
        raise ValueError("NSE equity list schema is invalid")
    result = []
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        isin = row.get("ISIN NUMBER", row.get("ISIN", ""))
        if not equity_isin(isin) or not row.get("SYMBOL"):
            continue
        result.append(
            dict(
                ticker=row["SYMBOL"],
                exchange="NSE",
                isin=isin,
                shortName=row.get("NAME OF COMPANY") or row["SYMBOL"],
                raw_request_id=request_id,
            )
        )
    if not result:
        raise ValueError("NSE equity list is empty; existing universe retained")
    return result


def combine_equities(nse: list[dict], bse: list[dict], request_id: str) -> list[dict]:
    by_isin = {}
    for row in nse:
        existing = by_isin.get(row["isin"])
        if existing and existing["ticker"] != row["ticker"]:
            raise ValueError(f"Ambiguous NSE symbols for ISIN {row['isin']}")
        by_isin[row["isin"]] = dict(row)
    if not isinstance(bse, list) or not bse:
        raise ValueError("BSE active equity list is empty or invalid")
    bse_count = 0
    for row in bse:
        isin = str(row.get("ISIN_NUMBER") or "").strip()
        code = str(row.get("SCRIP_CD") or "").strip()
        if not equity_isin(isin) or not code.isdigit() or row.get("Status") != "Active":
            continue
        bse_count += 1
        stock = by_isin.setdefault(
            isin,
            dict(
                ticker=f"{code}.BO",
                exchange="BSE",
                isin=isin,
                shortName=row.get("Scrip_Name") or row.get("scrip_id") or code,
                raw_request_id=request_id,
            ),
        )
        if stock.get("bse_code") and stock["bse_code"] != code:
            raise ValueError(f"Ambiguous BSE listings for ISIN {isin}")
        stock["bse_code"] = code
    if not bse_count:
        raise ValueError("BSE response contains no active equity listings")
    return sorted(by_isin.values(), key=lambda row: row["ticker"])


def parse_upstox_equities(master: Any, request_id: str) -> list[dict]:
    if not isinstance(master, list):
        raise ValueError("Upstox exchange master schema is invalid")
    result = []
    for row in master:
        isin = str(row.get("isin") or "")
        if (
            row.get("segment") != "NSE_EQ"
            or not equity_isin(isin)
            or row.get("instrument_type") not in {"EQ", "BE", "BZ", "SM", "ST", "SZ"}
        ):
            continue
        symbol = str(row.get("trading_symbol") or "").strip()
        if not symbol:
            continue
        result.append(
            dict(
                ticker=symbol,
                exchange="NSE",
                isin=isin,
                shortName=row.get("name") or symbol,
                raw_request_id=request_id,
                universe_note="NSE lists unavailable; used Upstox NSE exchange master",
            )
        )
    if not result:
        raise ValueError("No equities in Upstox exchange master")
    return result


async def discover_equities(client: ArchivedHttpClient) -> list[dict]:
    nse = []
    try:
        for endpoint in NSE_LISTS:
            envelope, body = await client.get_text(
                provider="universe",
                endpoint=endpoint,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Referer": "https://www.nseindia.com/",
                },
            )
            nse.extend(parse_nse(body, str(envelope.request_id)))
    except (httpx.HTTPError, ValueError):
        # The broker's current exchange master is a documented alternative; record
        # its use explicitly and discard any partial NSE response before replacing it.
        envelope, master = await client.get_json(
            provider="universe",
            endpoint="https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
        )
        nse = parse_upstox_equities(master, str(envelope.request_id))
    envelope, bse = await client.get_json(
        provider="universe",
        endpoint=MASTER_ENDPOINT,
        params={
            "Group": "",
            "Scripcode": "",
            "industry": "",
            "segment": "Equity",
            "status": "Active",
        },
        headers=HEADERS,
    )
    return combine_equities(nse, bse, str(envelope.request_id))
