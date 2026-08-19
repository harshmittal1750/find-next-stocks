import json
from datetime import UTC, datetime

from find_next_pipeline.raw_store import RawJsonStore


def test_raw_store_hashes_payload_and_redacts_credentials(tmp_path) -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    envelope, path = RawJsonStore(tmp_path).save(
        provider="example",
        endpoint="https://example.test/quotes",
        requested_at=now,
        received_at=now,
        payload={"ticker": "GALLANTT", "value": 70},
        status_code=200,
        request_params={"symbol": "GALLANTT", "api_key": "do-not-write-this"},
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert envelope.content_sha256 == saved["content_sha256"]
    assert saved["request_params"]["api_key"] == "[redacted]"
    assert "do-not-write-this" not in path.read_text(encoding="utf-8")
