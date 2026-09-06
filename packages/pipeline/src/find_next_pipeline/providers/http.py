from __future__ import annotations

import base64
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
        self._session: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._session = httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True)
        return self

    async def __aexit__(self, *args):
        await self._session.aclose()
        self._session = None

    async def _get(self, endpoint, params, headers):
        if self._session is not None:
            return await self._session.get(endpoint, params=params, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            return await client.get(endpoint, params=params, headers=headers)

    async def get_json(
        self,
        *,
        provider: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[RawEnvelope, Any]:
        requested_at = datetime.now(UTC)
        response = await self._get(endpoint, params, headers)
        received_at = datetime.now(UTC)
        # Archive the exact response body before JSON decoding or provider parsing.
        envelope, _ = self.raw_store.save(
            provider=provider,
            endpoint=endpoint,
            requested_at=requested_at,
            received_at=received_at,
            payload={"body_base64": base64.b64encode(response.content).decode("ascii")},
            status_code=response.status_code,
            request_params=params,
        )
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
        response = await self._get(endpoint, params, headers)
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
