"""
build_report.py — synthesize the full >=1000cr pipeline into a polished, self-contained
HTML dashboard + markdown master report.

Produces (in the research folder root):
  REPORT.html   -- styled dashboard: top picks, multi-lens, sectors, value-traps, methodology
  REPORT.md     -- same content as markdown

It runs rank_all.py under several WEIGHT LENSES (value / quality / growth / deep-value /
smart-money) by shelling out, snapshotting each final_ranking.csv, then restores the
canonical default ranking at the end. No external dependencies (pure HTML/CSS).

Run AFTER the pipeline (stages 2-5) completes:
  ./.venv/bin/python build_report.py
"""
import subprocess, shutil, sys, html
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PY = str(ROOT / ".venv/bin/python")
RANK = str(Path(__file__).resolve().parent / "rank_all.py")
FINAL = DATA / "final_ranking.csv"

LENSES = {
    "Balanced (default)": None,
    "Deep value":         "valuation=4,price_setup=2.5,growth=0.5,momentum=0",
    "Quality compounder": "quality=4,growth=2.5,smart_money=2,valuation=1",
    "Smart-money":        "smart_money=4,quality=2,valuation=1.5",
    "Growth":             "growth=4,quality=2,momentum=1.5,valuation=1",
}

def run_lens(weights):
    cmd = [PY, RANK, "--top", "9999"]
    if weights: cmd += ["--weights", weights]
    subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    return pd.read_csv(FINAL)

def fmt(x, n=1, pct=False):
    if pd.isna(x): return "—"
    try: return f"{float(x):.{n}f}{'%' if pct else ''}"
    except: return html.escape(str(x))

# ---------- table renderers ----------
def bar(v, vmax=100, w=90):
    try: px = max(2, int(float(v) / vmax * w))
    except: px = 2
    return (f'<div class="barwrap"><div class="bar" style="width:{px}px"></div>'
            f'<span>{fmt(v)}</span></div>')

def rows_html(df, cols, score_col="final_score"):
    out = []
    for _, r in df.iterrows():
        tds = []
        for c, label, kind in cols:
            v = r.get(c)
            if kind == "bar": tds.append(f"<td>{bar(v)}</td>")
            elif kind == "pct": tds.append(f"<td>{fmt(v,1,True)}</td>")
            elif kind == "txt": tds.append(f"<td class='l'>{html.escape(str(v))[:34]}</td>")
            else: tds.append(f"<td>{fmt(v)}</td>")
        out.append("<tr>" + "".join(tds) + "</tr>")
    return "\n".join(out)

