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
from src.dashboard_v2.service import DashboardV2Service


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
        return _call_or_degrade(svc.build_today_snapshot, _fallback_today)

    @app.get("/api/v2/rebalance/latest")
    def get_rebalance_latest() -> dict[str, Any]:
        return _call_or_degrade(svc.build_rebalance_snapshot, _fallback_rebalance)

    @app.get("/api/v2/portfolio")
    def get_portfolio() -> dict[str, Any]:
        return _call_or_degrade(svc.build_portfolio_snapshot, _fallback_portfolio)

    @app.get("/api/v2/health")
    def get_health() -> dict[str, Any]:
        return _call_or_degrade(svc.build_health_snapshot, _fallback_health)

    @app.get("/api/v2/research/summary")
    def get_research_summary() -> dict[str, Any]:
        return _call_or_degrade(svc.build_research_summary, _fallback_research)

    @app.get("/api/v2/tournament")
    def get_tournament() -> dict[str, Any]:
        return _call_or_degrade(svc.build_tournament_snapshot, _fallback_tournament)

    @app.get("/api/v2/market")
    def get_market() -> dict[str, Any]:
        return _call_or_degrade(svc.build_market_snapshot, _fallback_market)

    @app.get("/api/v2/funds")
    def get_funds() -> dict[str, Any]:
        return _call_or_degrade(svc.build_funds_snapshot, _fallback_funds)

    @app.post("/api/v2/jobs/{job_key}/start")
    def start_job(job_key: str) -> dict[str, Any]:
        if hasattr(svc, "reject_job_start"):
            svc.reject_job_start(job_key)
        raise HTTPException(status_code=403, detail=f"Dashboard V2 只展示定时任务状态，不允许启动任务：{job_key}")

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


def _call_or_degrade(builder: Any, fallback_builder: Any) -> dict[str, Any]:
    try:
        return builder()
    except Exception as exc:
        if _is_database_unavailable(exc):
            return fallback_builder(exc)
        raise


def _is_database_unavailable(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Could not set lock on file" in message
        or "Conflicting lock is held" in message
        or "database is locked" in message.lower()
    )


def _data_unavailable_message(exc: Exception) -> str:
    pid_hint = _extract_pid_hint(str(exc))
    suffix = f"（锁定进程 {pid_hint}）" if pid_hint else ""
    return f"DuckDB 正被后台任务占用，数据暂不可用{suffix}；请等待定时任务结束后刷新。"


