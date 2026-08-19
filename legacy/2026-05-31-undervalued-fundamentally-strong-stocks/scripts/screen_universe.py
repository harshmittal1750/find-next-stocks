"""Refresh the >=1000cr universe with a fast price path and cached fundamentals.

The old implementation called ``Ticker.info`` sequentially for every stock on
every weekly run. Most of those fields change only when a company reports, so
this version keeps them for 30 days by default and refreshes daily price history
in Yahoo batches. The local history cache makes later runs incremental.

Reads : data/universe_filtered.csv
Cache : data/cache/price_history.csv
        data/cache/fundamentals_refresh.json
Writes: data/raw_fundamentals.csv
        data/screen_results.csv

Use ``--full-fundamentals`` to force a complete Yahoo fundamentals refresh.
"""
import argparse
import datetime as dt
import json
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd
import yfinance as yf

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "cache"
RAW = DATA / "raw_fundamentals.csv"
PRICE_CACHE = CACHE / "price_history.csv"
FUND_META = CACHE / "fundamentals_refresh.json"
PRICE_META = CACHE / "price_refresh.json"

WANT = ["shortName", "sector", "industry", "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "trailingPE", "forwardPE", "priceToBook", "pegRatio", "returnOnEquity", "returnOnAssets",
        "debtToEquity", "earningsGrowth", "earningsQuarterlyGrowth", "revenueGrowth",
        "profitMargins", "operatingMargins", "grossMargins", "ebitdaMargins", "dividendYield", "marketCap",
        "currentRatio", "quickRatio", "freeCashflow", "totalCashPerShare", "bookValue",
        "targetMeanPrice", "targetHighPrice", "targetLowPrice", "recommendationMean", "numberOfAnalystOpinions",
        "twoHundredDayAverage", "fiftyDayAverage", "fiftyTwoWeekChangePercent", "beta",
        "enterpriseToEbitda", "enterpriseValue", "trailingEps", "forwardEps",
        "heldPercentInsiders", "heldPercentInstitutions"]
PRICE_FIELDS = ["currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                "twoHundredDayAverage", "fiftyDayAverage", "fiftyTwoWeekChangePercent"]
HISTORY_COLUMNS = ["ticker", "date", "open", "high", "low", "close"]


def atomic_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def atomic_json(payload, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_raw():
    if not RAW.exists():
        return pd.DataFrame(columns=["ticker"] + WANT)
    try:
        return pd.read_csv(RAW)
    except Exception as exc:
        raise RuntimeError(f"cannot read {RAW}: {exc}") from exc


def fundamentals_age_days(raw_mtime=None):
    if FUND_META.exists():
        try:
            stamp = json.loads(FUND_META.read_text(encoding="utf-8"))["refreshed_at"]
            when = dt.datetime.fromisoformat(stamp)
            return (dt.datetime.now() - when).total_seconds() / 86400, when
        except Exception:
            pass
    if raw_mtime is not None:
        when = dt.datetime.fromtimestamp(raw_mtime)
        return (dt.datetime.now() - when).total_seconds() / 86400, when
    return float("inf"), None


def fetch_info(sym, retries=3):
    for attempt in range(retries):
        try:
            info = yf.Ticker(f"{sym}.NS").info
            if info and info.get("currentPrice"):
                row = {"ticker": sym}
                row.update({key: info.get(key) for key in WANT})
                return row
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  ! {sym}: {repr(exc)[:100]}", file=sys.stderr)
        time.sleep(1.5 * (attempt + 1))
    return None


def refresh_fundamentals(symbols, cached, workers):
    """Fetch fundamentals concurrently, retaining cached values for failed symbols."""
    print(f"Full fundamentals refresh: {len(symbols)} stocks, {workers} workers")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_info, sym): sym for sym in symbols}
        for i, future in enumerate(as_completed(futures), 1):
            row = future.result()
            if row:
                rows.append(row)
            if i % 50 == 0 or i == len(futures):
                print(f"  fundamentals [{i}/{len(futures)}] successful={len(rows)}")

    coverage = len(rows) / max(1, len(symbols))
    if coverage < 0.80:
        raise RuntimeError(f"fundamentals coverage only {coverage:.1%}; keeping previous files")

    fresh = pd.DataFrame(rows).set_index("ticker")
    if len(cached):
        merged = cached.drop_duplicates("ticker", keep="last").set_index("ticker")
        merged.update(fresh)
        missing = fresh.index.difference(merged.index)
        if len(missing):
            merged = pd.concat([merged, fresh.loc[missing]])
    else:
        merged = fresh
    merged = merged.reindex(symbols).reset_index()
    print(f"Fundamentals fetched for {len(rows)}/{len(symbols)} stocks ({coverage:.1%})")
    return merged, coverage


