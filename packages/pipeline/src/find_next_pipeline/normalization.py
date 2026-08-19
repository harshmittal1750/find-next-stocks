from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from find_next_pipeline.models import CanonicalMetric, MetricObservation, Severity, ValidationIssue

PERCENT_FIELDS = {
    "promoter_pct",
    "institutional_pct",
    "roe_pct",
    "return_on_assets_pct",
    "profit_margin_pct",
}

OWNERSHIP_FIELDS = {"promoter_pct", "institutional_pct"}

DEFAULT_PROVIDER_PRIORITY = {
    "exchange_filing": 10,
    "nse": 15,
    "bse": 15,
    "company_filing": 20,
    "upstox": 35,
    "alpha_vantage": 40,
    "fmp": 50,
    "yahoo": 80,
    "legacy_dashboard": 90,
}


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_observation(observation: MetricObservation) -> MetricObservation:
    """Normalize units and attach validation issues without losing the raw observation."""
    normalized = observation.model_copy(deep=True)
    if normalized.field not in PERCENT_FIELDS:
        return normalized

    numeric = _coerce_float(normalized.value)
    if numeric is None:
        normalized.is_valid = False
        normalized.issues.append(
            ValidationIssue(
                code="not_numeric",
                message=f"{normalized.field} must be numeric",
                field=normalized.field,
                raw_value=normalized.value,
            )
        )
        return normalized

    if normalized.unit == "fraction":
        numeric *= 100
        normalized.unit = "percent"
    elif normalized.unit not in {None, "percent"}:
        normalized.is_valid = False
        normalized.issues.append(
            ValidationIssue(
                code="unexpected_unit",
                message=f"Expected percent or fraction, received {normalized.unit}",
                field=normalized.field,
                raw_value=normalized.value,
            )
        )
        return normalized

    normalized.value = round(numeric, 6)
    normalized.unit = "percent"
    if not 0 <= numeric <= 100:
        normalized.is_valid = False
        normalized.issues.append(
            ValidationIssue(
                code="percent_out_of_range",
                message=f"{normalized.field} resolved to {numeric:.3f}%; expected 0–100%",
                field=normalized.field,
                raw_value=observation.value,
            )
        )
    return normalized


def select_canonical_metrics(
    observations: Iterable[MetricObservation],
    provider_priority: dict[str, int] | None = None,
) -> tuple[dict[tuple[str, str], CanonicalMetric], list[MetricObservation]]:
    """Return selected valid metrics and every normalized candidate for audit."""
    priorities = provider_priority or DEFAULT_PROVIDER_PRIORITY
    normalized = [normalize_observation(item) for item in observations]
    grouped: dict[tuple[str, str], list[MetricObservation]] = defaultdict(list)
    for item in normalized:
        grouped[(item.ticker, item.field)].append(item)

    canonical: dict[tuple[str, str], CanonicalMetric] = {}
    for key, candidates in grouped.items():
        valid = [item for item in candidates if item.is_valid]
        if not valid:
            continue
        valid.sort(
            key=lambda item: (
                priorities.get(item.provider, 1_000),
                -item.observed_at.timestamp(),
            )
        )
        selected = valid[0]
        canonical[key] = CanonicalMetric(
            ticker=selected.ticker,
            field=selected.field,
            value=selected.value,
            unit=selected.unit,
            provider=selected.provider,
            observed_at=selected.observed_at,
            observation_id=selected.observation_id,
        )

    _validate_ownership_totals(canonical, normalized, priorities)
    return canonical, normalized


def _validate_ownership_totals(
    canonical: dict[tuple[str, str], CanonicalMetric],
    observations: list[MetricObservation],
    priorities: dict[str, int],
) -> None:
    tickers = {ticker for ticker, field in canonical if field in OWNERSHIP_FIELDS}
    by_id = {item.observation_id: item for item in observations}
    for ticker in tickers:
        promoter = canonical.get((ticker, "promoter_pct"))
        institutional = canonical.get((ticker, "institutional_pct"))
        if promoter is None or institutional is None:
            continue
        total = float(promoter.value or 0) + float(institutional.value or 0)
        if total <= 100.5:
            continue

        # Preserve the higher-priority candidate. A tie rejects the institutional bucket,
        # which is more likely to overlap with promoter classifications in aggregator data.
        promoter_priority = priorities.get(promoter.provider, 1_000)
        institutional_priority = priorities.get(institutional.provider, 1_000)
        rejected = promoter if promoter_priority > institutional_priority else institutional
        canonical.pop((ticker, rejected.field), None)
        candidate = by_id[rejected.observation_id]
        candidate.is_valid = False
        candidate.issues.append(
            ValidationIssue(
                code="ownership_total_exceeds_100",
                message=f"Promoter and institutional ownership total {total:.3f}%",
                severity=Severity.ERROR,
                field=rejected.field,
                raw_value=rejected.value,
            )
        )
