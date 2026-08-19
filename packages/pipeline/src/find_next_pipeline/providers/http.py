from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from find_next_pipeline.models import RawEnvelope
from find_next_pipeline.raw_store import RawJsonStore


class ArchivedHttpClient:
    """HTTP JSON transport that archives both successful and error payloads."""

    def __init__(self, raw_store: RawJsonStore, timeout_seconds: float = 30) -> None:
        self.raw_store = raw_store
        self.timeout_seconds = timeout_seconds

    async def get_json(
        self,
        *,
        provider: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[RawEnvelope, Any]:
        requested_at = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(endpoint, params=params, headers=headers)
        received_at = datetime.now(UTC)
        try:
            payload = response.json()
        except ValueError:
            if response.content.startswith(b"\x1f\x8b"):
                try:
                    payload = json.loads(gzip.decompress(response.content))
                except (gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError):
                    payload = {"unparsed_text": response.text}
            else:
                payload = {"unparsed_text": response.text}
        envelope, _ = self.raw_store.save(
            provider=provider,
            endpoint=endpoint,
            requested_at=requested_at,
            received_at=received_at,
            payload=payload,
            status_code=response.status_code,
            request_params=params,
        )
        response.raise_for_status()
        return envelope, payload

    async def get_text(
        self,
        *,
        provider: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[RawEnvelope, str]:
        requested_at = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(endpoint, params=params, headers=headers)
        received_at = datetime.now(UTC)
        envelope, _ = self.raw_store.save(
            provider=provider,
            endpoint=endpoint,
            requested_at=requested_at,
            received_at=received_at,
            payload={"text": response.text},
            status_code=response.status_code,
            request_params=params,
        )
        response.raise_for_status()
        return envelope, response.text
