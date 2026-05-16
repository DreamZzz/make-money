"""FastAPI entrypoint for Dashboard V2."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import PROJECT_ROOT
from src.dashboard_v2.schemas import CashflowCreate, IndexFundSnapshotCreate, SafeWriteResult
from src.dashboard_v2.service import SAFE_JOB_KEYS, DashboardV2Service


def create_app(service: Any | None = None) -> FastAPI:
    svc = service or DashboardV2Service()
    app = FastAPI(title="make-money Dashboard V2", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8600",
            "http://127.0.0.1:8600",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v2/today")
    def get_today() -> dict[str, Any]:
        return svc.build_today_snapshot()

    @app.get("/api/v2/rebalance/latest")
    def get_rebalance_latest() -> dict[str, Any]:
        return svc.build_rebalance_snapshot()

    @app.get("/api/v2/portfolio")
    def get_portfolio() -> dict[str, Any]:
        return svc.build_portfolio_snapshot()

    @app.get("/api/v2/health")
    def get_health() -> dict[str, Any]:
        return svc.build_health_snapshot()

    @app.get("/api/v2/research/summary")
    def get_research_summary() -> dict[str, Any]:
        return svc.build_research_summary()

    @app.post("/api/v2/jobs/{job_key}/start")
    def start_job(job_key: str) -> dict[str, Any]:
        if job_key not in SAFE_JOB_KEYS:
            if hasattr(svc, "reject_job_start"):
                svc.reject_job_start(job_key)
            raise HTTPException(status_code=403, detail=f"不允许从 Dashboard V2 启动任务：{job_key}")
        try:
            return svc.start_job(job_key)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v2/jobs/{run_id}")
    def get_job(run_id: str) -> dict[str, Any]:
        return svc.build_job_status(run_id)

    @app.post("/api/v2/cashflows", response_model=SafeWriteResult)
    def create_cashflow(payload: CashflowCreate) -> dict[str, str]:
        try:
            return svc.record_cashflow(payload.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v2/index-fund-snapshots", response_model=SafeWriteResult)
    def create_index_fund_snapshot(payload: IndexFundSnapshotCreate) -> dict[str, str]:
        try:
            return svc.record_index_fund_snapshot(payload.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist_dir = Path(PROJECT_ROOT) / "frontend" / "dashboard-v2" / "dist"
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="dashboard-v2-assets")

    if not (dist_dir / "index.html").exists():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        target = dist_dir / full_path
        if full_path and target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(dist_dir / "index.html")


app = create_app()
