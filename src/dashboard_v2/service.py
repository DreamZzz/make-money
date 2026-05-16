"""Service layer for the Dashboard V2 retail operating cockpit.

The API calls this module instead of embedding SQL in React components.  The
queries here intentionally summarize existing domain tables into stable
front-end contracts while keeping writes limited to auditable, low-risk actions.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.dashboard import job_manager
from src.dashboard.signal_outcome_service import load_signal_outcome_snapshot
from src.data_pipeline.loader import get_connection, init_db
from src.index_funds.performance import add_snapshot
from src.portfolio.cashbook import add_cashflow
from src.portfolio.exposure_monitor import load_exposure_snapshot

SAFE_JOB_KEYS = {
    "daily_close_workflow",
    "update",
    "index_funds_update",
    "index_funds_signals",
    "generate_signals",
    "qlib_predict",
    "allocation_plan",
    "signal_outcomes",
}


class DashboardV2Service:
    """Build Dashboard V2 snapshots and execute audited safe writes."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else None

    def _connect(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        if self.db_path is not None:
            conn = duckdb.connect(str(self.db_path), read_only=read_only)
        else:
            conn = get_connection(read_only=read_only)
        if not read_only:
            init_db(conn)
        return conn

    def build_today_snapshot(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            latest_date = _latest_trade_date(conn)
            account = _load_account_summary(conn)
            plan = _load_latest_plan(conn)
            operation_summary = _build_operation_summary(plan["items"])
            health = _build_health_status(conn)
            blockers = _build_blockers(health, account, plan)
            next_action = _next_action(health, operation_summary, blockers)
            latest_job = _job_to_dict(job_manager.latest_run())
            evidence = {
                "data_date": latest_date,
                "signal_date": _latest_signal_date(conn),
                "model_version": _production_model_version(conn),
                "cost_model": "paper_engine_t1_open_with_fee_and_lot",
                "risk_rules": ["core_satellite_budget", "execution_guards", "risk_profile"],
                "latest_job_status": latest_job.get("status") if latest_job else None,
            }
            return {
                "trade_date": latest_date,
                "health": health,
                "account": account,
                "operation_summary": operation_summary,
                "blockers": blockers,
                "next_action": next_action,
                "evidence": evidence,
            }

    def build_rebalance_snapshot(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            plan = _load_latest_plan(conn)
            groups = _group_rebalance_items(plan["items"])
            summary = _build_operation_summary(plan["items"])
            summary["funding_gap"] = max(float(summary["cash_required"] or 0) - float(plan["cash"] or 0), 0.0)
            return {
                "plan_id": plan["plan_id"],
                "plan_date": _date_to_iso(plan["plan_date"]),
                "summary": summary,
                "groups": groups,
                "conflicts": _load_signal_conflicts(conn),
                "one_lot_gaps": _load_one_lot_gaps(conn),
                "evidence": {
                    "data_date": _latest_trade_date(conn),
                    "signal_date": _latest_signal_date(conn),
                    "cost_model": "paper_engine_t1_open_with_fee_and_lot",
                    "budget": {
                        "core_budget": _safe_float(plan.get("core_budget")),
                        "satellite_budget": _safe_float(plan.get("satellite_budget")),
                    },
                },
            }

    def build_portfolio_snapshot(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            account = _load_account_summary(conn)
            holdings = _load_latest_holdings(conn)
            exposure = _safe_exposure_snapshot(conn)
            outcomes = _safe_signal_outcomes(conn)
            return {
                "account": account,
                "holdings": holdings,
                "risk_alerts": exposure["warnings"],
                "exposure": {
                    "industry": exposure["industry"],
                    "size": exposure["size"],
                    "summary": exposure["summary"],
                },
                "signal_outcomes": outcomes,
                "evidence": {
                    "position_date": holdings[0]["trade_date"] if holdings else None,
                    "benchmark": "000300",
                    "valuation_fields": ["pe_ttm", "pb", "market_cap", "industry"],
                },
            }

    def build_health_snapshot(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            health = _build_health_status(conn)
            latest_run = _job_to_dict(job_manager.latest_run())
            failure = job_manager.latest_failure_diagnostic(latest_run) if latest_run else None
            return {
                **health,
                "latest_quote_date": _latest_trade_date(conn),
                "data_sources": _load_data_sources(conn),
                "field_coverage": _load_field_coverage(conn),
                "qlib": _load_qlib_status(conn),
                "latest_job": latest_run,
                "failure_diagnostic": failure,
            }

    def build_research_summary(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            production = _load_production_model(conn)
            experiments = _load_recent_experiments(conn)
            ic = _load_recent_ic(conn)
            return {
                "production_model": production,
                "recent_experiments": experiments,
                "ic": ic,
                "portana": _load_portana_status(),
                "legacy_streamlit": {"label": "打开 Streamlit 研究工作台", "url": "http://localhost:8501"},
            }

    def start_job(self, job_key: str) -> dict[str, Any]:
        if job_key not in SAFE_JOB_KEYS:
            self.reject_job_start(job_key)
            raise PermissionError(f"不允许从 Dashboard V2 启动任务：{job_key}")
        try:
            run_id = job_manager.start_job(job_key)
            result = {"run_id": run_id, "job_key": job_key, "status": "RUNNING"}
            self._record_audit("job.start", {"job_key": job_key}, "ok", result)
            return result
        except Exception as exc:
            self._record_audit("job.start", {"job_key": job_key}, "failed", error_message=str(exc))
            raise

    def reject_job_start(self, job_key: str) -> None:
        self._record_audit(
            "job.start",
            {"job_key": job_key},
            "rejected",
            error_message="job is not in Dashboard V2 whitelist",
        )

    def build_job_status(self, run_id: str) -> dict[str, Any]:
        run = job_manager.poll_run(run_id)
        data = _job_to_dict(run)
        if not data:
            return {"run_id": run_id, "status": "NOT_FOUND", "steps": [], "failure_diagnostic": None}
        return {
            **data,
            "failure_diagnostic": job_manager.latest_failure_diagnostic(data),
            "log_excerpt": job_manager.tail_log(run_id, lines=80),
        }

    def record_cashflow(self, payload: dict[str, Any]) -> dict[str, str]:
        try:
            if self.db_path is not None:
                flow_id = self._insert_cashflow(payload)
            else:
                flow_id = add_cashflow(
                    flow_date=_coerce_date(payload["flow_date"]),
                    flow_type=str(payload["flow_type"]),
                    amount=float(payload["amount"]),
                    note=str(payload.get("note") or ""),
                    account_id=str(payload.get("account_id") or "default"),
                    currency=str(payload.get("currency") or "CNY"),
                )
            result = {"id": flow_id, "status": "ok"}
            self._record_audit("cashflow.create", payload, "ok", result)
            return result
        except Exception as exc:
            self._record_audit("cashflow.create", payload, "failed", error_message=str(exc))
            raise

    def record_index_fund_snapshot(self, payload: dict[str, Any]) -> dict[str, str]:
        try:
            if self.db_path is not None:
                snapshot_id = self._insert_index_fund_snapshot(payload)
            else:
                snapshot_id = add_snapshot(
                    fund_code=str(payload["fund_code"]),
                    snapshot_date=_coerce_date(payload["snapshot_date"]),
                    shares=float(payload["shares"]),
                    cost_amount=float(payload["cost_amount"]),
                    note=str(payload.get("note") or ""),
                )
            result = {"id": snapshot_id, "status": "ok"}
            self._record_audit("index_fund_snapshot.create", payload, "ok", result)
            return result
        except Exception as exc:
            self._record_audit("index_fund_snapshot.create", payload, "failed", error_message=str(exc))
            raise

    def _record_audit(
        self,
        action: str,
        payload: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self._connect(read_only=False)
        try:
            conn.execute(
                """
                INSERT INTO dashboard_audit_log (
                    audit_id, action, payload_json, status, result_json, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    f"DAUD-{uuid.uuid4().hex[:12].upper()}",
                    action,
                    _json_dumps(payload),
                    status,
                    _json_dumps(result or {}),
                    error_message,
                ],
            )
        finally:
            conn.close()

    def _insert_cashflow(self, payload: dict[str, Any]) -> str:
        flow_type = str(payload["flow_type"])
        amount = float(payload["amount"])
        if flow_type not in {"DEPOSIT", "WITHDRAW"}:
            raise ValueError("flow_type must be DEPOSIT or WITHDRAW")
        if amount <= 0:
            raise ValueError("amount must be positive")
        flow_id = f"FLOW-{uuid.uuid4().hex[:10].upper()}"
        conn = self._connect(read_only=False)
        try:
            conn.execute(
                """
                INSERT INTO account_cashflows (
                    flow_id, flow_date, account_id, currency, flow_type, amount, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    flow_id,
                    _coerce_date(payload["flow_date"]),
                    str(payload.get("account_id") or "default"),
                    str(payload.get("currency") or "CNY"),
                    flow_type,
                    amount,
                    str(payload.get("note") or ""),
                ],
            )
        finally:
            conn.close()
        return flow_id

    def _insert_index_fund_snapshot(self, payload: dict[str, Any]) -> str:
        if not payload.get("fund_code"):
            raise ValueError("fund_code is required")
        shares = float(payload["shares"])
        cost_amount = float(payload["cost_amount"])
        if shares < 0 or cost_amount < 0:
            raise ValueError("shares and cost_amount must be non-negative")
        snapshot_id = f"IFSNAP-{uuid.uuid4().hex[:10].upper()}"
        conn = self._connect(read_only=False)
        try:
            conn.execute(
                """
                INSERT INTO index_fund_snapshots (
                    snapshot_id, snapshot_date, fund_code, shares, cost_amount, note
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    _coerce_date(payload["snapshot_date"]),
                    str(payload["fund_code"]),
                    shares,
                    cost_amount,
                    str(payload.get("note") or ""),
                ],
            )
        finally:
            conn.close()
        return snapshot_id

    def _managed_connection(self, read_only: bool = True):
        return _ConnectionContext(self, read_only=read_only)


class _ConnectionContext:
    def __init__(self, service: DashboardV2Service, read_only: bool) -> None:
        self.service = service
        self.read_only = read_only
        self.conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self):
        self.conn = self.service._connect(read_only=self.read_only)
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()


def _latest_trade_date(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    return _date_to_iso(row[0] if row else None)


def _latest_signal_date(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute("SELECT MAX(CAST(signal_ts AS DATE)) FROM signals").fetchone()
    return _date_to_iso(row[0] if row else None)


def _load_account_summary(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT trade_date, cash, position_value, total_value, net_contribution, nav, daily_return, drawdown
        FROM account_daily
        WHERE account_id = 'default'
        ORDER BY trade_date DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
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
    keys = ["trade_date", "cash", "position_value", "total_value", "net_contribution", "nav", "daily_return", "drawdown"]
    return _jsonable(dict(zip(keys, row)))


def _load_latest_plan(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    plan_row = conn.execute(
        """
        SELECT plan_id, plan_date, total_value, cash, core_target_pct, satellite_target_pct,
               core_value, satellite_value, core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        FROM allocation_plans
        ORDER BY plan_date DESC, created_at DESC, plan_id DESC
        LIMIT 1
        """
    ).fetchone()
    if plan_row is None:
        return {
            "plan_id": None,
            "plan_date": None,
            "cash": 0.0,
            "core_budget": 0.0,
            "satellite_budget": 0.0,
            "items": [],
        }
    keys = [
        "plan_id",
        "plan_date",
        "total_value",
        "cash",
        "core_target_pct",
        "satellite_target_pct",
        "core_value",
        "satellite_value",
        "core_budget",
        "satellite_budget",
        "core_drift_pct",
        "satellite_drift_pct",
        "status",
    ]
    plan = _jsonable(dict(zip(keys, plan_row)))
    df = conn.execute(
        """
        SELECT sleeve, instrument_type, instrument_id, action, current_value, target_value,
               budget_delta, execution_mode, expected_cash, cash_effect, budget_consumption, priority, reason
        FROM allocation_plan_items
        WHERE plan_id = ?
        ORDER BY COALESCE(priority, 999), sleeve, instrument_type, instrument_id
        """,
        [plan["plan_id"]],
    ).fetchdf()
    plan["items"] = _records(df)
    return plan


def _build_operation_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [
        item
        for item in items
        if str(item.get("action") or "").upper() not in {"HOLD", "PAUSE"}
        and str(item.get("execution_mode") or "").upper() in {"MANUAL", "BUDGET"}
    ]
    cash_required = sum(max(_safe_float(item.get("expected_cash")), 0.0) for item in actionable)
    return {
        "operation_count": len(actionable),
        "cash_required": round(cash_required, 2),
        "estimated_minutes": max(len(actionable) * 6, 0),
        "buy_count": sum(1 for item in actionable if str(item.get("action") or "").upper() in {"BUY", "ADD"}),
        "reduce_count": sum(1 for item in actionable if str(item.get("action") or "").upper() == "REDUCE"),
    }


def _build_health_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    messages: list[str] = []
    latest_date = _latest_trade_date(conn)
    if latest_date is None:
        messages.append("daily_price 尚无行情日期")
    production = _production_model_version(conn)
    if production is None:
        messages.append("Qlib production 模型不可用")
    latest_run = _job_to_dict(job_manager.latest_run())
    if latest_run and latest_run.get("status") == "FAILED":
        messages.append(f"最近任务失败：{latest_run.get('job_label') or latest_run.get('job_key')}")
    status = "ok" if not messages else ("failed" if any("失败" in msg for msg in messages) else "degraded")
    return {
        "status": status,
        "label": {"ok": "数据可用", "degraded": "数据需确认", "failed": "任务失败"}[status],
        "blocking": status == "failed" or latest_date is None,
        "messages": messages,
    }


def _build_blockers(health: dict[str, Any], account: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if health.get("blocking"):
        blockers.append({"level": "error", "title": health.get("label"), "detail": "数据或任务状态阻塞调仓 CTA"})
    if plan.get("plan_id") is None:
        blockers.append({"level": "warning", "title": "暂无统一资金分配计划", "detail": "请先生成调仓计划"})
    if _safe_float(account.get("cash")) < 0:
        blockers.append({"level": "error", "title": "现金余额为负", "detail": "请核对现金流水或纸交易账本"})
    return blockers


def _next_action(
    health: dict[str, Any],
    operation_summary: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    if health.get("blocking"):
        return {"label": "等待数据更新", "href": "/health", "enabled": False}
    if any(item.get("title") == "暂无统一资金分配计划" for item in blockers):
        return {"label": "开始收盘检查", "job_key": "daily_close_workflow", "enabled": True}
    if int(operation_summary.get("operation_count") or 0) > 0:
        return {"label": "查看调仓计划", "href": "/rebalance", "enabled": True}
    return {"label": "查看组合体检", "href": "/portfolio", "enabled": True}


def _group_rebalance_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {"executable": [], "confirm": [], "deferred": []}
    for item in items:
        normalized = {
            **item,
            "bucket_reason": _bucket_reason(item),
            "display_name": item.get("instrument_id"),
        }
        action = str(item.get("action") or "").upper()
        mode = str(item.get("execution_mode") or "").upper()
        if action in {"HOLD", "PAUSE"}:
            groups["deferred"].append(normalized)
        elif mode == "BUDGET":
            groups["executable"].append(normalized)
        else:
            groups["confirm"].append(normalized)
    return groups


def _bucket_reason(item: dict[str, Any]) -> str:
    action = str(item.get("action") or "").upper()
    mode = str(item.get("execution_mode") or "").upper()
    if action in {"HOLD", "PAUSE"}:
        return str(item.get("reason") or "无需执行")
    if mode == "MANUAL":
        return "需要手动确认基金或现金操作"
    return "预算内可执行"


def _load_signal_conflicts(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        WITH latest AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        )
        SELECT symbol,
               COUNT(DISTINCT side) AS side_count,
               STRING_AGG(DISTINCT side, ',') AS sides,
               COUNT(*) AS signal_count
        FROM signals, latest
        WHERE CAST(signal_ts AS DATE) = latest.signal_date
          AND status = 'ACTIVE'
        GROUP BY symbol
        HAVING COUNT(DISTINCT side) > 1
        ORDER BY signal_count DESC, symbol
        LIMIT 20
        """
    ).fetchdf()
    return _records(df)


def _load_one_lot_gaps(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        WITH latest_signal AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        ),
        latest_price AS (
            SELECT symbol, close
            FROM daily_price
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        )
        SELECT s.symbol,
               COALESCE(si.name, s.symbol) AS name,
               lp.close * 100 AS one_lot_cash,
               s.confidence,
               s.model_name
        FROM signals s
        JOIN latest_signal ls ON CAST(s.signal_ts AS DATE) = ls.signal_date
        LEFT JOIN latest_price lp ON s.symbol = lp.symbol
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.side = 'BUY'
          AND s.status = 'ACTIVE'
          AND lp.close IS NOT NULL
        ORDER BY one_lot_cash DESC
        LIMIT 20
        """
    ).fetchdf()
    return _records(df)


def _load_latest_holdings(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        WITH latest AS (
            SELECT strategy_name, MAX(trade_date) AS trade_date
            FROM paper_positions
            GROUP BY strategy_name
        )
        SELECT p.strategy_name, p.trade_date, p.symbol, COALESCE(si.name, p.symbol) AS name,
               p.quantity, p.avg_cost, p.current_price, p.market_value, p.pnl, p.pnl_pct, p.weight,
               si.industry, si.market_cap
        FROM paper_positions p
        JOIN latest ON p.strategy_name = latest.strategy_name AND p.trade_date = latest.trade_date
        LEFT JOIN stock_info si ON p.symbol = si.symbol
        WHERE COALESCE(p.quantity, 0) > 0
        ORDER BY p.market_value DESC, p.symbol
        LIMIT 100
        """
    ).fetchdf()
    return _records(df)


def _safe_exposure_snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    try:
        snapshot = load_exposure_snapshot(conn)
        return {
            "industry": _records(snapshot.get("industry", pd.DataFrame())),
            "size": _records(snapshot.get("size", pd.DataFrame())),
            "summary": _first_record(snapshot.get("summary", pd.DataFrame())),
            "warnings": _records(snapshot.get("warnings", pd.DataFrame())),
        }
    except Exception as exc:
        return {
            "industry": [],
            "size": [],
            "summary": {},
            "warnings": [{"level": "warning", "metric": "exposure", "message": f"暴露计算暂不可用：{exc}"}],
        }


def _safe_signal_outcomes(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    try:
        snapshot = load_signal_outcome_snapshot(conn, limit=30)
        return {
            "summary": _records(snapshot.get("summary", pd.DataFrame())),
            "monthly": _records(snapshot.get("monthly", pd.DataFrame())),
            "detail": _records(snapshot.get("detail", pd.DataFrame()).head(30)),
        }
    except Exception as exc:
        return {"summary": [], "monthly": [], "detail": [], "error": str(exc)}


def _load_data_sources(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "data_update_runs"):
        return []
    df = conn.execute(
        """
        SELECT source, market, operation, status, started_at, ended_at, attempted, updated, failed, message
        FROM data_update_runs
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        LIMIT 20
        """
    ).fetchdf()
    return _records(df)


def _load_field_coverage(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    latest = _latest_trade_date(conn)
    if latest is None:
        return []
    columns = ["industry", "market_cap", "pe_ttm", "pb"]
    rows = []
    total = conn.execute(
        """
        SELECT COUNT(DISTINCT dp.symbol)
        FROM daily_price dp
        LEFT JOIN stock_info si ON dp.symbol = si.symbol
        WHERE dp.trade_date = ?
        """,
        [latest],
    ).fetchone()[0]
    for field in columns:
        if field in {"industry", "market_cap"}:
            expr = f"si.{field}"
        else:
            expr = f"dp.{field}"
        covered = conn.execute(
            f"""
            SELECT COUNT(DISTINCT dp.symbol)
            FROM daily_price dp
            LEFT JOIN stock_info si ON dp.symbol = si.symbol
            WHERE dp.trade_date = ?
              AND {expr} IS NOT NULL
            """,
            [latest],
        ).fetchone()[0]
        rows.append({"field": field, "covered": int(covered), "total": int(total or 0), "coverage": covered / total if total else 0.0})
    return rows


def _load_qlib_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    production = _load_production_model(conn)
    recent_ic = _load_recent_ic(conn)
    return {
        "production_available": bool(production),
        "production_model": production,
        "recent_ic": recent_ic,
    }


def _load_production_model(conn: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not _table_exists(conn, "qlib_model_registry"):
        return None
    row = conn.execute(
        """
        SELECT model_version, experiment_id, model_name, status, market, published_at, metrics_json
        FROM qlib_model_registry
        WHERE status = 'production'
        ORDER BY published_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    keys = ["model_version", "experiment_id", "model_name", "status", "market", "published_at", "metrics_json"]
    model = _jsonable(dict(zip(keys, row)))
    model["metrics"] = _loads_json(model.pop("metrics_json", None))
    return model


def _production_model_version(conn: duckdb.DuckDBPyConnection) -> str | None:
    model = _load_production_model(conn)
    return model.get("model_version") if model else None


def _load_recent_experiments(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "qlib_experiments"):
        return []
    df = conn.execute(
        """
        SELECT experiment_id, model_name, model_version, mode, status, started_at, ended_at, metrics_json, error_message
        FROM qlib_experiments
        ORDER BY started_at DESC NULLS LAST
        LIMIT 12
        """
    ).fetchdf()
    records = _records(df)
    for row in records:
        row["metrics"] = _loads_json(row.pop("metrics_json", None))
    return records


def _load_recent_ic(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    if not _table_exists(conn, "qlib_daily_metrics"):
        return {"ic": None, "rank_ic": None, "icir": None, "sample_days": 0}
    df = conn.execute(
        """
        SELECT ic, rank_ic
        FROM qlib_daily_metrics
        WHERE ic IS NOT NULL OR rank_ic IS NOT NULL
        ORDER BY metric_date DESC
        LIMIT 60
        """
    ).fetchdf()
    if df.empty:
        return {"ic": None, "rank_ic": None, "icir": None, "sample_days": 0}
    ic = pd.to_numeric(df["ic"], errors="coerce").dropna()
    rank_ic = pd.to_numeric(df["rank_ic"], errors="coerce").dropna()
    icir = float(ic.mean() / ic.std()) if len(ic) > 1 and float(ic.std() or 0) != 0 else None
    return {
        "ic": _nullable_float(ic.mean() if len(ic) else None),
        "rank_ic": _nullable_float(rank_ic.mean() if len(rank_ic) else None),
        "icir": _nullable_float(icir),
        "sample_days": int(max(len(ic), len(rank_ic))),
    }


def _load_portana_status() -> dict[str, Any]:
    runs_dir = Path("runs")
    artifacts = sorted(runs_dir.glob("**/portana.html")) if runs_dir.exists() else []
    latest = artifacts[-1] if artifacts else None
    return {"available": latest is not None, "artifact_path": str(latest) if latest else None}


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchone()[0]
    )


def _job_to_dict(run: Any) -> dict[str, Any] | None:
    if run is None:
        return None
    data = run.data if hasattr(run, "data") else run
    return _jsonable(dict(data))


def _records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return [_jsonable(row) for row in df.to_dict(orient="records")]


def _first_record(df: pd.DataFrame | None) -> dict[str, Any]:
    records = _records(df)
    return records[0] if records else {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if pd.isna(value) if value is not None and not isinstance(value, (list, dict, tuple)) else False:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _safe_float(value: Any) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _nullable_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        val = float(value)
        return None if math.isnan(val) or math.isinf(val) else val
    except Exception:
        return None


def _date_to_iso(value: Any) -> str | None:
    return _jsonable(value) if value is not None else None


def _coerce_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)


def _loads_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {}