def load_price_cache():
    if not PRICE_CACHE.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        history = pd.read_csv(PRICE_CACHE)
        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        return history.dropna(subset=["ticker", "date", "close"])
    except Exception as exc:
        print(f"  ! ignoring unreadable price cache: {exc}", file=sys.stderr)
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def symbol_frame(downloaded, yahoo_symbol):
    if downloaded is None or downloaded.empty:
        return None
    frame = None
    if isinstance(downloaded.columns, pd.MultiIndex):
        level0 = downloaded.columns.get_level_values(0)
        level1 = downloaded.columns.get_level_values(1)
        if yahoo_symbol in level0:
            frame = downloaded[yahoo_symbol]
        elif yahoo_symbol in level1:
            frame = downloaded.xs(yahoo_symbol, axis=1, level=1)
    else:
        frame = downloaded
    if frame is None or frame.empty:
        return None
    frame = frame.rename(columns={c: str(c).strip().lower().replace(" ", "_") for c in frame.columns})
    if "close" not in frame:
        return None
    def numeric_column(name):
        if name not in frame:
            return pd.Series(index=frame.index, dtype=float)
        return pd.to_numeric(frame[name], errors="coerce")

    out = pd.DataFrame(index=frame.index)
    out["date"] = pd.to_datetime(frame.index, errors="coerce")
    out["open"] = numeric_column("open").to_numpy()
    out["high"] = numeric_column("high").to_numpy()
    out["low"] = numeric_column("low").to_numpy()
    out["close"] = numeric_column("close").to_numpy()
    return out.dropna(subset=["date", "close"])


def download_history(symbols, *, period=None, start=None, batch_size=100):
    """Download daily OHLC in bounded batches and return a long dataframe."""
    rows = []
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset:offset + batch_size]
        yahoo_symbols = [f"{sym}.NS" for sym in batch]
        kwargs = dict(interval="1d", group_by="ticker", auto_adjust=False,
                      actions=False, threads=True, progress=False, timeout=30)
        if start is not None:
            kwargs["start"] = start
        else:
            kwargs["period"] = period or "1y"
        try:
            downloaded = yf.download(yahoo_symbols, **kwargs)
        except Exception as exc:
            print(f"  ! price batch {offset + 1}-{offset + len(batch)}: {exc}", file=sys.stderr)
            continue
        found = 0
        for sym, yahoo_symbol in zip(batch, yahoo_symbols):
            frame = symbol_frame(downloaded, yahoo_symbol)
            if frame is None or frame.empty:
                continue
            frame.insert(0, "ticker", sym)
            rows.append(frame[HISTORY_COLUMNS])
            found += 1
        print(f"  prices [{min(offset + len(batch), len(symbols))}/{len(symbols)}] batch coverage={found}/{len(batch)}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=HISTORY_COLUMNS)


def update_price_cache(symbols, batch_size):
    history = load_price_cache()
    if len(history):
        counts = history.groupby("ticker")["date"].count()
        # Recently listed stocks legitimately have fewer than 200 sessions; once
        # a symbol has a usable seed history, incremental updates are sufficient.
        complete = [sym for sym in symbols if counts.get(sym, 0) >= 10]
        incomplete = [sym for sym in symbols if sym not in set(complete)]
        latest = history["date"].max()
        start = (latest - pd.Timedelta(days=10)).date().isoformat()
        print(f"Incremental prices from {start}: {len(complete)} cached stocks")
        new = download_history(complete, start=start, batch_size=batch_size)
        if incomplete:
            print(f"One-year price backfill: {len(incomplete)} new/incomplete stocks")
            backfill = download_history(incomplete, period="1y", batch_size=batch_size)
            new = pd.concat([new, backfill], ignore_index=True)
        history = pd.concat([history, new], ignore_index=True)
    else:
        print(f"Initial one-year price backfill: {len(symbols)} stocks")
        history = download_history(symbols, period="1y", batch_size=batch_size)

    if history.empty:
        raise RuntimeError("price download returned no usable rows")
    history["date"] = pd.to_datetime(history["date"], errors="coerce", utc=True).dt.tz_convert(None)
    cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(days=400))
    history = history[(history["ticker"].isin(symbols)) & (history["date"] >= cutoff)]
    history = history.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    stored = history.copy()
    stored["date"] = stored["date"].dt.date.astype(str)
    atomic_csv(stored[HISTORY_COLUMNS], PRICE_CACHE)
    return history


