# Weekly auto-refresh + email digest

Re-runs the full stock-ranking pipeline **every Saturday at 09:00 IST**, detects the
major week-over-week changes, and emails you a digest. Runs locally on this Mac via
`launchd` (chosen over cron because launchd reliably runs a *missed* job when the
laptop next wakes; cron silently skips it).

## What runs each week
`scripts/run_weekly.sh` → `scripts/weekly_update.py`, which:
0. Archives `data/raw_fundamentals.csv` → `data/history/` while retaining the live cache.
1. `screen_universe.py`    — batched/incremental price history; fundamentals refresh every
   30 days by default instead of every week.
2. `ownership_layer.py`    — whole-market NSE delivery files + bulk/block deals + NSDL FPI.
3. `shareholding_layer.py` — reuses ownership already returned by the fundamentals fetch;
   the last institution count is retained in the quarterly cache.
4. `enrich_missing_data.py` — targets sparse/core-gap stocks through free NSE, BSE and
   Yahoo statement fallbacks; fills blanks only and records field-level provenance.
5. `shareholding_layer.py` — consolidates any recovered ownership percentages.
6. `rank_all.py` — coverage-adjusted score; insufficient-evidence stocks stay visible
   but are unranked instead of receiving a neutral-filled rank.
7. `build_rank_tracker.py` — compares every stock's rank and score across the current
   working CSV, Git's staged index, and the upstream branch's last pushed snapshot.
8. Rebuilds `DASHBOARD.html` + `site/dashboard-data.json`; both dashboard entry points
   fetch the same versioned JSON payload, and `update_site.py` validates its schema.
9. Diffs vs `data/baseline_ranking.csv` (last completed run) → major changes.
10. Emails the digest via Resend (report is also saved to `reports/weekly_<date>.html`).

The first optimized run seeds about one year of local price history. Subsequent weekly
runs are expected to take roughly **2–6 minutes**; a forced cold fundamentals refresh is
slower. Rebuildable downloads live in the gitignored `data/cache/`. Enrichment changes
are audited in `data/enrichment_report.csv` and summarized in `data/enrichment_summary.json`. Snapshots accumulate
in `data/history/`; logs in `logs/`.

## "Major change" thresholds (tune in `scripts/diff_rankings.py`)
| Section | Fires when |
|---|---|
| New entrants / drop-outs of Top 25 | rank crosses 25 |
| Big price moves | \|Δprice\| ≥ 15% week-over-week |
| Big rank gainers / losers | \|Δrank\| ≥ 25 places |
| Score jumps | \|Δfinal_score\| ≥ 5 pts |
| Drawdown deepened | ≥ 10pp further below 52w high |
| Delivery-trend flips | accumulation ↔ distribution (\|trend\| ≥ 5) |
| Universe added / removed | ticker enters/leaves the ≥₹1000cr set |

## One-time setup: email (Resend)
1. Free account at <https://resend.com> → **API Keys → Create** (key starts with `re_`).
2. `cp .env.example .env` and fill in `RESEND_API_KEY` + `EMAIL_TO`.
   Free tier sends from `onboarding@resend.dev` to your signup address — no domain setup.
3. Test it: `./.venv/bin/python scripts/notify_email.py --test`

`.env` is gitignored — your key is never committed.

## Manual commands
```bash
cd research/2026-05-31-undervalued-fundamentally-strong-stocks

# Full run now (fetch + diff + email + dashboard) — same as the scheduled job:
bash scripts/run_weekly.sh

# Offline dry run (no fetch, no send) — just re-diff current files & save the HTML report:
./.venv/bin/python scripts/weekly_update.py --diff-only --no-email

# Run the fetch but don't email:
./.venv/bin/python scripts/weekly_update.py --no-email

# Force the slower all-stock fundamentals refresh now instead of waiting 30 days:
./.venv/bin/python scripts/weekly_update.py --full-fundamentals --no-email

# Re-run only the targeted missing-data recovery, then consolidate/rank/rebuild manually:
./.venv/bin/python scripts/enrich_missing_data.py

# Rebuild only the Git rank tracker and dashboards (no market-data fetch):
./.venv/bin/python scripts/build_rank_tracker.py
./.venv/bin/python scripts/build_dashboard.py
./.venv/bin/python scripts/update_site.py --no-rebuild

# Diagnostic escape hatch: run the weekly pipeline without provider enrichment:
./.venv/bin/python scripts/weekly_update.py --skip-enrichment --no-email
```

## Manage the schedule
```bash
PLIST=~/Library/LaunchAgents/com.harshmittal.stockweekly.plist
launchctl print gui/$(id -u)/com.harshmittal.stockweekly   # status + next fire
launchctl kickstart -k gui/$(id -u)/com.harshmittal.stockweekly   # run it right now
launchctl bootout gui/$(id -u) "$PLIST"; rm "$PLIST"        # uninstall

# After editing scripts/com.harshmittal.stockweekly.plist, reinstall:
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
cp scripts/com.harshmittal.stockweekly.plist "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
```

To change the day/time, edit `StartCalendarInterval` in the plist (Weekday: 0/7=Sun,
1=Mon … 6=Sat) and reinstall with the commands above.
