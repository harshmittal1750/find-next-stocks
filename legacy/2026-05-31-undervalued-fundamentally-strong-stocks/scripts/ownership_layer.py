"""Build ownership signals using bulk NSE delivery files.

The previous version requested one month of delivery history separately for
every stock. NSE publishes the same information as a whole-market daily file,
so this version downloads/cache those daily files and aggregates all symbols
locally. A weekly run normally needs only the missing trading dates.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd
from nselib import capital_market as cm
from nselib import nsdl_fpi

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CACHE = DATA / "cache" / "nse_delivery"
DELIVERY_META = DATA / "cache" / "delivery_refresh.json"

INST = re.compile(r"FUND|MUTUAL|LIFE INSURANCE|INSURANCE|\bLIC\b|PENSION|FPI|FII|"
                  r"FOREIGN|ASSET MANAGE|\bAMC\b|INVESTMENT|CAPITAL|PORTFOLIO|PMS|"
                  r"VENTURES?|HOLDINGS?|TRUST|SOVEREIGN|GOVERNMENT|ABU DHABI|SINGAPORE|"
                  r"MORGAN|GOLDMAN|NOMURA|SOCIETE|BNP|CITIGROUP|VANGUARD|BLACKROCK|"
                  r"SMALLCAP|MIDCAP|MULTICAP|FLEXI|EQUITY", re.I)


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


def qty(value):
    try:
        return int(str(value).replace(",", "").strip())
    except Exception:
        return 0


def market_regime():
    """Market-level FII + MF equity flow from NSDL FPI (context, not per-stock)."""
    df = nsdl_fpi.fetch_nsdl_fpi_latest_investment_activity()
    df = df if hasattr(df, "columns") else pd.DataFrame(df)
    atomic_csv(df, DATA / "nsdl_fpi_latest.csv")
    report_date = df["REPORT_DATE"].iloc[0] if "REPORT_DATE" in df else "?"

    def net(mask):
        values = pd.to_numeric(df[mask]["NET_INVESTMENT_RS_CR"], errors="coerce")
        return round(float(values.sum()), 1) if len(values) else 0.0

    fii = net((df["ASSET_CLASS"] == "Equity") & (df["INVESTMENT_ROUTE"] == "Sub-total"))
    mf = net((df["ASSET_CLASS"] == "Mutual Funds") & (df["INVESTMENT_ROUTE"] == "Equity schemes"))
    debt = net(df["ASSET_CLASS"].astype(str).str.startswith("Debt") &
               (df["INVESTMENT_ROUTE"] == "Sub-total"))
    return {"report_date": report_date, "fii_equity_net_cr": fii,
            "mf_equity_net_cr": mf, "debt_net_cr": debt}


def deals():
    """Bulk + block deals over the last month, aggregated per symbol."""
    frames = []
    for fn, kind in [(cm.bulk_deal_data, "bulk"), (cm.block_deals_data, "block")]:
        try:
            data = fn(period="1M")
        except Exception:
            try:
                data = fn()
            except Exception as exc:
                print(f"  ! {kind} deals: {exc}", file=sys.stderr)
                continue
        if data is None or not len(data):
            continue
        data = data.copy()
        data["DealType"] = kind
        frames.append(data)
    if not frames:
        return pd.DataFrame(), {}
    raw = pd.concat(frames, ignore_index=True)
    atomic_csv(raw, DATA / "bulk_block_deals.csv")
    raw["q"] = raw["QuantityTraded"].map(qty)
    raw["is_inst"] = raw["ClientName"].astype(str).str.contains(INST)
    aggregated = {}
    for symbol, group in raw.groupby("Symbol"):
        buys = group[group["Buy/Sell"].str.upper().str.startswith("B")]
        sells = group[group["Buy/Sell"].str.upper().str.startswith("S")]
        inst_buyers = sorted(set(buys[buys["is_inst"]]["ClientName"].astype(str)))
        inst_sellers = sorted(set(sells[sells["is_inst"]]["ClientName"].astype(str)))
        aggregated[symbol] = {
            "deal_buy_qty": int(buys["q"].sum()),
            "deal_sell_qty": int(sells["q"].sum()),
            "inst_buyers": " | ".join(inst_buyers)[:300],
            "inst_sellers": " | ".join(inst_sellers)[:300],
            "n_inst_buy": len(inst_buyers),
            "n_inst_sell": len(inst_sellers),
        }
    return raw, aggregated


def load_delivery_day(trade_date):
    """Load one whole-market bhavcopy, downloading it only when not cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{trade_date.isoformat()}.csv"
    if path.exists():
        return pd.read_csv(path), False
    frame = cm.bhav_copy_with_delivery(trade_date.strftime("%d-%m-%Y"))
    atomic_csv(frame, path)
    return frame, True


def delivery_sessions(target_sessions=20, max_lookback_days=50):
    """Return the most recent available whole-market delivery sessions."""
    frames, downloaded = [], 0
    for offset in range(max_lookback_days + 1):
        trade_date = dt.date.today() - dt.timedelta(days=offset)
        if trade_date.weekday() >= 5:
            continue
        try:
            frame, was_downloaded = load_delivery_day(trade_date)
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"  ! delivery file {trade_date}: {str(exc)[:140]}", file=sys.stderr)
            continue
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame.columns = [str(col).strip().replace(" ", "") for col in frame.columns]
        if not {"SYMBOL", "DELIV_PER"}.issubset(frame.columns):
            print(f"  ! delivery file {trade_date}: expected columns missing", file=sys.stderr)
            continue
        frame["trade_date"] = pd.Timestamp(trade_date)
        frames.append(frame)
        downloaded += int(was_downloaded)
        if was_downloaded:
            time.sleep(0.15)
        if len(frames) >= target_sessions:
            break
    if not frames:
        return pd.DataFrame(), downloaded
    return pd.concat(frames, ignore_index=True), downloaded