def price_metrics(history, max_staleness_days=10):
    metrics = {}
    cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(days=max_staleness_days))
    fresh = 0
    for ticker, frame in history.groupby("ticker"):
        frame = frame.sort_values("date").dropna(subset=["close"])
        if frame.empty:
            continue
        last = frame.iloc[-1]
        closes = frame["close"]
        first = closes.iloc[0]
        metrics[ticker] = {
            "currentPrice": float(last["close"]),
            "fiftyTwoWeekHigh": float(frame["high"].max()) if frame["high"].notna().any() else None,
            "fiftyTwoWeekLow": float(frame["low"].min()) if frame["low"].notna().any() else None,
            "fiftyDayAverage": float(closes.tail(50).mean()),
            "twoHundredDayAverage": float(closes.tail(200).mean()),
            "fiftyTwoWeekChangePercent": float((closes.iloc[-1] / first - 1) * 100) if first else None,
            "price_asof": pd.Timestamp(last["date"]).date().isoformat(),
        }
        if pd.Timestamp(last["date"]) >= cutoff:
            fresh += 1
    return metrics, fresh


def apply_prices(raw, symbols, metrics):
    raw = raw.drop_duplicates("ticker", keep="last").set_index("ticker").reindex(symbols)
    # CSV inference often makes marketCap int64; price-ratio updates are floats.
    # Coerce these columns once so pandas does not reject non-integral values.
    for field in PRICE_FIELDS + ["marketCap"]:
        raw[field] = pd.to_numeric(raw[field], errors="coerce").astype(float)
    for ticker, values in metrics.items():
        if ticker not in raw.index:
            continue
        old_price = pd.to_numeric(pd.Series([raw.at[ticker, "currentPrice"]]), errors="coerce").iloc[0]
        old_mcap = pd.to_numeric(pd.Series([raw.at[ticker, "marketCap"]]), errors="coerce").iloc[0]
        new_price = values["currentPrice"]
        for field in PRICE_FIELDS:
            value = values.get(field)
            if value is not None:
                raw.at[ticker, field] = value
        if pd.notna(old_mcap) and pd.notna(old_price) and old_price > 0:
            raw.at[ticker, "marketCap"] = old_mcap * new_price / old_price
    raw.index.name = "ticker"
    return raw.reset_index()


