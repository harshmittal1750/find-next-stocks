"""
diff_rankings.py — detect MAJOR week-over-week changes between two
`final_ranking.csv` snapshots and render them as an HTML/text email.

Used by weekly_update.py; also runnable standalone for testing:
  ./.venv/bin/python diff_rankings.py PREV.csv NEW.csv            # print summary
  ./.venv/bin/python diff_rankings.py PREV.csv NEW.csv --html out.html

A "major change" is anything in the THRESHOLDS block below: a stock entering or
leaving the Top-N, a big rank/score move, a big week-over-week price move, a
sharp drawdown deepening, or a delivery-trend flip (accumulation<->distribution).
Tune the thresholds to make the email noisier or quieter.
Legal: personal/internal research use only.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

# --------------------------- THRESHOLDS (tune here) ---------------------------
TOP_N            = 25     # the "Top list" we watch for entrants / drop-outs
RANK_MOVE        = 25     # |Δrank| (positions) to count as a big mover
SCORE_MOVE       = 5.0    # |Δfinal_score| (points, 0-100 scale)
PRICE_MOVE_PCT   = 15.0   # |% change in price| week-over-week
DRAWDOWN_MOVE_PP = 10.0   # increase in "% below 52w high" (percentage points)
DELIV_FLIP       = 5.0    # delivery_trend magnitude for an accum/distribution flip
MAX_ROWS         = 15     # cap rows shown per section in the email

NUMCOLS = ["rank", "currentPrice", "pct_below_52w_high", "final_score",
           "trailingPE", "roe_pct", "delivery_trend"]


# --------------------------------- loading -----------------------------------
def load(path):
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].astype(str).str.strip()
    for c in NUMCOLS:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ------------------------------- formatting ----------------------------------
def _f1(x):    return "—" if pd.isna(x) else f"{x:.1f}"
def _f0(x):    return "—" if pd.isna(x) else f"{x:.0f}"
def _sgn(x, n=1): return "—" if pd.isna(x) else f"{x:+.{n}f}"
def _money(x):
    if pd.isna(x): return "—"
    return f"₹{x:,.0f}" if abs(x) >= 100 else f"₹{x:,.1f}"
def _arrow(a, b, fmt=_f0):
    return f"{fmt(a)} → {fmt(b)}"


# ------------------------------- diff engine ---------------------------------
def compute(prev, new, top_n=TOP_N):
    """Return a dict: {summary: {...}, sections: {key: {title, note, rows[]}}}."""
    p = prev.add_suffix("_prev").rename(columns={"ticker_prev": "ticker"})
    n = new.add_suffix("_new").rename(columns={"ticker_new": "ticker"})
    m = n.merge(p, on="ticker", how="outer", indicator=True)

    def col(name):
        return m[name] if name in m else pd.Series(np.nan, index=m.index)

    m["name"] = col("shortName_new").fillna(col("shortName_prev")).fillna(m["ticker"])
    m["sector"] = col("sector_new").fillna(col("sector_prev")).fillna("—")
    m["d_rank"]  = col("rank_prev") - col("rank_new")              # + = improved
    m["d_score"] = col("final_score_new") - col("final_score_prev")
    with np.errstate(divide="ignore", invalid="ignore"):
        m["d_price_pct"] = (col("currentPrice_new") / col("currentPrice_prev") - 1) * 100
    m["d_draw"]  = col("pct_below_52w_high_new") - col("pct_below_52w_high_prev")

    both = m["_merge"] == "both"
    in_new = m["rank_new"].notna()
    in_prev = m["rank_prev"].notna()

    sections = {}

    def add(key, title, note, df, builder):
        rows = [builder(r) for _, r in df.iterrows()]
        if rows:
            sections[key] = {"title": title, "note": note, "rows": rows}

    # 1. New entrants to the Top-N
    new_top = m[in_new & (m["rank_new"] <= top_n) &
                ((col("rank_prev") > top_n) | col("rank_prev").isna())]
    add("new_top", f"🟢 New entrants to Top {top_n}",
        "Rose into the watch list this week.",
        new_top.sort_values("rank_new"),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Rank": _arrow(r.get("rank_prev"), r["rank_new"]),
                   "Score": _f1(r.get("final_score_new")),
                   "Price": _money(r.get("currentPrice_new")),
                   "Below 52w hi": _f0(r.get("pct_below_52w_high_new")) + "%"})

    # 2. Drop-outs from the Top-N
    drop_top = m[in_prev & (m["rank_prev"] <= top_n) &
                 ((col("rank_new") > top_n) | col("rank_new").isna())]
    add("dropped_top", f"🔴 Dropped out of Top {top_n}",
        "Fell off the watch list this week.",
        drop_top.sort_values("rank_prev"),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Rank": _arrow(r.get("rank_prev"), r.get("rank_new")),
                   "Score": _arrow(r.get("final_score_prev"), r.get("final_score_new"), _f1)})

    # 3. Big week-over-week PRICE moves (the most direct "something happened")
    price = m[both & (m["d_price_pct"].abs() >= PRICE_MOVE_PCT)]
    add("price_moves", f"💸 Big price moves (≥{PRICE_MOVE_PCT:.0f}%)",
        "Largest week-over-week price changes among ranked stocks.",
        price.reindex(price["d_price_pct"].abs().sort_values(ascending=False).index).head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"],
                   "Price": _arrow(r.get("currentPrice_prev"), r.get("currentPrice_new"), _money),
                   "Δ%": _sgn(r["d_price_pct"], 1) + "%",
                   "Rank now": _f0(r.get("rank_new")), "Score": _f1(r.get("final_score_new"))})

    # 4. Big RANK gainers
    up = m[both & (m["d_rank"] >= RANK_MOVE)]
    add("rank_up", f"📈 Big rank gainers (≥{RANK_MOVE} places)",
        "Moved up the ranking the most.",
        up.sort_values("d_rank", ascending=False).head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Rank": _arrow(r.get("rank_prev"), r["rank_new"]),
                   "Δ": _sgn(r["d_rank"], 0),
                   "Score": _arrow(r.get("final_score_prev"), r.get("final_score_new"), _f1)})

    # 5. Big RANK losers
    down = m[both & (m["d_rank"] <= -RANK_MOVE)]
    add("rank_down", f"📉 Big rank losers (≥{RANK_MOVE} places)",
        "Slid down the ranking the most.",
        down.sort_values("d_rank").head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Rank": _arrow(r.get("rank_prev"), r["rank_new"]),
                   "Δ": _sgn(r["d_rank"], 0),
                   "Score": _arrow(r.get("final_score_prev"), r.get("final_score_new"), _f1)})

    # 6. Sharp drawdown deepening (fell a lot further from its 52w high)
    draw = m[both & (m["d_draw"] >= DRAWDOWN_MOVE_PP)]
    add("drawdown", f"⬇️ Drawdown deepened (≥{DRAWDOWN_MOVE_PP:.0f}pp further from 52w high)",
        "Now meaningfully cheaper vs their highs — potential entries or value traps; check why.",
        draw.sort_values("d_draw", ascending=False).head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Below 52w hi": _arrow(r.get("pct_below_52w_high_prev"),
                                          r.get("pct_below_52w_high_new"), _f0) + " %",
                   "Price Δ%": _sgn(r.get("d_price_pct"), 1) + "%",
                   "Rank now": _f0(r.get("rank_new"))})

    # 7. Delivery-trend flips (our free FII/DII accumulation<->distribution proxy)
    dt_prev = col("delivery_trend_prev"); dt_new = col("delivery_trend_new")
    accum = m[both & (dt_prev < 0) & (dt_new >= DELIV_FLIP)]
    distr = m[both & (dt_prev > 0) & (dt_new <= -DELIV_FLIP)]
    flips = pd.concat([accum.assign(_flip="Accumulation started ▲"),
                       distr.assign(_flip="Distribution started ▼")])
    add("delivery_flips", "🔄 Delivery-trend flips",
        "Delivery %% (conviction proxy) reversed direction — accumulation or distribution turning on.",
        flips.reindex(flips["delivery_trend_new"].abs().sort_values(ascending=False).index).head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Signal": r["_flip"],
                   "Delivery trend": _arrow(r.get("delivery_trend_prev"),
                                            r.get("delivery_trend_new"), _f1),
                   "Rank now": _f0(r.get("rank_new"))})

    # 8. Universe changes (added / removed tickers)
    added = m[m["_merge"] == "left_only"]
    removed = m[m["_merge"] == "right_only"]
    add("added", "➕ New to the universe",
        "Newly priced / newly ≥₹1000cr (or fixed data) this week.",
        added.sort_values("rank_new").head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Rank": _f0(r.get("rank_new")), "Score": _f1(r.get("final_score_new"))})
    add("removed", "➖ Dropped from the universe",
        "No longer priced / fell below the size cut this week.",
        removed.sort_values("rank_prev").head(MAX_ROWS),
        lambda r: {"Ticker": r["ticker"], "Name": r["name"], "Sector": r["sector"],
                   "Last rank": _f0(r.get("rank_prev"))})

    summary = {sections[k]["title"]: len(sections[k]["rows"]) for k in sections}
    return {"summary": summary, "sections": sections}


# ------------------------------- rendering -----------------------------------
_TH = ("padding:6px 10px;text-align:left;font-size:12px;color:#fff;"
       "background:#334155;border:0;")
_TD = "padding:6px 10px;font-size:13px;border-bottom:1px solid #e2e8f0;color:#0f172a;"


def _table(rows):
    cols = list(rows[0].keys())
    head = "".join(f'<th style="{_TH}">{c}</th>' for c in cols)
    body = ""
    for i, r in enumerate(rows):
        bg = "#f8fafc" if i % 2 else "#ffffff"
        tds = "".join(f'<td style="{_TD}background:{bg};">{r.get(c, "")}</td>' for c in cols)
        body += f"<tr>{tds}</tr>"
    return (f'<table cellspacing="0" cellpadding="0" '
            f'style="border-collapse:collapse;width:100%;margin:6px 0 18px;">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def _regime_html(regime):
    if not regime:
        return ""
    def tag(v):
        if v is None or pd.isna(v): return "—"
        color = "#16a34a" if v >= 0 else "#dc2626"
        return f'<b style="color:{color}">{v:+,.0f} cr</b>'
    return (
        '<div style="background:#f1f5f9;border-radius:8px;padding:12px 14px;margin:0 0 18px;">'
        f'<div style="font-size:12px;color:#64748b;margin-bottom:4px;">Market regime '
        f'(NSDL FPI, report {regime.get("report_date","?")} — may lag, context only)</div>'
        f'<div style="font-size:14px;">FII equity {tag(regime.get("fii_equity_net_cr"))} '
        f'&nbsp;·&nbsp; MF equity {tag(regime.get("mf_equity_net_cr"))} '
        f'&nbsp;·&nbsp; Debt {tag(regime.get("debt_net_cr"))}</div></div>')


def render_email(changes, *, run_date, prev_date=None, regime=None,
                 top_now=None, universe=None):
    """Return (subject, html, text)."""
    sec = changes["sections"] if changes else {}
    n_new = len(sec.get("new_top", {}).get("rows", []))
    n_price = len(sec.get("price_moves", {}).get("rows", []))

    if changes is None:
        subject = f"📊 Weekly stock screen — baseline established ({run_date})"
        headline = ("First run — this is the baseline. From next week you'll get the "
                    "major week-over-week changes here.")
    else:
        subject = (f"📊 Weekly stock screen — {n_new} new in Top {TOP_N}, "
                   f"{n_price} big price move{'s' if n_price != 1 else ''} ({run_date})")
        if changes["summary"]:
            headline = "Major changes this week: " + ", ".join(
                f"{v} {t.split(' ', 1)[-1].lower()}" for t, v in changes["summary"].items())
        else:
            headline = "No major changes this week (everything within thresholds)."

    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:720px;margin:0 auto;color:#0f172a;">',
        '<h2 style="margin:0 0 2px;">📊 Weekly Undervalued-Stock Screen</h2>',
        f'<div style="color:#64748b;font-size:13px;margin-bottom:14px;">'
        f'Run {run_date}'
        + (f' vs baseline {prev_date}' if prev_date else '')
        + (f' · universe {universe} stocks' if universe else '') + '</div>',
        f'<div style="background:#eff6ff;border-left:4px solid #2563eb;padding:10px 14px;'
        f'border-radius:6px;margin-bottom:18px;font-size:14px;">{headline}</div>',
        _regime_html(regime),
    ]

    for key, s in sec.items():
        parts.append(f'<h3 style="margin:18px 0 2px;font-size:16px;">{s["title"]}</h3>')
        parts.append(f'<div style="color:#64748b;font-size:12px;">{s["note"]}</div>')
        parts.append(_table(s["rows"]))

    if top_now is not None and len(top_now):
        rows = [{"#": _f0(r.get("rank")), "Ticker": r.get("ticker"),
                 "Name": r.get("shortName", ""), "Sector": r.get("sector", ""),
                 "Score": _f1(r.get("final_score")),
                 "Below 52w hi": _f0(r.get("pct_below_52w_high")) + "%",
                 "PE": _f1(r.get("trailingPE")), "ROE": _f0(r.get("roe_pct")) + "%"}
                for _, r in top_now.iterrows()]
        parts.append('<h3 style="margin:22px 0 2px;font-size:16px;">🏆 Current Top 10</h3>')
        parts.append(_table(rows))

    parts.append(
        '<div style="color:#94a3b8;font-size:11px;margin-top:24px;border-top:1px solid #e2e8f0;'
        'padding-top:10px;">Generated locally from NSE/yfinance data (free sources; '
        'cross-check finalists on Screener.in). Personal research only — not investment '
        'advice. Thresholds: price ≥%s%%, rank ≥%s, score ≥%s, drawdown ≥%spp.</div>'
        % (int(PRICE_MOVE_PCT), RANK_MOVE, int(SCORE_MOVE), int(DRAWDOWN_MOVE_PP)))
    parts.append("</div>")
    html = "\n".join(parts)

    # plain-text fallback
    tlines = [subject, "", headline, ""]
    for key, s in sec.items():
        tlines.append(s["title"])
        for r in s["rows"]:
            tlines.append("  " + " | ".join(f"{k}: {v}" for k, v in r.items()))
        tlines.append("")
    text = "\n".join(tlines)
    return subject, html, text


# --------------------------------- CLI ---------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prev"); ap.add_argument("new")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--html")
    args = ap.parse_args()
    changes = compute(load(args.prev), load(args.new), top_n=args.top)
    print("Section counts:")
    for t, c in changes["summary"].items():
        print(f"  {c:>3}  {t}")
    if args.html:
        _, html, _ = render_email(changes, run_date="(test)")
        Path(args.html).write_text(html)
        print(f"\nWrote {args.html}")


if __name__ == "__main__":
    main()
