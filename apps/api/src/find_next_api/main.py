from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from find_next_api.config import get_settings
from find_next_api.refresh_jobs import RefreshJobManager, provider_specs
from find_next_api.repository import DashboardRepository

settings = get_settings()
repository = DashboardRepository(database_url=settings.effective_database_url)
refresh_manager = RefreshJobManager(
    settings.effective_database_url,
    provider_specs(settings),
)


class RefreshRequest(BaseModel):
    providers: list[str] | None = None

app = FastAPI(
    title="Find Next Stocks API",
    version="0.1.0",
    description="Validated, provenance-aware equity research data.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({settings.web_origin, "http://localhost:3000", "http://127.0.0.1:3000"}),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    payload = repository.load()
    return {
        "status": "ok" if payload["data_status"] == "ready" else "degraded",
        "storage_backend": payload.get("storage_backend", "unknown"),
        "data_source": payload.get("served_from", "unknown"),
    }


@app.get("/api/v1/dashboard")
def dashboard() -> dict[str, Any]:
    return repository.load()


@app.get("/api/v1/refresh")
def latest_refresh() -> dict[str, Any]:
    return {
        "job": refresh_manager.latest(),
        "providers": refresh_manager.manifest(),
    }


@app.post("/api/v1/refresh", status_code=status.HTTP_202_ACCEPTED)
def start_refresh(request: RefreshRequest | None = None) -> dict[str, Any]:
    payload = repository.load()
    stocks = payload.get("stocks", [])
    if not stocks:
        raise HTTPException(status_code=503, detail="No stock universe is available")
    manifest = refresh_manager.manifest()
    availability = {item["provider"]: item["available"] for item in manifest}
    requested = (
        list(dict.fromkeys(request.providers))
        if request is not None and request.providers is not None
        else [provider for provider, available in availability.items() if available]
    )
    unknown = [provider for provider in requested if provider not in availability]
    unavailable = [
        provider
        for provider in requested
        if provider in availability and not availability[provider]
    ]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown providers: {', '.join(unknown)}",
        )
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail=f"Unavailable providers: {', '.join(unavailable)}",
        )
    if not requested:
        raise HTTPException(status_code=422, detail="Select at least one provider")
    job, already_running = refresh_manager.start(stocks, set(requested))
    return {"job": job, "already_running": already_running}


@app.get("/api/v1/refresh/{job_id}")
def refresh_status(job_id: str) -> dict[str, Any]:
    job = refresh_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown refresh job")
    return {"job": job}


@app.get("/api/v1/stocks")
def stocks(
    search: str | None = None,
    sector: str | None = None,
    min_coverage: float = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    records = repository.load()["stocks"]
    if search:
        needle = search.casefold()
        records = [
            stock
            for stock in records
            if needle in str(stock.get("ticker", "")).casefold()
            or needle in str(stock.get("shortName", "")).casefold()
        ]
    if sector:
        records = [stock for stock in records if stock.get("sector") == sector]
    records = [stock for stock in records if float(stock.get("data_cov") or 0) >= min_coverage]
    records.sort(key=lambda stock: float(stock.get("rank") or 10**9))
    return {"count": len(records), "stocks": records[:limit]}


@app.get("/api/v1/stocks/{ticker}")
def stock(ticker: str) -> dict[str, Any]:
    result = repository.get_stock(ticker)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker.upper()}")
    return result


@app.get("/api/v1/quality/issues")
def quality_issues(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    issues = [
        {"ticker": stock.get("ticker"), **issue}
        for stock in repository.load()["stocks"]
        for issue in stock.get("data_quality", {}).get("issues", [])
    ]
    return {"count": len(issues), "issues": issues[:limit]}
