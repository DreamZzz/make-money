from __future__ import annotations

from fastapi.testclient import TestClient


class FakeDashboardV2Service:
    def __init__(self) -> None:
        self.started_jobs: list[str] = []
        self.cashflows: list[dict] = []
        self.snapshots: list[dict] = []

    def build_today_snapshot(self) -> dict:
        return {
            "trade_date": "2026-05-15",
            "health": {"status": "ok", "label": "数据可用", "blocking": False, "messages": []},
            "account": {"cash": 120000.0, "total_value": 300000.0},
            "operation_summary": {"operation_count": 2, "cash_required": 18000.0, "estimated_minutes": 12},
            "blockers": [],
            "next_action": {"label": "查看调仓计划", "href": "/rebalance", "enabled": True},
            "evidence": {"data_date": "2026-05-15", "model_version": "alpha158_v1"},
        }

    def build_rebalance_snapshot(self) -> dict:
        return {
            "plan_id": "PLAN-1",
            "plan_date": "2026-05-15",
            "summary": {"operation_count": 2, "cash_required": 18000.0, "funding_gap": 0.0},
            "groups": {"executable": [], "confirm": [], "deferred": []},
            "conflicts": [],
            "evidence": {"cost_model": "paper_engine_t1_open"},
        }

    def build_portfolio_snapshot(self) -> dict:
        return {
            "account": {"cash": 120000.0, "total_value": 300000.0},
            "holdings": [],
            "risk_alerts": [],
            "exposure": {"industry": [], "size": [], "summary": {}},
            "signal_outcomes": {"summary": [], "monthly": []},
        }

    def build_health_snapshot(self) -> dict:
        return {
            "status": "ok",
            "latest_quote_date": "2026-05-15",
            "data_sources": [],
            "field_coverage": [],
            "qlib": {"production_available": True},
            "latest_job": None,
            "failure_diagnostic": None,
        }

    def build_research_summary(self) -> dict:
        return {
            "production_model": {"model_version": "alpha158_v1", "status": "production"},
            "recent_experiments": [],
            "ic": {"icir": 0.4, "rank_ic": 0.03},
            "portana": {"available": False},
        }

    def start_job(self, job_key: str) -> dict:
        self.started_jobs.append(job_key)
        return {"run_id": "RUN-1", "job_key": job_key, "status": "RUNNING"}

    def build_job_status(self, run_id: str) -> dict:
        return {"run_id": run_id, "status": "SUCCEEDED", "steps": [], "failure_diagnostic": None}

    def record_cashflow(self, payload: dict) -> dict:
        self.cashflows.append(payload)
        return {"id": "FLOW-1", "status": "ok"}

    def record_index_fund_snapshot(self, payload: dict) -> dict:
        self.snapshots.append(payload)
        return {"id": "IFSNAP-1", "status": "ok"}


def _client() -> tuple[TestClient, FakeDashboardV2Service]:
    from src.dashboard_v2.api import create_app

    service = FakeDashboardV2Service()
    return TestClient(create_app(service=service)), service


def test_dashboard_v2_get_contracts_expose_stable_operating_snapshots() -> None:
    client, _ = _client()

    today = client.get("/api/v2/today")
    rebalance = client.get("/api/v2/rebalance/latest")
    portfolio = client.get("/api/v2/portfolio")
    health = client.get("/api/v2/health")
    research = client.get("/api/v2/research/summary")

    assert today.status_code == 200
    assert set(today.json()) >= {"trade_date", "health", "account", "operation_summary", "next_action", "evidence"}
    assert rebalance.status_code == 200
    assert set(rebalance.json()) >= {"plan_id", "summary", "groups", "conflicts", "evidence"}
    assert portfolio.status_code == 200
    assert set(portfolio.json()) >= {"account", "holdings", "risk_alerts", "exposure", "signal_outcomes"}
    assert health.status_code == 200
    assert set(health.json()) >= {"status", "latest_quote_date", "data_sources", "field_coverage", "qlib"}
    assert research.status_code == 200
    assert set(research.json()) >= {"production_model", "recent_experiments", "ic", "portana"}


def test_dashboard_v2_job_start_is_whitelisted() -> None:
    client, service = _client()

    allowed = client.post("/api/v2/jobs/daily_close_workflow/start")
    blocked = client.post("/api/v2/jobs/qlib_research_workflow/start")

    assert allowed.status_code == 200
    assert allowed.json()["run_id"] == "RUN-1"
    assert service.started_jobs == ["daily_close_workflow"]
    assert blocked.status_code == 403
    assert "不允许" in blocked.json()["detail"]


def test_dashboard_v2_safe_write_endpoints_delegate_to_service() -> None:
    client, service = _client()

    cashflow = client.post(
        "/api/v2/cashflows",
        json={"flow_date": "2026-05-15", "flow_type": "DEPOSIT", "amount": 10000, "note": "追加资金"},
    )
    snapshot = client.post(
        "/api/v2/index-fund-snapshots",
        json={"snapshot_date": "2026-05-15", "fund_code": "510300", "shares": 1000, "cost_amount": 3800},
    )

    assert cashflow.status_code == 200
    assert service.cashflows[0]["flow_type"] == "DEPOSIT"
    assert snapshot.status_code == 200
    assert service.snapshots[0]["fund_code"] == "510300"
