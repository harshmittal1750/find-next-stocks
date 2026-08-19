from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Severity = Severity.ERROR
    field: str | None = None
    raw_value: Any = None


class MetricObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: UUID = Field(default_factory=uuid4)
    ticker: str
    field: str
    value: float | int | str | None
    unit: str | None = None
    provider: str
    endpoint: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_request_id: UUID | None = None
    is_valid: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


class CanonicalMetric(BaseModel):
    ticker: str
    field: str
    value: float | int | str | None
    unit: str | None
    provider: str
    observed_at: datetime
    observation_id: UUID


class ProviderResult(BaseModel):
    provider: str
    observations: list[MetricObservation] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)


class RawEnvelope(BaseModel):
    schema_version: int = 1
    request_id: UUID = Field(default_factory=uuid4)
    provider: str
    endpoint: str
    requested_at: datetime
    received_at: datetime
    status_code: int | None = None
    request_params: dict[str, Any] = Field(default_factory=dict)
    payload: Any
    content_sha256: str