def build_screen(raw, symbols):
    df = raw[raw["ticker"].astype(str).isin(set(symbols))]
    d = df.dropna(subset=["currentPrice"]).copy()
    d["pct_below_52w_high"] = (d["fiftyTwoWeekHigh"] - d["currentPrice"]) / d["fiftyTwoWeekHigh"] * 100
    d["pct_above_52w_low"] = (d["currentPrice"] - d["fiftyTwoWeekLow"]) / d["fiftyTwoWeekLow"] * 100
    d["roe_pct"] = d["returnOnEquity"] * 100
    d["margin_pct"] = d["profitMargins"] * 100

    def score(row):
        pts, reasons = 0, []
        pbh = row["pct_below_52w_high"]
        if pd.notna(pbh):
            if pbh >= 30: pts += 3; reasons.append(f"{pbh:.0f}% below 52w high")
            elif pbh >= 20: pts += 2; reasons.append(f"{pbh:.0f}% below 52w high")
            elif pbh >= 12: pts += 1
        roe = row.get("returnOnEquity")
        if pd.notna(roe):
            if roe >= 0.18: pts += 3; reasons.append(f"ROE {roe * 100:.0f}%")
            elif roe >= 0.12: pts += 2
            elif roe < 0: pts -= 2
        debt = row.get("debtToEquity")
        if pd.notna(debt):
            if debt < 50: pts += 2; reasons.append(f"low D/E {debt:.0f}")
            elif debt < 100: pts += 1
            elif debt > 200: pts -= 2
        margin = row.get("profitMargins")
        if pd.notna(margin):
            if margin >= 0.15: pts += 2; reasons.append(f"margin {margin * 100:.0f}%")
            elif margin > 0: pts += 1
            else: pts -= 2
        earnings = row.get("earningsGrowth")
        if pd.notna(earnings):
            if earnings >= 0.15: pts += 2; reasons.append(f"earnings +{earnings * 100:.0f}%")
            elif earnings > 0: pts += 1
            elif earnings < -0.10: pts -= 1
        revenue = row.get("revenueGrowth")
        if pd.notna(revenue) and revenue >= 0.10: pts += 1; reasons.append(f"rev +{revenue * 100:.0f}%")
        pe = row.get("trailingPE")
        if pd.notna(pe) and 0 < pe < 18: pts += 1; reasons.append(f"PE {pe:.0f}")
        pb = row.get("priceToBook")
        if pd.notna(pb) and 0 < pb < 3: pts += 1
        return pd.Series({"score": pts, "reasons": "; ".join(reasons)})

    d[["score", "reasons"]] = d.apply(score, axis=1)
    cols = ["ticker", "shortName", "sector", "currentPrice", "pct_below_52w_high", "pct_above_52w_low",
            "trailingPE", "priceToBook", "roe_pct", "debtToEquity", "margin_pct", "earningsGrowth",
            "revenueGrowth", "marketCap", "score", "reasons"]
    return d.sort_values("score", ascending=False)[cols]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-fundamentals", action="store_true",
                        help="force all per-stock Yahoo fundamentals calls")
    parser.add_argument("--fundamentals-max-age-days", type=float, default=30)
    parser.add_argument("--fundamental-workers", type=int, default=6)
    parser.add_argument("--price-batch-size", type=int, default=100)
    parser.add_argument("--min-price-coverage", type=float, default=0.90)
    args = parser.parse_args()

    universe = pd.read_csv(DATA / "universe_filtered.csv")
    symbols = universe["symbol"].astype(str).str.strip().tolist()
    raw_mtime = RAW.stat().st_mtime if RAW.exists() else None
    raw = read_raw()
    age, inferred_refresh = fundamentals_age_days(raw_mtime)
    full_refresh = args.full_fundamentals or raw.empty or age >= args.fundamentals_max_age_days
    if full_refresh:
        raw, fund_coverage = refresh_fundamentals(symbols, raw, args.fundamental_workers)
        inferred_refresh = dt.datetime.now()
    else:
        fund_coverage = raw.loc[raw["ticker"].isin(symbols), "ticker"].nunique() / max(1, len(symbols))
        print(f"Using cached fundamentals ({age:.1f} days old); pass --full-fundamentals to override")

    history = update_price_cache(symbols, args.price_batch_size)
    metrics, fresh_prices = price_metrics(history)
    price_coverage = fresh_prices / max(1, len(symbols))
    print(f"Fresh price coverage: {fresh_prices}/{len(symbols)} ({price_coverage:.1%})")
    if price_coverage < args.min_price_coverage:
        raise RuntimeError(f"fresh price coverage {price_coverage:.1%} is below {args.min_price_coverage:.0%}")

    raw = apply_prices(raw, symbols, metrics)
    atomic_csv(raw[["ticker"] + WANT], RAW)
    atomic_json({"refreshed_at": inferred_refresh.isoformat(timespec="seconds"),
                 "coverage": round(fund_coverage, 4)}, FUND_META)
    atomic_json({"refreshed_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "fresh_symbols": fresh_prices, "universe": len(symbols)}, PRICE_META)

    screen = build_screen(raw, symbols)
    atomic_csv(screen, DATA / "screen_results.csv")
    print(f"Saved screen_results.csv ({len(screen)} rows). Top 10:")
    pd.set_option("display.width", 200, "display.max_columns", 20)
    print(screen.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