def main():
    if not FINAL.exists():
        sys.exit("final_ranking.csv missing — run the pipeline (stages 2-5) first.")

    print("Running weight lenses…")
    lens_df = {}
    for name, w in LENSES.items():
        lens_df[name] = run_lens(w)
        print(f"  {name}: {len(lens_df[name])} stocks")
    run_lens(None)  # restore canonical default
    base = lens_df["Balanced (default)"]

    n = len(base)
    deliv_cov = base["delivery_trend"].notna().mean() * 100 if "delivery_trend" in base else 0

    # sector analysis
    sec = (base.groupby("sector")
           .agg(n=("ticker","size"), avg_score=("final_score","mean"),
                top=("ticker","first"))
           .sort_values("avg_score", ascending=False)) if "sector" in base else pd.DataFrame()
    sec = sec[sec["n"] >= 3]

    # value-trap watchlist: deeply down + weak quality + (falling delivery if available)
    vt = base.copy()
    if {"pct_below_52w_high","g_quality"}.issubset(vt.columns):
        vt = vt[(vt["pct_below_52w_high"] >= 40) & (vt["g_quality"] <= 35)]
        if "delivery_trend" in vt: vt = vt.sort_values("delivery_trend")
        vt = vt.head(15)

    # provided-50 standalone (if scored earlier)
    prov = DATA / "provided_50_scored.csv"
    prov_df = pd.read_csv(prov).head(15) if prov.exists() else pd.DataFrame()

    # ---------------- HTML ----------------
    css = """
    body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial;margin:0;background:#0f1419;color:#e6e6e6}
    .wrap{max-width:1100px;margin:0 auto;padding:28px}
    h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:34px 0 10px;border-bottom:1px solid #2a3340;padding-bottom:6px}
    .sub{color:#8a97a6;margin-bottom:18px} .pill{display:inline-block;background:#1b2430;border:1px solid #2a3340;border-radius:20px;padding:3px 11px;margin:2px;font-size:12px;color:#bcd}
    table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
    th,td{padding:6px 8px;text-align:right;border-bottom:1px solid #1f2730} th{color:#8a97a6;font-weight:600;text-align:right}
    td.l,th.l{text-align:left} tr:hover td{background:#161d27}
    .barwrap{display:flex;align-items:center;justify-content:flex-end;gap:6px}
    .bar{height:9px;background:linear-gradient(90deg,#2d7;#19c);background:#1f9d6b;border-radius:3px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
    .card{background:#141b24;border:1px solid #222c38;border-radius:10px;padding:14px 16px}
    .big{font-size:30px;font-weight:700;color:#3ddc91} .note{color:#c8a24a}
    code{background:#1b2430;padding:1px 5px;border-radius:4px}
    """
    def table(df, cols, score_col="final_score"):
        head = "".join(f"<th class='{'l' if k=='txt' else ''}'>{html.escape(lbl)}</th>" for _,lbl,k in cols)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows_html(df,cols,score_col)}</tbody></table>"

    main_cols = [("rank","#","num"),("ticker","Ticker","txt"),("sector","Sector","txt"),
                 ("mcap_cr","MCap Cr","num"),("pct_below_52w_high","↓52wH%","pct"),
                 ("trailingPE","PE","num"),("roe_pct","ROE%","num"),
                 ("institutional_pct","Inst%","num"),("delivery_trend","DlvTrd","num"),
                 ("upside_pct","Upside%","num"),("final_score","Score","bar")]
    lens_cols = [("rank","#","num"),("ticker","Ticker","txt"),("sector","Sector","txt"),
                 ("pct_below_52w_high","↓52wH%","pct"),("trailingPE","PE","num"),
                 ("roe_pct","ROE%","num"),("final_score","Score","bar")]

    lens_sections = ""
    for name, df in lens_df.items():
        if name == "Balanced (default)": continue
        lens_sections += f"<h3>{html.escape(name)}</h3>" + table(df.head(10), lens_cols)

    sec_rows = ""
    if len(sec):
        for s, r in sec.head(14).iterrows():
            sec_rows += (f"<tr><td class='l'>{html.escape(str(s))}</td><td>{int(r['n'])}</td>"
                         f"<td>{bar(r['avg_score'])}</td><td class='l'>{html.escape(str(r['top']))}</td></tr>")

    vt_html = table(vt, [("ticker","Ticker","txt"),("sector","Sector","txt"),
                         ("pct_below_52w_high","↓52wH%","pct"),("roe_pct","ROE%","num"),
                         ("g_quality","QualSc","num"),("delivery_trend","DlvTrd","num"),
                         ("final_score","Score","num")]) if len(vt) else "<p>None flagged.</p>"

    prov_html = ""
    if len(prov_df):
        pc = [("rank","#","num"),("name","Name","txt"),("pe","PE","num"),("roce","ROCE%","num"),
              ("roe","ROE%","num"),("ret_6m","6m%","pct"),("final_score","Score","bar")]
        prov_html = ("<h2>Your 50 pre-screened names — standalone score</h2>"
                     "<p class='sub'>Ranked within that list only (percentile-rank model). See "
                     "<code>sources/provided-50-scoring.md</code>.</p>" + table(prov_df, pc))

    htmldoc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Indian Stocks — Beaten-Down but Fundamentally Strong</title><style>{css}</style></head>
<body><div class="wrap">
<h1>Indian Stocks — Beaten-Down but Fundamentally Strong</h1>
<div class="sub">Universe: all NSE EQ &ge; &#8377;1000 Cr &middot; generated from the 5-stage free-data pipeline &middot; personal/internal research</div>
<div class="grid">
  <div class="card"><div>Universe ranked</div><div class="big">{n}</div><div class="sub">stocks &ge; &#8377;1000 Cr</div></div>
  <div class="card"><div>Delivery-data coverage</div><div class="big">{deliv_cov:.0f}%</div><div class="sub">NSE delivery % pulled (smart-money proxy)</div></div>
</div>
<p class="note">⚠ Research tool, not advice. Scores are relative within this universe. Verify finalists on Screener.in (multi-year consistency, debt, promoter pledge, FII/DII QoQ) before any decision.</p>

<h2>Top 30 — Balanced model</h2>
<p class="sub">7 weighted factor groups: quality 2.5 · smart-money 2.0 · valuation 2.0 · growth 1.75 · price-setup 1.5 · analyst 1.25 · momentum 0.75. Full rationale in <code>sources/factor-model.md</code>.</p>
{table(base.head(30), main_cols)}

