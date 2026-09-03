from __future__ import annotations

from typing import Any, TypedDict


class Movement(TypedDict):
    rank_vs_staged: int | None
    score_vs_staged: float | None
    movement_vs_staged: str


def _movement_label(
    old_rank: int | None, new_rank: int | None, old_exists: bool, new_exists: bool
) -> str:
    if not old_exists and new_exists:
        return "added"
    if old_exists and not new_exists:
        return "removed"
    if old_rank is None and new_rank is not None:
        return "newly ranked"
    if old_rank is not None and new_rank is None:
        return "became unranked"
    if old_rank is None and new_rank is None:
        return "unranked"
    if old_rank > new_rank:
        return "up"
    if old_rank < new_rank:
        return "down"
    return "unchanged"


def compare_runs(
    previous: dict[str, dict[str, Any]] | None, current: list[dict[str, Any]]
) -> dict[str, Movement]:
    """Per-ticker rank/score movement between the previous scoring run and this one.

    Replaces legacy build_rank_tracker.py's git working/staged/pushed diff — there is
    no git-committed ranking file anymore, so "the previous run" (the last row written
    to ranking_runs) takes the place of "staged". Field names (rank_vs_staged,
    score_vs_staged, movement_vs_staged) are kept as-is: the dashboard already reads
    them under this name, and the meaning — "how did this stock move since we last
    scored the universe" — is the same one users care about.

    ``previous`` is ``{ticker: {"rank": int|None, "score": float|None}}`` for the prior
    run, or None if this is the first run ever. Rank delta is old-minus-new, so a
    positive value means the stock moved up the list; score delta is new-minus-old.
    """
    result: dict[str, Movement] = {}
    for row in current:
        ticker = row["ticker"]
        new_rank = row.get("rank")
        new_score = row.get("final_score")
        old = (previous or {}).get(ticker)
        old_rank = old.get("rank") if old else None
        old_score = old.get("score") if old else None

        rank_delta = old_rank - new_rank if old_rank is not None and new_rank is not None else None
        score_delta = (
            new_score - old_score if old_score is not None and new_score is not None else None
        )
        result[ticker] = {
            "rank_vs_staged": rank_delta,
            "score_vs_staged": round(score_delta, 1) if score_delta is not None else None,
            # new_exists is always True: we're iterating current's own rows. A ticker
            # dropped from the universe entirely has no row here to label "removed" on.
            "movement_vs_staged": _movement_label(old_rank, new_rank, old is not None, True),
        }
    return result
