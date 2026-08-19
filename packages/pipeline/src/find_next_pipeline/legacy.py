from __future__ import annotations

from copy import deepcopy
from typing import Any

PERCENT_FIELDS = ("promoter_pct", "institutional_pct")
PROVIDER_FRACTION_FIELDS = ("heldPercentInsiders", "heldPercentInstitutions")


def clean_legacy_stock(stock: dict[str, Any]) -> dict[str, Any]:
    """Validate legacy dashboard data while preserving a visible issue trail."""
    cleaned = deepcopy(stock)
    quality = cleaned.get("data_quality") or {}
    issues: list[dict[str, Any]] = deepcopy(quality.get("issues") or [])
    for field in PERCENT_FIELDS:
        value = cleaned.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float("nan")
        if not 0 <= numeric <= 100:
            issues.append(
                {
                    "code": "percent_out_of_range",
                    "field": field,
                    "raw_value": value,
                    "message": f"{field} must be between 0% and 100%",
                }
            )
            cleaned[field] = None

    for field in PROVIDER_FRACTION_FIELDS:
        value = cleaned.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float("nan")
        if not 0 <= numeric <= 1:
            issues.append(
                {
                    "code": "provider_fraction_out_of_range",
                    "field": field,
                    "raw_value": value,
                    "message": f"{field} must be between 0 and 1 before percent conversion",
                }
            )

    promoter = cleaned.get("promoter_pct")
    institutional = cleaned.get("institutional_pct")
    if promoter is not None and institutional is not None:
        total = float(promoter) + float(institutional)
        if total > 100.5:
            issues.append(
                {
                    "code": "ownership_total_exceeds_100",
                    "field": "institutional_pct",
                    "raw_value": institutional,
                    "message": f"Promoter and institutional ownership total {total:.2f}%",
                }
            )
            cleaned["institutional_pct"] = None

    cleaned["data_quality"] = {"status": "review" if issues else "valid", "issues": issues}
    return cleaned
