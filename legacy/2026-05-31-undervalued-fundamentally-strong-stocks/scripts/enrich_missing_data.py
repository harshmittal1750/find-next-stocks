#!/usr/bin/env python3
"""Targeted multi-provider recovery for sparse stock fundamentals.

Runs after the normal price/ownership/shareholding stages and before ranking.
Only blank values are filled; an existing value is never overwritten. Free
provider order is NSE daily valuation data, BSE company headers, then targeted
Yahoo summary/statement retries. Every accepted value is written to a provenance
report and provider responses are cached locally.

Reads : data/raw_fundamentals.csv, data/shareholding_layer.csv
Writes: data/raw_fundamentals.csv, data/screen_results.csv
        data/enrichment_report.csv, data/enrichment_summary.json
Cache : data/cache/enrichment/

Examples:
  ./.venv/bin/python scripts/enrich_missing_data.py
  ./.venv/bin/python scripts/enrich_missing_data.py --symbols ELPROINTL,JSWDULUX
  ./.venv/bin/python scripts/enrich_missing_data.py --offline
"""
import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from nselib import capital_market

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rank_all as ranking  # noqa: E402
import screen_universe as screening  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CACHE = DATA / "cache" / "enrichment"
RAW = DATA / "raw_fundamentals.csv"
SHARE = DATA / "shareholding_layer.csv"
UNIVERSE = DATA / "universe_filtered.csv"
REPORT = DATA / "enrichment_report.csv"
SUMMARY = DATA / "enrichment_summary.json"

BSE_MASTER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
BSE_HEADER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w"
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.bseindia.com/",
}
SHARE_KEEP = ["ticker", "promoter_pct", "institutional_pct", "institutions_count",
              "avg_delivery_pct", "delivery_trend"]
INFO_FIELDS = [
    "shortName", "sector", "industry", "trailingPE", "forwardPE", "priceToBook", "pegRatio",
    "returnOnEquity", "returnOnAssets", "debtToEquity", "earningsGrowth",
    "earningsQuarterlyGrowth", "revenueGrowth", "profitMargins", "operatingMargins",
    "grossMargins", "ebitdaMargins", "dividendYield", "marketCap", "currentRatio",
    "quickRatio", "freeCashflow", "totalCashPerShare", "bookValue", "targetMeanPrice",
    "targetHighPrice", "targetLowPrice", "recommendationMean", "numberOfAnalystOpinions",
    "enterpriseToEbitda", "enterpriseValue", "trailingEps", "forwardEps",
    "heldPercentInsiders", "heldPercentInstitutions",
]
REPORT_COLUMNS = ["ticker", "field", "old_value", "new_value", "provider", "source", "fetched_at"]


def log(message):
    print(f"[{dt.datetime.now():%H:%M:%S}] {message}", flush=True)


def atomic_csv(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)


