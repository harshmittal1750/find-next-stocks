from datetime import UTC, datetime, timedelta

from find_next_pipeline.models import MetricObservation
from find_next_pipeline.normalization import normalize_observation, select_canonical_metrics


def observation(**overrides):
    values = {
        "ticker": "GALLANTT",
        "field": "promoter_pct",
        "value": 0.70,
        "unit": "fraction",
        "provider": "yahoo",
        "observed_at": datetime(2026, 7, 22, tzinfo=UTC),
    }
    values.update(overrides)
    return MetricObservation(**values)


def test_impossible_fraction_is_rejected_not_reinterpreted() -> None:
    normalized = normalize_observation(observation(value=1.07481))

    assert normalized.value == 107.481
    assert normalized.unit == "percent"
    assert normalized.is_valid is False
    assert normalized.issues[0].code == "percent_out_of_range"


def test_valid_fraction_becomes_percent() -> None:
    normalized = normalize_observation(observation(value=0.70))

    assert normalized.value == 70.0
    assert normalized.is_valid is True


def test_official_source_beats_newer_aggregator() -> None:
    official = observation(
        value=70,
        unit="percent",
        provider="exchange_filing",
        observed_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    yahoo = observation(observed_at=datetime(2026, 7, 22, tzinfo=UTC))

    canonical, _ = select_canonical_metrics([yahoo, official])

    assert canonical[("GALLANTT", "promoter_pct")].provider == "exchange_filing"
    assert canonical[("GALLANTT", "promoter_pct")].value == 70


def test_overlapping_ownership_rejects_lower_priority_bucket() -> None:
    promoter = observation(value=70, unit="percent", provider="exchange_filing")
    institutional = observation(
        field="institutional_pct",
        value=40,
        unit="percent",
        provider="yahoo",
        observed_at=datetime(2026, 7, 22, tzinfo=UTC) + timedelta(hours=1),
    )

    canonical, normalized = select_canonical_metrics([promoter, institutional])

    assert ("GALLANTT", "promoter_pct") in canonical
    assert ("GALLANTT", "institutional_pct") not in canonical
    rejected = next(item for item in normalized if item.field == "institutional_pct")
    assert rejected.is_valid is False
    assert rejected.issues[0].code == "ownership_total_exceeds_100"


def test_returns_and_margins_are_not_range_checked() -> None:
    """Regression: 413 correct BSE figures were rejected as percent_out_of_range.

    SPARC is loss-making, so ROE of -1989% is the real number. Bounding it discarded
    the distressed small-caps a screener exists to surface.
    """
    for field, value in (("roe_pct", -1989.17), ("profit_margin_pct", -2193.15),
                         ("roe_pct", 148.0)):
        result = normalize_observation(
            MetricObservation(
                ticker="SPARC", field=field, value=value, unit="percent", provider="bse"
            )
        )
        assert result.is_valid, (field, value, result.issues)


def test_ownership_is_still_bounded() -> None:
    """The GALLANTT case: a promoter stake over 100% is a provider error, not a signal."""
    for value in (107.481, -3.0):
        result = normalize_observation(
            MetricObservation(
                ticker="GALLANTT", field="promoter_pct", value=value,
                unit="percent", provider="yahoo",
            )
        )
        assert not result.is_valid
        assert result.issues[0].code == "percent_out_of_range"
