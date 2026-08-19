"""
weekly_update.py — refresh the undervalued-stocks ranking and email MAJOR
week-over-week changes. This is the entry point the Saturday scheduler runs.

What it does
  0. Archive last run's fundamentals without deleting the live cache.
  1. screen_universe.py     — batched prices + cached fundamentals
  2. ownership_layer.py     — whole-market delivery files + deals + FPI regime
  3. shareholding_layer.py  — reuse already-fetched promoter / institutional %
  4. enrich_missing_data.py — target sparse stocks through NSE, BSE and Yahoo fallbacks
  5. shareholding_layer.py  — consolidate any recovered ownership percentages
  6. rank_all.py            — coverage-adjusted ranking -> data/final_ranking.csv
  7. build_rank_tracker.py — compare working vs staged vs last-pushed ranks/scores
  8. rebuild DASHBOARD.html + site/dashboard-data.json and validate the site payload
  9. diff vs data/baseline_ranking.csv (last completed run) -> major changes
 10. email the report via Resend                     (unless --no-email)

Snapshots accumulate in data/history/. The HTML report is also saved to reports/.

Usage
  ./.venv/bin/python scripts/weekly_update.py                 # full weekly run
  ./.venv/bin/python scripts/weekly_update.py --no-email      # run + save report, don't send
  ./.venv/bin/python scripts/weekly_update.py --full-fundamentals  # force slow cold refresh
  ./.venv/bin/python scripts/weekly_update.py --skip-enrichment    # diagnostic fallback
  ./.venv/bin/python scripts/weekly_update.py --diff-only     # skip fetch; diff/report current files
  ./.venv/bin/python scripts/weekly_update.py --diff-only --no-email   # offline dry run
Legal: personal/internal research use only.
"""
import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff_rankings as D          # noqa: E402
import notify_email                # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
DATA = BASE / "data"
HIST = DATA / "history"
REPORTS = BASE / "reports"
PY = str(BASE / ".venv" / "bin" / "python")

RANKING = DATA / "final_ranking.csv"        # live latest output
BASELINE = DATA / "baseline_ranking.csv"    # last completed run (diff target)
RAW = DATA / "raw_fundamentals.csv"
SCREEN = DATA / "screen_results.csv"
FPI = DATA / "nsdl_fpi_latest.csv"


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def today():
    return dt.date.today().isoformat()


def run_stage(label, script, *args):
    log(f"STAGE {label}: {script} {' '.join(args)}".rstrip())
    t0 = dt.datetime.now()
    r = subprocess.run([PY, str(SCRIPTS / script), *args], cwd=str(SCRIPTS))
    mins = (dt.datetime.now() - t0).total_seconds() / 60
    if r.returncode != 0:
        log(f"  ! {script} exited {r.returncode} after {mins:.1f} min")
    else:
        log(f"  ✓ {label} done in {mins:.1f} min")
    return r.returncode == 0


def refresh(full_fundamentals=False, skip_enrichment=False, enrichment_target_coverage=75):
    """Archive inputs, run data stages, and stop on the first failure."""
    HIST.mkdir(parents=True, exist_ok=True)
    if RAW.exists():
        shutil.copy(str(RAW), str(HIST / f"raw_fundamentals_{today()}.csv"))
        log("archived raw_fundamentals.csv; live cache retained")
    if SCREEN.exists():
        SCREEN.unlink()
    screen_args = ["--full-fundamentals"] if full_fundamentals else []
    if skip_enrichment:
        stages = [
            ("1/4 prices + fundamentals", "screen_universe.py", screen_args),
            ("2/4 ownership", "ownership_layer.py", []),
            ("3/4 shareholding", "shareholding_layer.py", []),
            ("4/4 coverage-adjusted ranking", "rank_all.py", ["--top", "30"]),
        ]
    else:
        stages = [
            ("1/6 prices + fundamentals", "screen_universe.py", screen_args),
            ("2/6 ownership", "ownership_layer.py", []),
            ("3/6 shareholding", "shareholding_layer.py", []),
            ("4/6 targeted data-gap enrichment", "enrich_missing_data.py",
             ["--target-coverage", str(enrichment_target_coverage)]),
            ("5/6 enriched shareholding consolidation", "shareholding_layer.py", []),
            ("6/6 coverage-adjusted ranking", "rank_all.py", ["--top", "30"]),
        ]
    for label, script, args in stages:
        if not run_stage(label, script, *args):
            log("ERROR: refresh aborted; baseline, report, and dashboards were not promoted")
            return False
    return True


