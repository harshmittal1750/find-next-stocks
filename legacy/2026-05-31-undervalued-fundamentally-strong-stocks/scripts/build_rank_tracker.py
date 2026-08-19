#!/usr/bin/env python3
"""Build stock rank/score movements from the working tree, Git index and upstream.

Snapshots compared
  current  data/final_ranking.csv in the working tree
  staged   the same file in Git's index (what the next commit would contain)
  pushed   the file on the branch's upstream, falling back to HEAD if needed

Rank deltas are ``old rank - new rank`` so a positive value means the stock moved
up the list. Score deltas are ``new score - old score`` so a positive value means
the score improved. Missing/unranked transitions are labelled rather than coerced
to a numeric movement.

Outputs
  data/rank_tracker.csv
  data/rank_tracker_meta.json

This script is read-only with respect to Git. It does not fetch, add, commit or push.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
RANKING = BASE / "data" / "final_ranking.csv"
OUT = BASE / "data" / "rank_tracker.csv"
META = BASE / "data" / "rank_tracker_meta.json"

OUTPUT_FIELDS = [
    "ticker", "shortName", "sector",
    "current_rank", "current_score", "current_score_status",
    "staged_rank", "staged_score", "staged_score_status",
    "pushed_rank", "pushed_score", "pushed_score_status",
    "rank_vs_staged", "score_vs_staged", "movement_vs_staged",
    "staged_rank_vs_pushed", "staged_score_vs_pushed", "staged_movement_vs_pushed",
    "rank_vs_pushed", "score_vs_pushed", "movement_vs_pushed",
]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run Git without a shell so file/ref names cannot be interpreted by it."""
    return subprocess.run(
        ["git", *args], cwd=BASE, text=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
    )


def git_text(*args: str) -> str:
    return git(*args).stdout.decode("utf-8", errors="replace").strip()


def optional_git_text(*args: str) -> str | None:
    result = git(*args, check=False)
    if result.returncode:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def parse_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def clean_number(value, digits=2):
    value = parse_number(value)
    if value is None:
        return ""
    rounded = round(value, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def load_csv_text(text: str, label: str) -> dict[str, dict]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"ticker", "rank", "final_score"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(sorted(missing))}")
    rows = {}
    for row in reader:
        ticker = (row.get("ticker") or "").strip()
        if not ticker:
            continue
        if ticker in rows:
            raise ValueError(f"{label} contains duplicate ticker {ticker}")
        rows[ticker] = row
    if not rows:
        raise ValueError(f"{label} contains no stock rows")
    return rows


def load_file(path: Path, label: str) -> dict[str, dict]:
    return load_csv_text(path.read_text(encoding="utf-8"), label)


