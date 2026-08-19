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
    assert providers["bse"]["available"] is False
    assert providers["bse"]["reason"]


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