def _extract_pid_hint(message: str) -> str | None:
    marker = "PID "
    if marker not in message:
        return None
    tail = message.split(marker, 1)[1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    return f"PID {digits}" if digits else None


def _fallback_health(exc: Exception) -> dict[str, Any]:
    message = _data_unavailable_message(exc)
    return {
        "status": "failed",
        "label": "数据暂不可用",
        "blocking": True,
        "messages": [message],
        "latest_quote_date": None,
        "data_health_summary": None,
        "data_sources": [],
        "field_coverage": [],
        "scheduled_jobs": [],
        "scheduled_job_history": [],
        "regime_policy": _unavailable_regime_policy(message),
        "qlib": {"production_available": False},
        "latest_job": None,
        "failure_diagnostic": {
            "summary": message,
            "raw_error": str(exc),
        },
    }


def _fallback_today(exc: Exception) -> dict[str, Any]:
    health = _fallback_health(exc)
    return {
        "trade_date": None,
        "health": health,
        "account": _empty_account(),
        "capital": _empty_capital(),
        "regime_policy": _unavailable_regime_policy(health["messages"][0]),
        "operation_summary": _empty_operation_summary(),
        "blockers": [{
            "level": "error",
            "label": "数据暂不可用",
            "message": health["messages"][0],
        }],
        "next_action": {"label": "查看数据健康", "href": "/health", "enabled": True},
        "funds_summary": {"available": False, "headline": health["messages"][0]},
        "evidence": {"error": health["messages"][0]},
    }


def _fallback_rebalance(exc: Exception) -> dict[str, Any]:
    message = _data_unavailable_message(exc)
    return {
        "plan_id": None,
        "plan_date": None,
        "capital": _empty_capital(),
        "regime_policy": _unavailable_regime_policy(message),
        "summary": {**_empty_operation_summary(), "funding_gap": 0.0},
        "budget_reason": None,
        "groups": {"budget": [], "executable": [], "confirm": [], "deferred": []},
        "sell_signals": [],
        "conflicts": [],
        "one_lot_gaps": [],
        "satellite_candidates": {
            "budget": 0.0,
            "candidate_count": 0,
            "covered_count": 0,
            "over_budget_count": 0,
            "rows": [],
            "decision_hint": message,
        },
        "evidence": {"error": message},
    }


def _fallback_portfolio(exc: Exception) -> dict[str, Any]:
    message = _data_unavailable_message(exc)
    return {
        "account": _empty_account(),
        "capital": _empty_capital(),
        "regime_policy": _unavailable_regime_policy(message),
        "holdings": [],
        "risk_alerts": [{
            "level": "error",
            "label": "数据暂不可用",
            "message": message,
            "affected_holdings": [],
            "suggested_actions": ["等待后台任务结束后刷新；若长期不恢复，请检查市场与数据健康页的定时任务状态。"],
        }],
        "exposure": {"industry": [], "size": [], "summary": {}, "insights": []},
        "funds_panel": {"available": False, "funds": [], "alerts": [], "alternatives": []},
        "signal_outcomes": {
            "summary": [],
            "monthly": [],
            "detail": [],
            "state": {
                "status": "error",
                "message": f"数据暂不可用：{message}",
                "ready_count": 0,
                "pending_count": 0,
                "total_count": 0,
                "next_ready_date": None,
            },
        },
        "evidence": {"error": message},
    }


def _fallback_research(exc: Exception) -> dict[str, Any]:
    message = _data_unavailable_message(exc)
    return {
        "production_model": None,
        "recent_experiments": [],
        "ic": {"ic": None, "rank_ic": None, "icir": None, "sample_days": 0},
        "portana": {"available": False},
        "legacy_streamlit": {"label": "打开 Streamlit 研究工作台", "url": "http://localhost:8501"},
        "error": message,
    }


def _fallback_market(exc: Exception) -> dict[str, Any]:
    return {"market_state": None, "exposure": None, "allocation": [], "history": [],
            "error": _data_unavailable_message(exc)}


def _fallback_funds(exc: Exception) -> dict[str, Any]:
    msg = _data_unavailable_message(exc)
    return {
        "eval_date": None,
        "account_total_value": None,
        "equity_exposure": None,
        "core_total_target_value": 0.0,
        "core_total_current_value": 0.0,
        "core_total_delta_amount": 0.0,
        "overall_advice": {"headline": msg, "actions": []},
        "funds": [],
        "holding_alerts": [],
        "recommendations": {
            "eval_date": None, "in_window": [], "watch_high_value": [],
            "oversold_candidates": [],
            "excluded_holdings": [], "overlap_tracking": [],
            "holding_categories": [], "total_candidates": 0,
            "overall_advice": msg,
        },
        "rebalance_plan": {
            "plan_id": None, "plan_date": None,
            "trigger_type": "monthly", "trigger_reason": msg,
            "account_total": None, "equity_exposure": None,
            "actions": [], "headline": msg,
            "total_actions": 0, "total_buy_amount": 0.0, "total_sell_amount": 0.0,
        },
        "risk_attribution": {
            "eval_date": None, "portfolio_annual_volatility": None,
            "sleeves": [], "correlation_matrix": [], "sleeve_codes": [],
            "headline": msg, "risk_tags": [],
        },
        "monte_carlo": {
            "eval_date": None, "horizon_days": 252, "n_paths": 0,
            "history_days_used": 0, "block_size": 5,
            "return_percentiles": {}, "drawdown_percentiles": {},
            "expected_return": 0.0, "expected_volatility": 0.0,
            "prob_loss": 0.0, "prob_loss_10pct": 0.0,
            "headline": msg, "risk_tags": [],
        },
        "error": msg,
    }


def _fallback_tournament(exc: Exception) -> dict[str, Any]:
    return {
        "accounts": [],
        "leaderboard": [],
        "tournament": {"ranking": [], "eligible_count": 0, "recommended_winner": None, "selection_note": ""},
        "nav_curves": {},
        "error": _data_unavailable_message(exc),
    }


def _empty_account() -> dict[str, float | None]:
    return {
        "trade_date": None,
        "cash": 0.0,
        "position_value": 0.0,
        "total_value": 0.0,
        "net_contribution": 0.0,
        "nav": 1.0,
        "daily_return": 0.0,
        "drawdown": 0.0,
    }


def _empty_capital() -> dict[str, float | str | dict[str, float | str]]:
    return {
        "scope": "unavailable",
        "scope_label": "资金口径暂不可用",
        "scope_note": "数据暂不可用；恢复后会展示统一资金池与股票纸盘账户的对账结果。",
        "formula": "统一总资产 = 现金 + Core基金市值 + Satellite股票市值",
        "unified_total_value": 0.0,
        "trading_account_total_value": 0.0,
        "trading_position_value": 0.0,
        "cash": 0.0,
        "core_value": 0.0,
        "satellite_value": 0.0,
        "core_budget": 0.0,
        "satellite_budget": 0.0,
        "reserved_cash": 0.0,
        "unreserved_cash": 0.0,
        "core_target_value": 0.0,
        "satellite_target_value": 0.0,
        "core_target_pct": 0.0,
        "satellite_target_pct": 0.0,
        "cash_target_pct": 0.0,
        "cash_target_value": 0.0,
        "reconciliation": {
            "formula": "统一总资产 = 现金 + Core基金市值 + Satellite股票市值",
            "computed_total": 0.0,
            "recorded_total": 0.0,
            "delta": 0.0,
            "trading_account_formula": "股票纸盘资产 = 现金 + Satellite股票市值",
            "trading_account_computed_total": 0.0,
            "trading_account_recorded_total": 0.0,
            "trading_account_delta": 0.0,
        },
    }


def _unavailable_regime_policy(message: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "as_of_date": None,
        "regime_key": None,
        "regime_label": "宏观状态不可用",
        "stance": "unknown",
        "application_state": "not_applied",
        "buy_mode": "paused",
        "satellite_budget_multiplier": None,
        "signal_threshold_adjustment": None,
        "reason_summary": message,
        "source": "unavailable",
        "evidence": {"source": "unavailable", "data_date": None},
    }


def _empty_operation_summary() -> dict[str, int | float]:
    return {
        "operation_count": 0,
        "cash_required": 0.0,
        "reserved_cash": 0.0,
        "cash_commitment": 0.0,
        "available_cash_after_reserve": 0.0,
        "available_cash_after_commitment": 0.0,
        "estimated_minutes": 0,
        "buy_count": 0,
        "reduce_count": 0,
    }


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