def load_git_snapshot(spec: str, label: str) -> dict[str, dict] | None:
    result = git("show", spec, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        print(f"WARNING: {label} snapshot unavailable ({message or spec})")
        return None
    return load_csv_text(result.stdout.decode("utf-8", errors="replace"), label)


def snapshot_values(rows: dict[str, dict] | None, ticker: str):
    if rows is None or ticker not in rows:
        return None, None, ""
    row = rows[ticker]
    return (
        parse_number(row.get("rank")),
        parse_number(row.get("final_score")),
        (row.get("score_status") or "").strip(),
    )


def movement(old_rows, new_rows, ticker: str):
    """Return signed rank/score changes and a safe transition label."""
    old_exists = old_rows is not None and ticker in old_rows
    new_exists = new_rows is not None and ticker in new_rows
    old_rank, old_score, _ = snapshot_values(old_rows, ticker)
    new_rank, new_score, _ = snapshot_values(new_rows, ticker)

    rank_delta = old_rank - new_rank if old_rank is not None and new_rank is not None else None
    score_delta = new_score - old_score if old_score is not None and new_score is not None else None

    if not old_exists and new_exists:
        label = "added"
    elif old_exists and not new_exists:
        label = "removed"
    elif old_rank is None and new_rank is not None:
        label = "newly ranked"
    elif old_rank is not None and new_rank is None:
        label = "became unranked"
    elif old_rank is None and new_rank is None:
        label = "unranked"
    elif rank_delta > 0:
        label = "up"
    elif rank_delta < 0:
        label = "down"
    else:
        label = "unchanged"
    return rank_delta, score_delta, label


def ordered_tickers(current, staged, pushed):
    """Keep current CSV order, then append stocks removed from the current file."""
    order = list(current)
    seen = set(order)
    for snapshot in (staged or {}, pushed or {}):
        for ticker in snapshot:
            if ticker not in seen:
                order.append(ticker)
                seen.add(ticker)
    return order


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def comparison_counts(rows: list[dict], field: str) -> dict[str, int]:
    counts = {}
    for row in rows:
        label = row[field]
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def main():
    if not RANKING.exists():
        raise SystemExit(f"ERROR: current ranking not found: {RANKING}")

    repo_root_text = git_text("rev-parse", "--show-toplevel")
    repo_root = Path(repo_root_text)
    try:
        rel = RANKING.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise SystemExit(f"ERROR: ranking is outside Git repository {repo_root}") from exc

    current = load_file(RANKING, "working ranking")
    staged_spec = f":{rel}"
    staged = load_git_snapshot(staged_spec, "staged ranking")

    upstream = optional_git_text("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    pushed_ref = upstream or "HEAD"
    pushed = load_git_snapshot(f"{pushed_ref}:{rel}", "pushed ranking")

    output = []
    for ticker in ordered_tickers(current, staged, pushed):
        cur_rank, cur_score, cur_status = snapshot_values(current, ticker)
        stage_rank, stage_score, stage_status = snapshot_values(staged, ticker)
        push_rank, push_score, push_status = snapshot_values(pushed, ticker)
        r_stage, s_stage, m_stage = movement(staged, current, ticker)
        r_push_stage, s_push_stage, m_push_stage = movement(pushed, staged, ticker)
        r_push, s_push, m_push = movement(pushed, current, ticker)
        identity = current.get(ticker) or (staged or {}).get(ticker) or (pushed or {}).get(ticker) or {}
        output.append({
            "ticker": ticker,
            "shortName": identity.get("shortName", ""),
            "sector": identity.get("sector", ""),
            "current_rank": clean_number(cur_rank, 0),
            "current_score": clean_number(cur_score),
            "current_score_status": cur_status,
            "staged_rank": clean_number(stage_rank, 0),
            "staged_score": clean_number(stage_score),
            "staged_score_status": stage_status,
            "pushed_rank": clean_number(push_rank, 0),
            "pushed_score": clean_number(push_score),
            "pushed_score_status": push_status,
            "rank_vs_staged": clean_number(r_stage, 0),
            "score_vs_staged": clean_number(s_stage),
            "movement_vs_staged": m_stage,
            "staged_rank_vs_pushed": clean_number(r_push_stage, 0),
            "staged_score_vs_pushed": clean_number(s_push_stage),
            "staged_movement_vs_pushed": m_push_stage,
            "rank_vs_pushed": clean_number(r_push, 0),
            "score_vs_pushed": clean_number(s_push),
            "movement_vs_pushed": m_push,
        })

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS)
    writer.writeheader()
    writer.writerows(output)
    atomic_write(OUT, buffer.getvalue())

    pushed_commit = optional_git_text("rev-parse", pushed_ref)
    head_commit = optional_git_text("rev-parse", "HEAD")
    staged_blob = optional_git_text("rev-parse", staged_spec)
    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "repository": str(repo_root),
        "ranking_path": rel,
        "semantics": {
            "rank_delta": "old_rank - new_rank; positive means moved up",
            "score_delta": "new_score - old_score; positive means score improved",
        },
        "working": {"rows": len(current), "path": str(RANKING)},
        "staged": {"available": staged is not None, "rows": len(staged or {}), "blob": staged_blob},
        "pushed": {
            "available": pushed is not None,
            "rows": len(pushed or {}),
            "ref": pushed_ref,
            "commit": pushed_commit,
            "upstream_configured": bool(upstream),
        },
        "head_commit": head_commit,
        "comparisons": {
            "working_vs_staged": comparison_counts(output, "movement_vs_staged"),
            "staged_vs_pushed": comparison_counts(output, "staged_movement_vs_pushed"),
            "working_vs_pushed": comparison_counts(output, "movement_vs_pushed"),
        },
    }
    atomic_write(META, json.dumps(meta, indent=2) + "\n")

    print(f"Wrote {OUT} ({len(output):,} stocks)")
    print(f"Staged snapshot: {len(staged or {}):,} stocks · blob {(staged_blob or 'unavailable')[:10]}")
    print(f"Pushed snapshot: {len(pushed or {}):,} stocks · {pushed_ref} @ {(pushed_commit or 'unavailable')[:10]}")
    print("Working vs staged:", comparison_counts(output, "movement_vs_staged"))
    print("Working vs pushed:", comparison_counts(output, "movement_vs_pushed"))


if __name__ == "__main__":
    main()
