from fastapi.testclient import TestClient
from find_next_api.main import app, refresh_manager, repository

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_gallantt_ownership_is_within_physical_bounds() -> None:
    response = client.get("/api/v1/stocks/GALLANTT")

    assert response.status_code == 200
    assert 0 <= response.json()["promoter_pct"] <= 100


def test_refresh_manifest_reports_provider_availability() -> None:
    response = client.get("/api/v1/refresh")

    assert response.status_code == 200
    providers = {item["provider"]: item for item in response.json()["providers"]}
    assert providers["nse"]["available"] is True
    assert providers["yahoo"]["available"] is True
    # BSE was enabled by owner decision; it needs no credentials, so it is available.
    assert providers["bse"]["available"] is True
    # A provider that genuinely cannot run must still say why, rather than failing
    # silently at refresh time.
    assert providers["upstox"]["available"] is False
    assert providers["upstox"]["reason"]


def test_refresh_request_passes_only_selected_providers(monkeypatch) -> None:
    selected: dict[str, set[str]] = {}
    monkeypatch.setattr(repository, "load", lambda: {"stocks": [{"ticker": "AAA"}]})
    monkeypatch.setattr(
        refresh_manager,
        "manifest",
        lambda: [
            {"provider": "nse", "available": True},
            {"provider": "yahoo", "available": True},
            {"provider": "bse", "available": False},
        ],
    )

    def start(stocks, providers):
        selected["providers"] = providers
        return {"job_id": "job-1", "status": "queued"}, False

    monkeypatch.setattr(refresh_manager, "start", start)

    response = client.post("/api/v1/refresh", json={"providers": ["yahoo"]})

    assert response.status_code == 202
    assert selected["providers"] == {"yahoo"}


def test_refresh_request_rejects_unavailable_provider(monkeypatch) -> None:
    monkeypatch.setattr(repository, "load", lambda: {"stocks": [{"ticker": "AAA"}]})
    monkeypatch.setattr(
        refresh_manager,
        "manifest",
        lambda: [{"provider": "bse", "available": False}],
    )

    response = client.post("/api/v1/refresh", json={"providers": ["bse"]})

    assert response.status_code == 422
    assert response.json()["detail"] == "Unavailable providers: bse"


def test_quality_coverage_separates_obtainable_from_raw() -> None:
    response = client.get("/api/v1/quality/coverage")

    assert response.status_code == 200
    payload = response.json()
    # Excluding cells nobody can supply must never make coverage look worse.
    assert payload["obtainable_coverage_pct"] >= payload["raw_coverage_pct"]
    assert payload["gaps"]["analyst"] > 0
    assert payload["stocks"] > 0


def test_quality_gaps_explains_each_blank() -> None:
    """Every blank gets a reason from the known set.

    Deliberately not asserting that a named stock is missing a named field: an earlier
    version pinned OMAXE's blank `rank`, which stopped being blank the moment the
    database had real data. That tested the snapshot, not the endpoint.
    """
    response = client.get("/api/v1/quality/gaps/OMAXE")

    assert response.status_code == 200
    gaps = response.json()["gaps"]
    known = {"recoverable", "analyst", "undefined", "not_applicable", "derived",
             "descriptive", "unknown"}
    assert set(gaps.values()) <= known, gaps
    # Whatever is blank, pipeline bookkeeping is never reported as a provider gap.
    for field in ("rank", "final_score", "current_rank"):
        assert gaps.get(field, "derived") == "derived"


def test_quality_gaps_rejects_an_unknown_ticker() -> None:
    assert client.get("/api/v1/quality/gaps/NOSUCHTICKER").status_code == 404
