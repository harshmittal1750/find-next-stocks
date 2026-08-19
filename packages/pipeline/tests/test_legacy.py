from find_next_pipeline.legacy import clean_legacy_stock


def test_legacy_dashboard_never_displays_107_percent_promoter() -> None:
    cleaned = clean_legacy_stock(
        {"ticker": "GALLANTT", "promoter_pct": 107.481, "institutional_pct": 0.1}
    )

    assert cleaned["promoter_pct"] is None
    assert cleaned["data_quality"]["status"] == "review"
    assert cleaned["data_quality"]["issues"][0]["raw_value"] == 107.481


def test_legacy_valid_ownership_is_preserved() -> None:
    cleaned = clean_legacy_stock(
        {"ticker": "GALLANTT", "promoter_pct": 70.0, "institutional_pct": 0.1}
    )

    assert cleaned["promoter_pct"] == 70.0
    assert cleaned["institutional_pct"] == 0.1
    assert cleaned["data_quality"]["status"] == "valid"


def test_impossible_provider_fraction_is_flagged_without_overwriting_canonical_value() -> None:
    cleaned = clean_legacy_stock(
        {
            "ticker": "GALLANTT",
            "promoter_pct": 70.0,
            "heldPercentInsiders": 1.07481,
        }
    )

    assert cleaned["promoter_pct"] == 70.0
    assert cleaned["heldPercentInsiders"] == 1.07481
    assert cleaned["data_quality"]["status"] == "review"
    assert cleaned["data_quality"]["issues"][0]["field"] == "heldPercentInsiders"