def read_regime():
    """Parse market-level FII/MF/Debt net flows from the saved NSDL FPI dump."""
    if not FPI.exists():
        return None
    try:
        df = pd.read_csv(FPI)

        def net(mask):
            s = pd.to_numeric(df[mask]["NET_INVESTMENT_RS_CR"], errors="coerce")
            return round(float(s.sum()), 1) if len(s) else 0.0

        return {
            "report_date": df["REPORT_DATE"].iloc[0] if "REPORT_DATE" in df else "?",
            "fii_equity_net_cr": net((df["ASSET_CLASS"] == "Equity") &
                                     (df["INVESTMENT_ROUTE"] == "Sub-total")),
            "mf_equity_net_cr": net((df["ASSET_CLASS"] == "Mutual Funds") &
                                    (df["INVESTMENT_ROUTE"] == "Equity schemes")),
            "debt_net_cr": net(df["ASSET_CLASS"].astype(str).str.startswith("Debt") &
                               (df["INVESTMENT_ROUTE"] == "Sub-total")),
        }
    except Exception as e:
        log(f"  (could not parse market regime: {e})")
        return None


def diff_and_report(send_email=True):
    if not RANKING.exists():
        log("ERROR: no final_ranking.csv — pipeline produced no output, nothing to report")
        return False
    new = D.load(RANKING)
    if len(new) < 500:
        log(f"WARNING: ranking has only {len(new)} rows — the fetch likely failed/was throttled")

    HIST.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(RANKING), str(HIST / f"final_ranking_{today()}.csv"))

    prev, prev_date = None, None
    if BASELINE.exists():
        prev = D.load(BASELINE)
        prev_date = dt.date.fromtimestamp(BASELINE.stat().st_mtime).isoformat()

    regime = read_regime()
    top_now = new.head(10)

    if prev is None:
        log("first run — establishing baseline (no diff this week)")
        changes = None
    else:
        changes = D.compute(prev, new)
        counts = ", ".join(f"{t}={c}" for t, c in changes["summary"].items()) or "none"
        log(f"major changes: {counts}")

    subject, html, text = D.render_email(
        changes, run_date=today(), prev_date=prev_date,
        regime=regime, top_now=top_now, universe=len(new))

    REPORTS.mkdir(exist_ok=True)
    preview = REPORTS / f"weekly_{today()}.html"
    preview.write_text(html)
    log(f"report saved -> {preview}")

    if send_email:
        try:
            res = notify_email.send(subject, html, text)
            log(f"email sent via Resend: {res}")
        except Exception as e:
            log(f"! email NOT sent: {e}")
            log("  (report is still saved locally; set RESEND_API_KEY/EMAIL_TO in .env)")

    shutil.copy(str(RANKING), str(BASELINE))   # promote this run to next week's baseline
    log(f"baseline updated -> {BASELINE}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff-only", action="store_true",
                    help="skip the data refresh; just diff + report the current files")
    ap.add_argument("--no-email", action="store_true", help="don't send the email")
    ap.add_argument("--no-dashboard", action="store_true", help="skip the DASHBOARD.html rebuild")
    ap.add_argument("--full-fundamentals", action="store_true",
                    help="force the slower per-stock fundamentals refresh")
    ap.add_argument("--skip-enrichment", action="store_true",
                    help="skip the targeted NSE/BSE/Yahoo missing-data recovery stage")
    ap.add_argument("--enrichment-target-coverage", type=float, default=75,
                    help="target stocks below this weighted data-coverage percentage")
    args = ap.parse_args()

    log("=== weekly_update start ===")
    if not args.diff_only:
        if not refresh(full_fundamentals=args.full_fundamentals,
                       skip_enrichment=args.skip_enrichment,
                       enrichment_target_coverage=args.enrichment_target_coverage):
            return 1
    else:
        log("--diff-only: skipping data refresh")

    if not run_stage("Git rank tracker", "build_rank_tracker.py"):
        log("ERROR: Git rank tracker failed; dashboards were not rebuilt")
        return 1

    if not args.no_dashboard:
        try:
            log("rebuilding DASHBOARD.html …")
            subprocess.run([PY, str(SCRIPTS / "build_dashboard.py")],
                           cwd=str(SCRIPTS), timeout=600, check=True)
            log("validating the JSON-backed site payload …")
            subprocess.run([PY, str(SCRIPTS / "update_site.py"), "--no-rebuild"],
                           cwd=str(SCRIPTS), timeout=600, check=True)
        except Exception as e:
            log(f"ERROR: dashboard/site publish failed: {e}")
            return 1

    ok = diff_and_report(send_email=not args.no_email)
    if not ok:
        return 1

    log("=== weekly_update done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
