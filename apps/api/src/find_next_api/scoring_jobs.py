from __future__ import annotations

from typing import Any, Protocol

from find_next_pipeline.rank_history import compare_runs
from find_next_pipeline.scoring import MODEL_VERSION, WEIGHTS, score_universe

from find_next_api.repository import DashboardRepository

GROUP_FACTOR_KEYS = [f"g_{group}" for group in WEIGHTS]


class RankingWarehouse(Protocol):
    def latest_ranking_run(self) -> dict[str, dict[str, Any]] | None: ...

    def write_ranking_run(
        self, *, model_version: str, parameters: dict[str, Any], rows: list[dict[str, Any]]
    ) -> Any: ...


def run_scoring(repository: DashboardRepository, warehouse: RankingWarehouse) -> dict[str, Any]:
    """Score the current merged universe and persist it as a new ranking_runs entry.

    Reads the same CSV-archived + live-overlaid stock list the dashboard itself
    assembles, so a fresh score reflects whatever refresh just landed in Postgres —
    without needing its own copy of the merge logic.
    """
    stocks = repository.load()["stocks"]
    scored = score_universe(stocks)
    previous = warehouse.latest_ranking_run()
    movement = compare_runs(previous, scored)

    rows = []
    for item in scored:
        ticker = item["ticker"]
        factors = {key: item.get(key) for key in GROUP_FACTOR_KEYS}
        factors["model_score"] = item.get("model_score")
        # scoring computes these every run and nothing stored them, so the dashboard
        # read them from the archived CSV instead of the run that just produced them.
        factors["quality_cov"] = item.get("quality_cov")
        factors["valuation_cov"] = item.get("valuation_cov")
        factors.update(movement[ticker])
        rows.append(
            {
                "ticker": ticker,
                "rank": item["rank"],
                "score": item["final_score"],
                "score_status": item["score_status"],
                "data_coverage": item["data_cov"],
                "factors": factors,
            }
        )

    run_id = warehouse.write_ranking_run(
        model_version=MODEL_VERSION, parameters=WEIGHTS, rows=rows
    )
    ranked = sum(1 for row in rows if row["rank"] is not None)
    return {
        "run_id": str(run_id),
        "universe": len(rows),
        "ranked": ranked,
        "unranked": len(rows) - ranked,
    }