def aggregate_delivery(history):
    """Calculate the same recent-vs-prior delivery metrics for every symbol."""
    if history is None or history.empty:
        return {}
    frame = history.copy()
    frame["SYMBOL"] = frame["SYMBOL"].astype(str).str.strip()
    frame["dly"] = pd.to_numeric(frame["DELIV_PER"], errors="coerce")
    volume = frame["TTL_TRD_QNTY"] if "TTL_TRD_QNTY" in frame else pd.Series(0, index=frame.index)
    frame["volume"] = pd.to_numeric(volume, errors="coerce").fillna(0)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["SYMBOL", "dly", "trade_date"])
    # If a symbol appears in multiple series on one day, use its most-liquid row.
    frame = frame.sort_values(["SYMBOL", "trade_date", "volume"]).drop_duplicates(
        ["SYMBOL", "trade_date"], keep="last")
    result = {}
    for symbol, group in frame.groupby("SYMBOL"):
        values = group.sort_values("trade_date", ascending=False)["dly"]
        if len(values) < 4:
            continue
        recent = values.head(5).mean()
        prior = values.iloc[5:].mean() if len(values) > 5 else values.tail(max(1, len(values) - 1)).mean()
        result[symbol] = {
            "avg_delivery_pct": round(float(values.mean()), 1),
            "delivery_recent": round(float(recent), 1),
            "delivery_trend": round(float(recent - prior), 1),
        }
    return result


def prior_delivery():
    path = DATA / "ownership_overlay.csv"
    if not path.exists():
        return {}
    try:
        old = pd.read_csv(path)
        cols = [c for c in ["avg_delivery_pct", "delivery_trend"] if c in old]
        return old.set_index("ticker")[cols].to_dict("index") if cols else {}
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-sessions", type=int, default=20)
    args = parser.parse_args()

    screen = pd.read_csv(DATA / "screen_results.csv")
    fallback_delivery = prior_delivery()
    print("Market regime (NSDL FPI)…")
    regime = market_regime()
    print("  ", regime)

    print("Bulk/block deals…")
    _, deal_agg = deals()
    print(f"  symbols with deals: {len(deal_agg)}")

    print(f"Whole-market NSE delivery files ({args.delivery_sessions} sessions)…")
    delivery_history, downloaded = delivery_sessions(args.delivery_sessions)
    delivery_by_symbol = aggregate_delivery(delivery_history)
    sessions = delivery_history["trade_date"].nunique() if len(delivery_history) else 0
    if not delivery_by_symbol and not fallback_delivery:
        raise RuntimeError("no fresh or cached delivery data is available")
    print(f"  sessions={sessions}, newly downloaded={downloaded}, symbols={len(delivery_by_symbol)}")
    if sessions < args.delivery_sessions:
        print("  ! incomplete fresh delivery history; cached per-symbol values will fill gaps", file=sys.stderr)

    rows = []
    for _, row in screen.sort_values("score", ascending=False).iterrows():
        symbol = row["ticker"]
        output = dict(row)
        delivery = dict(fallback_delivery.get(symbol, {}))
        delivery.update(delivery_by_symbol.get(symbol, {}))
        deal = deal_agg.get(symbol, {})
        output.update(delivery)
        output.update(deal)
        ownership_score, why = 0, []
        trend = delivery.get("delivery_trend")
        average = delivery.get("avg_delivery_pct")
        if average is not None and pd.notna(average):
            if average >= 60: ownership_score += 2; why.append(f"high delivery {average}%")
            elif average >= 45: ownership_score += 1
        if trend is not None and pd.notna(trend) and trend >= 5:
            ownership_score += 2; why.append(f"delivery rising +{trend}")
        elif trend is not None and pd.notna(trend) and trend <= -5:
            ownership_score -= 1
        buy_qty, sell_qty = deal.get("deal_buy_qty", 0), deal.get("deal_sell_qty", 0)
        if deal.get("n_inst_buy", 0) > deal.get("n_inst_sell", 0) and buy_qty > sell_qty:
            ownership_score += 3; why.append(f"net institutional BUY ({deal.get('n_inst_buy')} funds)")
        elif deal.get("n_inst_sell", 0) > deal.get("n_inst_buy", 0) and sell_qty > buy_qty:
            ownership_score -= 2; why.append(f"institutional SELLING ({deal.get('n_inst_sell')} funds)")
        output["ownership_score"] = ownership_score
        output["ownership_reasons"] = "; ".join(why)
        output["combined_score"] = row["score"] + ownership_score
        rows.append(output)

    out = pd.DataFrame(rows).sort_values("combined_score", ascending=False)
    keep = ["ticker", "shortName", "sector", "currentPrice", "pct_below_52w_high", "trailingPE",
            "roe_pct", "debtToEquity", "score", "avg_delivery_pct", "delivery_trend",
            "n_inst_buy", "n_inst_sell", "inst_buyers", "ownership_score", "combined_score",
            "reasons", "ownership_reasons"]
    keep = [col for col in keep if col in out.columns]
    atomic_csv(out[keep], DATA / "ownership_overlay.csv")
    atomic_json({"refreshed_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "sessions": int(sessions), "new_files": downloaded,
                 "fresh_symbols": len(delivery_by_symbol)}, DELIVERY_META)
    print(f"Saved -> {DATA / 'ownership_overlay.csv'}")
    print(f"MARKET REGIME ({regime['report_date']}): FII equity net {regime['fii_equity_net_cr']} cr | "
          f"MF equity net {regime['mf_equity_net_cr']} cr")


if __name__ == "__main__":
    main()
