from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from find_next_pipeline.models import RawEnvelope
from find_next_pipeline.paths import RAW_DIR

SENSITIVE_FRAGMENTS = ("key", "token", "secret", "password", "authorization", "cookie")


def _redact(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "[redacted]" if any(part in key.lower() for part in SENSITIVE_FRAGMENTS) else value
        for key, value in mapping.items()
    }


class RawJsonStore:
    def __init__(self, root: Path = RAW_DIR) -> None:
        self.root = root
        self.saved: list[tuple[RawEnvelope, Path]] = []

    def save(
        self,
        *,
        provider: str,
        endpoint: str,
        requested_at: datetime,
        received_at: datetime,
        payload: Any,
        status_code: int | None,
        request_params: dict[str, Any] | None = None,
        request_id: UUID | None = None,
    ) -> tuple[RawEnvelope, Path]:
        canonical_payload = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        envelope = RawEnvelope(
            request_id=request_id or uuid4(),
            provider=provider,
            endpoint=endpoint,
            requested_at=requested_at.astimezone(UTC),
            received_at=received_at.astimezone(UTC),
            status_code=status_code,
            request_params=_redact(request_params or {}),
            payload=payload,
            content_sha256=digest,
        )
        target_dir = self.root / provider / received_at.date().isoformat()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{envelope.request_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        self.saved.append((envelope, target))
        return envelope, target

    def drain_saved(self) -> list[tuple[RawEnvelope, Path]]:
        saved = self.saved[:]
        self.saved.clear()
        return saved


def utc_now() -> datetime:
    return datetime.now(UTC)