def atomic_json(payload, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def is_missing(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if value in {"", "-", "--", "NA", "N/A", "null", "None"}:
            return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def valid_value(field, value):
    if field in {"shortName", "sector", "industry"}:
        return isinstance(value, str) and bool(value.strip())
    value = number(value)
    if value is None:
        return False
    bounds = {
        "trailingPE": (0, 5000), "priceToBook": (0, 1000), "marketCap": (0, 1e16),
        "returnOnEquity": (-5, 5), "returnOnAssets": (-5, 5),
        "debtToEquity": (0, 10000), "currentRatio": (0, 1000),
        "profitMargins": (-10, 10), "ebitdaMargins": (-10, 10),
        "earningsGrowth": (-100, 100), "revenueGrowth": (-100, 100),
        "earningsQuarterlyGrowth": (-100, 100),
        "heldPercentInsiders": (0, 1), "heldPercentInstitutions": (0, 1),
    }
    low, high = bounds.get(field, (-1e18, 1e18))
    return low <= value <= high


def apply_value(raw, ticker, field, value, provider, source, changes):
    if field not in raw.columns:
        raw[field] = pd.NA
    old = raw.at[ticker, field]
    if not is_missing(old) or not valid_value(field, value):
        return False
    clean = value.strip() if isinstance(value, str) else number(value)
    raw.at[ticker, field] = clean
    changes.append({
        "ticker": ticker, "field": field, "old_value": None, "new_value": clean,
        "provider": provider, "source": source,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    })
    return True


def scoring_coverage(raw, share):
    keep = [column for column in SHARE_KEEP if column in share.columns]
    merged = raw.reset_index().merge(share[keep], on="ticker", how="left")
    derived = ranking.derive(merged.copy())
    total, groups = ranking.coverage_by_group(derived)
    return pd.DataFrame({
        "ticker": derived["ticker"].astype(str), "data_cov": total,
        "quality_cov": groups["quality"], "valuation_cov": groups["valuation"],
    }).set_index("ticker")


def candidate_dates():
    dates = []
    delivery = DATA / "cache" / "nse_delivery"
    if delivery.exists():
        for path in sorted(delivery.glob("*.csv"), reverse=True):
            try:
                dates.append(dt.date.fromisoformat(path.stem))
            except ValueError:
                pass
    day = dt.date.today()
    for offset in range(15):
        candidate = day - dt.timedelta(days=offset)
        if candidate.weekday() < 5:
            dates.append(candidate)
    return list(dict.fromkeys(dates))


def load_nse_pe(offline=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = sorted(CACHE.glob("nse_pe_*.csv"), reverse=True)
    if cached:
        try:
            return pd.read_csv(cached[0]), cached[0].stem.removeprefix("nse_pe_")
        except Exception as exc:
            log(f"NSE cached P/E ignored: {exc}")
    if offline:
        return None, None
    for trade_date in candidate_dates():
        try:
            frame = capital_market.pe_ratio(trade_date.strftime("%d-%m-%Y"))
            if len(frame):
                atomic_csv(frame, CACHE / f"nse_pe_{trade_date.isoformat()}.csv")
                return frame, trade_date.isoformat()
        except Exception:
            continue
    return None, None


def enrich_nse(raw, targets, changes, offline=False):
    frame, asof = load_nse_pe(offline=offline)
    if frame is None or frame.empty:
        log("NSE: no usable P/E report; skipped")
        return 0
    columns = {str(column).strip().upper(): column for column in frame.columns}
    symbol_col = columns.get("SYMBOL")
    adjusted_col = columns.get("ADJUSTEDP/E")
    pe_col = columns.get("SYMBOLP/E")
    if symbol_col is None or (adjusted_col is None and pe_col is None):
        log("NSE: unexpected P/E schema; skipped")
        return 0
    frame = frame.copy()
    frame[symbol_col] = frame[symbol_col].astype(str).str.strip().str.upper()
    rows = frame.drop_duplicates(symbol_col, keep="last").set_index(symbol_col)
    source = f"https://nsearchives.nseindia.com/content/equities/peDetail/PE_{dt.date.fromisoformat(asof):%d%m%y}.csv"
    accepted = 0
    for ticker in targets:
        if ticker not in rows.index:
            continue
        row = rows.loc[ticker]
        value = number(row.get(adjusted_col)) if adjusted_col is not None else None
        if value is None or value <= 0:
            value = number(row.get(pe_col)) if pe_col is not None else None
        accepted += apply_value(raw, ticker, "trailingPE", value, "NSE", source, changes)
    log(f"NSE: {accepted} missing P/E values recovered from {asof}")
    return accepted


def cache_is_fresh(path, max_age_days):
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400


def load_bse_master(offline=False, max_age_days=7):
    path = CACHE / "bse_security_master.json"
    if path.exists() and (offline or cache_is_fresh(path, max_age_days)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if offline:
        return []
    response = requests.get(
        BSE_MASTER_URL,
        params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"},
        headers=BSE_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("BSE security master returned an unexpected schema")
    atomic_json(payload, path)
    return payload


def normal_symbol(value):
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def map_bse_targets(master, targets):
    by_exact = {}
    by_normal = {}
    for row in master:
        symbol = str(row.get("scrip_id") or "").strip().upper()
        if not symbol:
            continue
        by_exact.setdefault(symbol, []).append(row)
        by_normal.setdefault(normal_symbol(symbol), []).append(row)

    def choose(rows):
        return max(rows, key=lambda row: number(row.get("Mktcap")) or -1) if rows else None

    mapped = {}
    for ticker in targets:
        row = choose(by_exact.get(ticker)) or choose(by_normal.get(normal_symbol(ticker)))
        if row and row.get("SCRIP_CD"):
            mapped[ticker] = row
    return mapped


def fetch_bse_header(ticker, master_row, offline=False, max_age_days=7):
    path = CACHE / "bse_headers" / f"{ticker}.json"
    if path.exists() and (offline or cache_is_fresh(path, max_age_days)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if offline:
        return None
    response = requests.get(
        BSE_HEADER_URL,
        params={"quotetype": "EQ", "scripcode": master_row["SCRIP_CD"], "seriesid": ""},
        headers=BSE_HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("SecurityCode"):
        return None
    atomic_json(payload, path)
    return payload


def preferred(payload, primary, fallback, *, positive=False, nonzero=False):
    for key in (primary, fallback):
        value = number(payload.get(key))
        if value is None or (positive and value <= 0) or (nonzero and value == 0):
            continue
        return value
    return None


def enrich_bse(raw, targets, changes, workers=4, offline=False):
    try:
        master = load_bse_master(offline=offline)
    except Exception as exc:
        log(f"BSE: security master failed ({exc}); skipped")
        return 0
    mapped = map_bse_targets(master, targets)
    if not mapped:
        log("BSE: no target symbols mapped")
        return 0
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_bse_header, ticker, row, offline): ticker
                   for ticker, row in mapped.items()}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                payload = future.result()
                if payload:
                    results[ticker] = payload
            except Exception as exc:
                log(f"  BSE {ticker}: {str(exc)[:100]}")

    accepted = 0
    for ticker, payload in results.items():
        master_row = mapped[ticker]
        code = master_row["SCRIP_CD"]
        source = f"{BSE_HEADER_URL}?quotetype=EQ&scripcode={code}&seriesid="
        values = {
            "trailingPE": preferred(payload, "ConPE", "PE", positive=True),
            "priceToBook": preferred(payload, "ConPB", "PB", positive=True),
            "returnOnEquity": ((preferred(payload, "ConROE", "ROE", nonzero=True) or 0) / 100
                               if preferred(payload, "ConROE", "ROE", nonzero=True) is not None else None),
            "profitMargins": ((preferred(payload, "ConNPM", "NPM", nonzero=True) or 0) / 100
                              if preferred(payload, "ConNPM", "NPM", nonzero=True) is not None else None),
            "trailingEps": preferred(payload, "ConEPS", "EPS", nonzero=True),
            "sector": payload.get("Sector"),
            "industry": payload.get("ISubGroup") or payload.get("Industry"),
            "marketCap": ((number(master_row.get("Mktcap")) or 0) * 1e7
                          if number(master_row.get("Mktcap")) is not None else None),
        }
        for field, value in values.items():
            accepted += apply_value(raw, ticker, field, value, "BSE", source, changes)
    log(f"BSE: mapped {len(mapped)}/{len(targets)} targets; {accepted} blank fields recovered")
    return accepted


def statement_series(frame, names):
    if frame is None or frame.empty:
        return []
    normalized = {normal_symbol(index): index for index in frame.index}
    row_name = next((normalized.get(normal_symbol(name)) for name in names
                     if normalized.get(normal_symbol(name)) is not None), None)
    if row_name is None:
        return []
    pairs = []
    for column, value in frame.loc[row_name].items():
        numeric = number(value)
        if numeric is None:
            continue
        try:
            stamp = pd.Timestamp(column)
        except Exception:
            stamp = pd.Timestamp.min
        pairs.append((stamp, numeric))
    return [value for _stamp, value in sorted(pairs, key=lambda item: item[0], reverse=True)]


def safe_ratio(numerator, denominator, multiplier=1):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * multiplier


def safe_growth(latest, previous):
    # Percentage growth across a zero/loss base is not economically comparable;
    # keep it missing instead of manufacturing an extreme turnaround number.
    if latest is None or previous is None or previous <= 0:
        return None
    return latest / previous - 1


def parse_holder_percent(frame, label):
    if frame is None or frame.empty:
        return None
    for _index, row in frame.iterrows():
        text = " ".join(str(value) for value in row.tolist())
        if label.lower() in text.lower():
            match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
            if match:
                return float(match.group(1)) / 100
    return None


def fetch_yahoo_supplement(ticker, offline=False, max_age_days=30):
    path = CACHE / "yahoo_supplement" / f"{ticker}.json"
    if path.exists() and (offline or cache_is_fresh(path, max_age_days)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if offline:
        return None
    stock = yf.Ticker(f"{ticker}.NS")
    values = {}
    try:
        info = stock.get_info() or {}
        values.update({field: info.get(field) for field in INFO_FIELDS if info.get(field) is not None})
    except Exception:
        pass
    try:
        annual_income = stock.income_stmt
    except Exception:
        annual_income = pd.DataFrame()
    try:
        quarterly_income = stock.quarterly_income_stmt
    except Exception:
        quarterly_income = pd.DataFrame()
    try:
        balance = stock.balance_sheet
    except Exception:
        balance = pd.DataFrame()

    revenue = statement_series(annual_income, ["Total Revenue", "Operating Revenue"])
    net_income = statement_series(annual_income, ["Net Income", "Net Income Common Stockholders"])
    ebitda = statement_series(annual_income, ["EBITDA", "Normalized EBITDA"])
    equity = statement_series(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    assets = statement_series(balance, ["Total Assets"])
    debt = statement_series(balance, ["Total Debt"])
    current_assets = statement_series(balance, ["Current Assets", "Total Current Assets"])
    current_liabilities = statement_series(balance, ["Current Liabilities", "Total Current Liabilities"])
    quarterly_net = statement_series(quarterly_income, ["Net Income", "Net Income Common Stockholders"])

    derived = {
        "returnOnEquity": safe_ratio(net_income[0] if net_income else None, equity[0] if equity else None),
        "returnOnAssets": safe_ratio(net_income[0] if net_income else None, assets[0] if assets else None),
        "ebitdaMargins": safe_ratio(ebitda[0] if ebitda else None, revenue[0] if revenue else None),
        "debtToEquity": safe_ratio(debt[0] if debt else None, equity[0] if equity else None, 100),
        "currentRatio": safe_ratio(current_assets[0] if current_assets else None,
                                   current_liabilities[0] if current_liabilities else None),
        "earningsGrowth": safe_growth(net_income[0], net_income[1]) if len(net_income) >= 2 else None,
        "revenueGrowth": safe_growth(revenue[0], revenue[1]) if len(revenue) >= 2 else None,
        "earningsQuarterlyGrowth": safe_growth(quarterly_net[0], quarterly_net[4])
                                   if len(quarterly_net) >= 5 else None,
    }
    for field, value in derived.items():
        if values.get(field) is None and value is not None:
            values[field] = value
    if values.get("heldPercentInsiders") is None or values.get("heldPercentInstitutions") is None:
        try:
            holders = stock.major_holders
            values.setdefault("heldPercentInsiders", parse_holder_percent(holders, "insider"))
            values.setdefault("heldPercentInstitutions", parse_holder_percent(holders, "institution"))
        except Exception:
            pass
    values = {field: value for field, value in values.items() if value is not None and not is_missing(value)}
    payload = {
        "provider": "Yahoo Finance", "ticker": ticker,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "values": values,
    }
    atomic_json(payload, path)
    return payload


def enrich_yahoo(raw, targets, changes, workers=4, offline=False):
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_yahoo_supplement, ticker, offline): ticker for ticker in targets}
        for index, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                payload = future.result()
                if payload:
                    results[ticker] = payload
            except Exception as exc:
                log(f"  Yahoo {ticker}: {str(exc)[:100]}")
            if index % 25 == 0 or index == len(futures):
                log(f"Yahoo supplemental progress: {index}/{len(futures)}")

    accepted = 0
    for ticker, payload in results.items():
        source = f"https://finance.yahoo.com/quote/{ticker}.NS/"
        for field, value in payload.get("values", {}).items():
            accepted += apply_value(raw, ticker, field, value, "Yahoo supplemental", source, changes)
    log(f"Yahoo supplemental: {accepted} blank fields recovered")
    return accepted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-coverage", type=float, default=75,
                        help="enrich stocks below this weighted coverage or a core-group minimum")
    parser.add_argument("--max-symbols", type=int, default=200,
                        help="cap targeted stocks, lowest coverage first")
    parser.add_argument("--symbols", help="comma-separated explicit symbols instead of coverage targeting")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--offline", action="store_true", help="use provider caches only")
    parser.add_argument("--skip-nse", action="store_true")
    parser.add_argument("--skip-bse", action="store_true")
    parser.add_argument("--skip-yahoo", action="store_true")
    args = parser.parse_args()

    if not RAW.exists() or not SHARE.exists():
        raise SystemExit("Run the normal screen, ownership and shareholding stages first")
    CACHE.mkdir(parents=True, exist_ok=True)
    raw_frame = pd.read_csv(RAW)
    share = pd.read_csv(SHARE)
    raw_frame["ticker"] = raw_frame["ticker"].astype(str).str.strip().str.upper()
    raw = raw_frame.drop_duplicates("ticker", keep="last").set_index("ticker")
    before = scoring_coverage(raw, share)

    if args.symbols:
        requested = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        targets = [symbol for symbol in requested if symbol in raw.index]
    else:
        target_mask = ((before["data_cov"] < args.target_coverage) |
                       (before["quality_cov"] < ranking.MIN_CORE_GROUP_COVERAGE) |
                       (before["valuation_cov"] < ranking.MIN_CORE_GROUP_COVERAGE))
        targets = before[target_mask].sort_values(
            ["data_cov", "quality_cov", "valuation_cov"]).head(max(0, args.max_symbols)).index.tolist()
    if not targets:
        log("No stocks meet the enrichment target")

    log(f"Targeting {len(targets)} stocks below {args.target_coverage:.0f}% weighted coverage "
        "or missing core quality/valuation evidence")
    changes = []
    if targets and not args.skip_nse:
        enrich_nse(raw, targets, changes, offline=args.offline)
    if targets and not args.skip_bse:
        enrich_bse(raw, targets, changes, workers=args.workers, offline=args.offline)
    if targets and not args.skip_yahoo:
        enrich_yahoo(raw, targets, changes, workers=args.workers, offline=args.offline)

    enriched = raw.reset_index()
    original_columns = ["ticker"] + [column for column in raw_frame.columns if column != "ticker"]
    for column in original_columns:
        if column not in enriched:
            enriched[column] = pd.NA
    atomic_csv(enriched[original_columns], RAW)

    universe = pd.read_csv(UNIVERSE)
    symbols = universe["symbol"].astype(str).str.strip().str.upper().tolist()
    screen = screening.build_screen(enriched, symbols)
    atomic_csv(screen, DATA / "screen_results.csv")

    change_frame = pd.DataFrame(changes, columns=REPORT_COLUMNS)
    if REPORT.exists():
        try:
            previous_audit = pd.read_csv(REPORT)
        except Exception:
            previous_audit = pd.DataFrame(columns=REPORT_COLUMNS)
    else:
        previous_audit = pd.DataFrame(columns=REPORT_COLUMNS)
    audit = pd.concat([previous_audit, change_frame], ignore_index=True)
    if len(audit):
        audit = audit.drop_duplicates(["ticker", "field"], keep="last").sort_values(
            ["ticker", "field"])
    atomic_csv(audit.reindex(columns=REPORT_COLUMNS), REPORT)
    after = scoring_coverage(enriched.set_index("ticker"), share)
    comparison = before.join(after, lsuffix="_before", rsuffix="_after")
    target_comparison = comparison.loc[[ticker for ticker in targets if ticker in comparison.index]]
    summary = {
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "target_coverage": args.target_coverage,
        "targeted_stocks": len(targets),
        "fields_recovered": len(changes),
        "audited_fields_total": len(audit),
        "providers": change_frame["provider"].value_counts().to_dict() if len(change_frame) else {},
        "coverage_before": {
            "mean": round(float(before["data_cov"].mean()), 2),
            "below_60": int((before["data_cov"] < 60).sum()),
            "minimum": round(float(before["data_cov"].min()), 1),
        },
        "coverage_after_raw_enrichment": {
            "mean": round(float(after["data_cov"].mean()), 2),
            "below_60": int((after["data_cov"] < 60).sum()),
            "minimum": round(float(after["data_cov"].min()), 1),
        },
        "target_mean_gain": (round(float(
            (target_comparison["data_cov_after"] - target_comparison["data_cov_before"]).mean()), 2)
                             if len(target_comparison) else 0),
    }
    atomic_json(summary, SUMMARY)
    log(f"Recovered {len(changes)} fields; audit -> {REPORT.relative_to(BASE)}")
    log(f"Coverage below 60%: {summary['coverage_before']['below_60']} -> "
        f"{summary['coverage_after_raw_enrichment']['below_60']} before shareholding recompute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