<h2>By investor lens (top 10 each)</h2>
<p class="sub">Same factors, re-weighted. Tune any combination via <code>rank_all.py --weights "..."</code>.</p>
{lens_sections}

<h2>Where the value is — sectors (avg score, ≥3 names)</h2>
<table><thead><tr><th class='l'>Sector</th><th># </th><th>Avg score</th><th class='l'>Top name</th></tr></thead><tbody>{sec_rows}</tbody></table>

<h2>⚠ Value-trap watchlist (cheap for a reason)</h2>
<p class="sub">Deeply down (≥40% off high) + weak quality score{' + sorted by worst delivery trend' if 'delivery_trend' in base else ''}. Avoid unless the story justifies it.</p>
{vt_html}

{prov_html}

<h2>Method &amp; honest limitations</h2>
<ul>
<li><b>Pipeline:</b> (1) universe by market cap → (2) fundamentals → (3) delivery/ownership → (4) promoter/institutional % → (5) weighted rank. Scripts in <code>scripts/</code>, docs in <code>sources/</code>.</li>
<li><b>FII/DII entering/exiting:</b> true quarterly change isn't free+legit programmatically — proxied by delivery-trend + institutional level/breadth. Use Screener.in or a paid feed for exact QoQ.</li>
<li><b>Data:</b> yfinance fundamentals can be stale/misclassified (e.g. no-promoter names); single-quarter growth is noisy; ownership is a snapshot. Cross-check finalists.</li>
<li><b>Coverage:</b> NSE main-board EQ only; ~97 names returned no market cap and a handful of BSE-only names are excluded vs Screener's full list.</li>
</ul>
<div class="sub">Generated by build_report.py · data in data/final_ranking.csv</div>
</div></body></html>"""
    (ROOT / "REPORT.html").write_text(htmldoc)

    # ---------------- Markdown ----------------
    def md_table(df, cols):
        hdr = "| " + " | ".join(l for _,l,_ in cols) + " |"
        sep = "|" + "|".join("---" for _ in cols) + "|"
        lines = [hdr, sep]
        for _, r in df.iterrows():
            lines.append("| " + " | ".join(fmt(r.get(c)) if k!="txt" else html.unescape(str(r.get(c)))[:30]
                                            for c,_,k in cols) + " |")
        return "\n".join(lines)
    md = [f"# Indian Stocks — Beaten-Down but Fundamentally Strong\n",
          f"Universe: **{n}** NSE stocks ≥₹1000 Cr · delivery coverage {deliv_cov:.0f}% · personal/internal research.\n",
          "> Research tool, not advice. Scores relative within universe; verify finalists on Screener.in.\n",
          "## Top 30 — Balanced model", md_table(base.head(30), main_cols), ""]
    for name, df in lens_df.items():
        if name == "Balanced (default)": continue
        md += [f"## Lens: {name} (top 10)", md_table(df.head(10), lens_cols), ""]
    if len(sec):
        md += ["## Sectors by avg score (≥3 names)",
               "| Sector | # | Avg score | Top |", "|---|---|---|---|"]
        for s, r in sec.head(14).iterrows():
            md.append(f"| {s} | {int(r['n'])} | {fmt(r['avg_score'])} | {r['top']} |")
        md.append("")
    if len(vt):
        md += ["## Value-trap watchlist",
               md_table(vt, [("ticker","Ticker","txt"),("sector","Sector","txt"),
                             ("pct_below_52w_high","↓52wH%","num"),("roe_pct","ROE%","num"),
                             ("final_score","Score","num")]), ""]
    md += ["## Method & limitations",
           "- Pipeline: universe→fundamentals→ownership→shareholding→weighted rank (scripts/, sources/).",
           "- FII/DII QoQ proxied by delivery-trend + institutional breadth (true QoQ needs Screener/paid).",
           "- yfinance fundamentals can be stale; single-qtr growth noisy; ownership is a snapshot.",
           "- NSE EQ only; ~97 no-mcap + BSE-only names excluded vs Screener's 1530.\n"]
    (ROOT / "REPORT.md").write_text("\n".join(md))

    print(f"\nWrote {ROOT/'REPORT.html'} and {ROOT/'REPORT.md'}")
    print(f"Universe {n} | delivery coverage {deliv_cov:.0f}%")

if __name__ == "__main__":
    main()
