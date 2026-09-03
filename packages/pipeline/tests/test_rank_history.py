from find_next_pipeline.rank_history import compare_runs


def test_first_run_ever_has_no_previous_to_compare_against() -> None:
    """No prior ranking_runs row exists at all — every ticker is new to the history."""
    current = [{"ticker": "AAA", "rank": 1, "final_score": 80.0}]

    movement = compare_runs(None, current)

    assert movement["AAA"]["rank_vs_staged"] is None
    assert movement["AAA"]["score_vs_staged"] is None
    assert movement["AAA"]["movement_vs_staged"] == "added"


def test_rank_delta_is_old_minus_new_so_moving_up_is_positive() -> None:
    previous = {"AAA": {"rank": 10, "score": 50.0}}
    current = [{"ticker": "AAA", "rank": 4, "final_score": 65.0}]

    movement = compare_runs(previous, current)

    assert movement["AAA"]["rank_vs_staged"] == 6
    assert movement["AAA"]["score_vs_staged"] == 15.0
    assert movement["AAA"]["movement_vs_staged"] == "up"


def test_moving_down_the_list_is_negative() -> None:
    previous = {"AAA": {"rank": 3, "score": 70.0}}
    current = [{"ticker": "AAA", "rank": 9, "final_score": 60.0}]

    movement = compare_runs(previous, current)

    assert movement["AAA"]["rank_vs_staged"] == -6
    assert movement["AAA"]["movement_vs_staged"] == "down"


def test_newly_ranked_and_became_unranked_transitions() -> None:
    previous = {
        "GAINED_DATA": {"rank": None, "score": None},
        "LOST_DATA": {"rank": 5, "score": 70.0},
    }
    current = [
        {"ticker": "GAINED_DATA", "rank": 20, "final_score": 55.0},
        {"ticker": "LOST_DATA", "rank": None, "final_score": None},
        {"ticker": "BRAND_NEW", "rank": 1, "final_score": 90.0},
    ]

    movement = compare_runs(previous, current)

    assert movement["GAINED_DATA"]["movement_vs_staged"] == "newly ranked"
    assert movement["LOST_DATA"]["movement_vs_staged"] == "became unranked"
    assert movement["BRAND_NEW"]["movement_vs_staged"] == "added"


def test_unchanged_rank_is_labeled_unchanged() -> None:
    previous = {"AAA": {"rank": 5, "score": 60.0}}
    current = [{"ticker": "AAA", "rank": 5, "final_score": 60.0}]

    movement = compare_runs(previous, current)

    assert movement["AAA"]["movement_vs_staged"] == "unchanged"
    assert movement["AAA"]["rank_vs_staged"] == 0
    assert movement["AAA"]["score_vs_staged"] == 0.0
