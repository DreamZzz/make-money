"""Service layer for the Dashboard V2 retail operating cockpit.

The API calls this module instead of embedding SQL in React components.  The
queries here intentionally summarize existing domain tables into stable
front-end contracts while keeping writes limited to auditable, low-risk actions.
"""
from __future__ import annotations

import json
import math
import plistlib
import re
import subprocess
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.config import PROJECT_ROOT, load_config
from src.dashboard import job_manager
from src.dashboard.signal_outcome_service import load_signal_outcome_snapshot
from src.data_pipeline.loader import get_connection, init_db
from src.index_funds.performance import add_snapshot
from src.portfolio.cashbook import add_cashflow
from src.portfolio.exposure_monitor import load_exposure_snapshot


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
            latest_job = _latest_operational_job_run()
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
            thresholds = _load_execution_thresholds()
            one_lot_gaps = _load_one_lot_gaps(conn, thresholds)
            threshold_filtered_count = _load_threshold_filtered_buy_candidate_count(conn, thresholds)
            satellite_budget = _safe_float(plan.get("satellite_budget"))
            sell_signals = _load_active_holding_sell_signals(conn)
            sell_release_estimate = sum(_safe_float(row.get("estimated_release_cash")) for row in sell_signals)
            effective_satellite_budget = satellite_budget + sell_release_estimate
            summary["funding_gap"] = max(float(summary["cash_required"] or 0) - float(plan["cash"] or 0), 0.0)
            return {
                "plan_id": plan["plan_id"],
                "plan_date": _date_to_iso(plan["plan_date"]),
                "summary": summary,
                "groups": groups,
                "sell_signals": sell_signals,
                "conflicts": _load_signal_conflicts(conn),
                "one_lot_gaps": one_lot_gaps,
                "satellite_candidates": _build_satellite_candidate_context(
                    one_lot_gaps,
                    effective_satellite_budget,
                    thresholds,
                    threshold_filtered_count=threshold_filtered_count,
                    base_budget=satellite_budget,
                    sell_release_estimate=sell_release_estimate,
                ),
                "evidence": {
                    "data_date": _latest_trade_date(conn),
                    "signal_date": _latest_signal_date(conn),
                    "cost_model": "paper_engine_t1_open_with_fee_and_lot",
                    "budget": {
                        "core_budget": _safe_float(plan.get("core_budget")),
                        "satellite_budget": satellite_budget,
                        "satellite_sell_release_estimate": round(sell_release_estimate, 2),
                        "satellite_effective_buy_budget": round(effective_satellite_budget, 2),
                    },
                },
            }

    def build_portfolio_snapshot(self) -> dict[str, Any]:
        with self._managed_connection(read_only=True) as conn:
            account = _load_account_summary(conn)
            holdings = _load_latest_holdings(conn)
            exposure = _safe_exposure_snapshot(conn)
            outcomes = _safe_signal_outcomes(conn)
            risk_alerts = _build_actionable_risk_alerts(exposure["warnings"], holdings, exposure)
            return {
                "account": account,
                "holdings": holdings,
                "risk_alerts": risk_alerts,
                "exposure": {
                    "industry": exposure["industry"],
                    "size": exposure["size"],
                    "summary": exposure["summary"],
                    "insights": _build_exposure_insights(exposure, holdings),
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
            scheduled_history = _load_scheduled_job_history()
            latest_run = _latest_scheduled_job_run(scheduled_history)
            failure = _scheduled_failure_diagnostic(latest_run)
            return {
                **health,
                "latest_quote_date": _latest_trade_date(conn),
                "data_sources": _load_data_sources(conn),
                "field_coverage": _load_field_coverage(conn),
                "scheduled_jobs": _load_scheduled_jobs(),
                "scheduled_job_history": scheduled_history,
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
        self.reject_job_start(job_key)
        raise PermissionError(f"Dashboard V2 只展示定时任务状态，不允许启动任务：{job_key}")

    def reject_job_start(self, job_key: str) -> None:
        self._record_audit(
            "job.start",
            {"job_key": job_key},
            "rejected",
            error_message="Dashboard V2 is read-only for scheduled jobs",
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
        SELECT api.sleeve,
               api.instrument_type,
               api.instrument_id,
               CASE
                   WHEN api.instrument_type = 'index_fund' THEN fi.name
                   WHEN api.instrument_type IN ('stock', 'stock_strategy') THEN si.name
                   WHEN api.instrument_id = 'core' THEN 'Core 指数基金池'
                   WHEN api.instrument_id = 'satellite' THEN 'Satellite 个股策略池'
                   ELSE COALESCE(fi.name, si.name)
               END AS instrument_name,
               CASE
                   WHEN api.instrument_type = 'index_fund' AND fi.name IS NOT NULL THEN fi.name || '（' || api.instrument_id || '）'
                   WHEN api.instrument_type IN ('stock', 'stock_strategy') AND si.name IS NOT NULL THEN si.name || '（' || api.instrument_id || '）'
                   WHEN api.instrument_id = 'core' THEN 'Core 指数基金池'
                   WHEN api.instrument_id = 'satellite' THEN 'Satellite 个股策略池'
                   ELSE api.instrument_id
               END AS display_name,
               api.action,
               api.current_value,
               api.target_value,
               api.budget_delta,
               api.execution_mode,
               api.expected_cash,
               api.cash_effect,
               api.budget_consumption,
               api.priority,
               api.reason
        FROM allocation_plan_items api
        LEFT JOIN stock_info si ON api.instrument_id = si.symbol
        LEFT JOIN fund_info fi ON api.instrument_id = fi.fund_code
        WHERE api.plan_id = ?
        ORDER BY COALESCE(api.priority, 999), api.sleeve, api.instrument_type, api.instrument_id
        """,
        [plan["plan_id"]],
    ).fetchdf()
    plan["items"] = _records(df)
    return plan


def _build_operation_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [item for item in items if _is_trade_actionable(item)]
    cash_required = sum(_cash_outflow(item) for item in actionable)
    return {
        "operation_count": len(actionable),
        "cash_required": round(cash_required, 2),
        "estimated_minutes": max(len(actionable) * 6, 0),
        "buy_count": sum(1 for item in actionable if str(item.get("action") or "").upper() in {"BUY", "ADD"} and _cash_outflow(item) > 0),
        "reduce_count": sum(1 for item in actionable if str(item.get("action") or "").upper() in {"REDUCE", "SELL"} and _cash_inflow(item) > 0),
    }


def _load_execution_thresholds() -> dict[str, float]:
    try:
        portfolio_cfg = load_config().get("portfolio", {})
    except Exception:
        portfolio_cfg = {}
    return {
        "min_confidence": max(float(portfolio_cfg.get("min_rebalance_buy_confidence", 0.75)), 0.0),
        "min_rank_score": max(float(portfolio_cfg.get("min_rebalance_buy_rank_score", 0.50)), 0.0),
    }


def _build_satellite_candidate_context(
    rows: list[dict[str, Any]],
    satellite_budget: float,
    thresholds: dict[str, float] | None = None,
    threshold_filtered_count: int = 0,
    base_budget: float | None = None,
    sell_release_estimate: float = 0.0,
) -> dict[str, Any]:
    thresholds = thresholds or _load_execution_thresholds()
    enriched_rows: list[dict[str, Any]] = []
    covered_count = 0
    executable_count = 0
    budget_blocked_count = 0
    max_one_lot_cash = 0.0
    for row in rows:
        one_lot_cash = _safe_float(row.get("one_lot_cash"))
        max_one_lot_cash = max(max_one_lot_cash, one_lot_cash)
        is_covered = one_lot_cash > 0 and one_lot_cash <= satellite_budget
        if is_covered:
            covered_count += 1
        budget_gap = max(one_lot_cash - satellite_budget, 0.0)
        if is_covered:
            execution_status = "executable_candidate"
            execution_label = "过门槛且预算够"
            decision = "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。"
            executable_count += 1
        else:
            execution_status = "budget_blocked"
            execution_label = "过门槛但预算不足"
            decision = "高分候选被一手资金门槛挡住，默认不操作；可追加 Satellite 预算或等待更低门槛候选。"
            budget_blocked_count += 1
        enriched_rows.append({
            **row,
            "budget": round(satellite_budget, 2),
            "budget_gap": round(budget_gap, 2),
            "budget_status": "covered" if is_covered else "over_budget",
            "budget_status_label": "预算可覆盖" if is_covered else "预算不足",
            "execution_status": execution_status,
            "execution_status_label": execution_label,
            "decision": decision,
        })

    candidate_count = len(rows)
    over_budget_count = candidate_count - covered_count
    if candidate_count == 0:
        decision_hint = "暂无股票 BUY 候选；Satellite 预算不会触发个股买入。"
    elif executable_count:
        decision_hint = f"优先关注 {executable_count} 只预算够且过门槛的候选；运行纸交易后还会继续经过风控、换手和可交易性检查。"
    elif budget_blocked_count:
        decision_hint = f"{budget_blocked_count} 只高分候选被一手资金门槛挡住；默认不操作，可追加 Satellite 预算或等待更低门槛候选。"
    else:
        decision_hint = "低于执行门槛的 BUY 信号已前置过滤；当前没有可进入纸交易队列的股票候选。"

    enriched_rows.sort(key=_satellite_candidate_sort_key)

    return {
        "budget": round(satellite_budget, 2),
        "base_budget": round(_safe_float(base_budget if base_budget is not None else satellite_budget), 2),
        "sell_release_estimate": round(_safe_float(sell_release_estimate), 2),
        "candidate_count": candidate_count,
        "covered_count": covered_count,
        "over_budget_count": over_budget_count,
        "executable_count": executable_count,
        "budget_blocked_count": budget_blocked_count,
        "threshold_blocked_count": int(threshold_filtered_count),
        "max_one_lot_cash": round(max_one_lot_cash, 2),
        "decision_hint": decision_hint,
        "thresholds": thresholds,
        "rows": enriched_rows,
    }


def _satellite_candidate_sort_key(row: dict[str, Any]) -> tuple[int, float, float, float]:
    status_order = {"executable_candidate": 0, "budget_blocked": 1, "below_threshold": 2}
    return (
        status_order.get(str(row.get("execution_status")), 9),
        -_safe_float(row.get("rank_score")),
        -_safe_float(row.get("confidence")),
        -_safe_float(row.get("one_lot_cash")),
    )


def _is_trade_actionable(item: dict[str, Any]) -> bool:
    action = str(item.get("action") or "").upper()
    if action in {"HOLD", "PAUSE"}:
        return False
    if _is_budget_item(item):
        return False
    return _cash_outflow(item) > 0 or _cash_inflow(item) > 0


def _cash_outflow(item: dict[str, Any]) -> float:
    cash_effect = _safe_float(item.get("cash_effect"))
    if cash_effect < 0:
        return abs(cash_effect)
    action = str(item.get("action") or "").upper()
    if action in {"BUY", "ADD"}:
        return max(_safe_float(item.get("expected_cash")), 0.0)
    return 0.0


def _cash_inflow(item: dict[str, Any]) -> float:
    cash_effect = _safe_float(item.get("cash_effect"))
    if cash_effect > 0:
        return cash_effect
    action = str(item.get("action") or "").upper()
    if action in {"REDUCE", "SELL"}:
        return max(_safe_float(item.get("expected_cash")), 0.0)
    return 0.0


def _build_health_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    messages: list[str] = []
    latest_date = _latest_trade_date(conn)
    if latest_date is None:
        messages.append("daily_price 尚无行情日期")
    production = _production_model_version(conn)
    if production is None:
        messages.append("Qlib production 模型不可用")
    model_monitor = _load_model_monitor_health(conn)
    if model_monitor["active_alert_count"]:
        messages.append(f"模型监控告警：{model_monitor['active_alert_count']} 条")
    signal_freshness = _load_alpha158_signal_freshness(conn)
    if signal_freshness.get("blocking"):
        messages.append(str(signal_freshness.get("message") or "Alpha158 production 信号需刷新"))
    status = "ok" if not messages else ("failed" if any("失败" in msg for msg in messages) else "degraded")
    if model_monitor["status"] == "failed":
        status = "failed"
    elif model_monitor["status"] == "degraded" and status == "ok":
        status = "degraded"
    return {
        "status": status,
        "label": {"ok": "数据可用", "degraded": "数据需确认", "failed": "任务失败"}[status],
        "blocking": status == "failed" or latest_date is None or bool(signal_freshness.get("blocking")),
        "messages": messages,
        "model_monitor": model_monitor,
        "signal_freshness": signal_freshness,
    }


def _load_model_monitor_health(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    if not _table_exists(conn, "model_monitor_alerts"):
        return {"status": "ok", "active_alert_count": 0, "alerts": []}
    df = conn.execute("""
        SELECT alert_id, model_name, model_version, experiment_id, alert_date,
               severity, metric_name, observed_value, threshold_value, status,
               message, context_json, updated_at
        FROM model_monitor_alerts
        WHERE status = 'ACTIVE'
        ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END,
                 updated_at DESC NULLS LAST
        LIMIT 20
    """).fetchdf()
    alerts = _records(df)
    for alert in alerts:
        alert["context"] = _loads_json(alert.pop("context_json", None))
    status = "ok"
    if any(alert.get("severity") == "CRITICAL" for alert in alerts):
        status = "failed"
    elif alerts:
        status = "degraded"
    return {"status": status, "active_alert_count": len(alerts), "alerts": alerts}


def _load_alpha158_signal_freshness(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    production = _load_production_model(conn)
    if not production:
        return {"status": "unknown", "blocking": False, "message": None}
    if not _table_exists(conn, "qlib_predictions") or not _table_exists(conn, "signals"):
        return {"status": "unknown", "blocking": True, "message": "Alpha158 production 预测或信号表不可用"}

    model_version = str(production.get("model_version") or "")
    experiment_id = str(production.get("experiment_id") or "")
    prediction = conn.execute(
        """
        SELECT MAX(prediction_date) AS latest_prediction_date, COUNT(*) AS prediction_count
        FROM qlib_predictions
        WHERE model_version = ? OR experiment_id = ?
        """,
        [model_version, experiment_id],
    ).fetchone()
    latest_prediction_date = prediction[0] if prediction else None
    prediction_count = int(prediction[1] or 0) if prediction else 0

    signal = conn.execute(
        """
        SELECT MAX(CAST(signal_ts AS DATE)) AS latest_signal_date,
               COUNT(*) AS signal_count,
               SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
               SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END) AS sell_count
        FROM signals
        WHERE model_version = ?
          AND model_name LIKE 'alpha158%'
        """,
        [model_version],
    ).fetchone()
    latest_signal_date = signal[0] if signal else None
    signal_count = int(signal[1] or 0) if signal else 0
    buy_count = int(signal[2] or 0) if signal else 0
    sell_count = int(signal[3] or 0) if signal else 0

    if latest_prediction_date is None:
        return {
            "status": "missing_prediction",
            "blocking": True,
            "message": "Alpha158 production 模型尚未生成预测截面",
            "model_version": model_version,
            "experiment_id": experiment_id,
            "latest_prediction_date": None,
            "latest_signal_date": _date_to_iso(latest_signal_date),
            "prediction_count": prediction_count,
            "signal_count": signal_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
        }

    if latest_signal_date is None or latest_signal_date < latest_prediction_date:
        prediction_label = _date_to_iso(latest_prediction_date) or "-"
        signal_label = _date_to_iso(latest_signal_date) or "-"
        return {
            "status": "stale",
            "blocking": True,
            "message": f"Alpha158 production 信号滞后：预测日期 {prediction_label}，信号日期 {signal_label}",
            "model_version": model_version,
            "experiment_id": experiment_id,
            "latest_prediction_date": prediction_label,
            "latest_signal_date": _date_to_iso(latest_signal_date),
            "prediction_count": prediction_count,
            "signal_count": signal_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
        }

    return {
        "status": "ok",
        "blocking": False,
        "message": None,
        "model_version": model_version,
        "experiment_id": experiment_id,
        "latest_prediction_date": _date_to_iso(latest_prediction_date),
        "latest_signal_date": _date_to_iso(latest_signal_date),
        "prediction_count": prediction_count,
        "signal_count": signal_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
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
        return {"label": "查看任务状态", "href": "/health", "enabled": True}
    if int(operation_summary.get("operation_count") or 0) > 0:
        return {"label": "查看调仓计划", "href": "/rebalance", "enabled": True}
    return {"label": "查看组合体检", "href": "/portfolio", "enabled": True}


def _group_rebalance_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {"budget": [], "executable": [], "confirm": [], "deferred": []}
    for item in items:
        normalized = {
            **item,
            "bucket_reason": _bucket_reason(item),
            "display_name": item.get("display_name") or _fallback_instrument_display_name(item),
        }
        action = str(item.get("action") or "").upper()
        mode = str(item.get("execution_mode") or "").upper()
        if _is_budget_item(item):
            groups["budget"].append(normalized)
        elif action in {"HOLD", "PAUSE"} or not _is_trade_actionable(item):
            groups["deferred"].append(normalized)
        elif mode == "BUDGET":
            groups["executable"].append(normalized)
        else:
            groups["confirm"].append(normalized)
    return groups


def _is_budget_item(item: dict[str, Any]) -> bool:
    return (
        str(item.get("instrument_type") or "").lower() == "sleeve"
        or str(item.get("execution_mode") or "").upper() == "BUDGET"
    )


def _fallback_instrument_display_name(item: dict[str, Any]) -> str:
    instrument_id = str(item.get("instrument_id") or "-")
    instrument_name = item.get("instrument_name")
    if instrument_name and str(instrument_name) != instrument_id:
        return f"{instrument_name}（{instrument_id}）"
    return instrument_id


def _bucket_reason(item: dict[str, Any]) -> str:
    action = str(item.get("action") or "").upper()
    mode = str(item.get("execution_mode") or "").upper()
    if _is_budget_item(item):
        return "资金池预算，不是交易指令；具体买卖以标的行和纸交易执行为准"
    if action in {"HOLD", "PAUSE"}:
        return str(item.get("reason") or "无需执行")
    if action in {"REDUCE", "SELL"} and not _is_trade_actionable(item):
        return str(item.get("reason") or "减仓信号未形成实际交易金额，暂不操作")
    if mode == "MANUAL":
        return "需要手动确认基金或现金操作"
    return "预算内可执行"


def _load_signal_conflicts(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        WITH latest AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        )
        SELECT s.symbol,
               COALESCE(si.name, s.symbol) AS name,
               CASE
                   WHEN si.name IS NOT NULL THEN si.name || '（' || s.symbol || '）'
                   ELSE s.symbol
               END AS display_name,
               COUNT(DISTINCT side) AS side_count,
               STRING_AGG(DISTINCT side, ',' ORDER BY side) AS sides,
               COUNT(*) AS signal_count
        FROM signals s
        JOIN latest ON CAST(s.signal_ts AS DATE) = latest.signal_date
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.status = 'ACTIVE'
        GROUP BY s.symbol, si.name
        HAVING COUNT(DISTINCT side) > 1
        ORDER BY signal_count DESC, s.symbol
        LIMIT 20
        """
    ).fetchdf()
    return _records(df)


def _load_active_holding_sell_signals(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "paper_positions") or not _table_exists(conn, "signals"):
        return []
    from src.portfolio.current_holdings import current_positions_cte

    current_positions, current_position_params = current_positions_cte()
    df = conn.execute(
        f"""
        WITH latest_signal AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        ),
        {current_positions},
        active_positions AS (
            SELECT strategy_name, symbol, quantity, market_value, pnl, pnl_pct
            FROM current_positions
        ),
        sell_signals AS (
            SELECT s.symbol,
                   MAX(s.confidence) AS confidence,
                   MAX(s.score) AS score,
                   STRING_AGG(DISTINCT s.model_name, ',' ORDER BY s.model_name) AS model_name,
                   COUNT(DISTINCT s.model_name || ':' || COALESCE(s.model_version, '')) AS signal_count,
                   MAX(CAST(s.signal_ts AS DATE)) AS signal_date
            FROM signals s
            JOIN latest_signal ls ON CAST(s.signal_ts AS DATE) = ls.signal_date
            WHERE s.side = 'SELL'
              AND s.status = 'ACTIVE'
            GROUP BY s.symbol
        )
        SELECT ap.symbol,
               COALESCE(si.name, ap.symbol) AS name,
               CASE
                   WHEN si.name IS NOT NULL THEN si.name || '（' || ap.symbol || '）'
                   ELSE ap.symbol
               END AS display_name,
               COALESCE(si.country, 'CN') AS market,
               ap.strategy_name,
               ap.quantity,
               ap.market_value,
               ap.pnl,
               ap.pnl_pct,
               ss.confidence,
               ss.score,
               ss.model_name,
               ss.signal_count,
               STRFTIME(ss.signal_date, '%Y-%m-%d') AS signal_date,
               CASE
                   WHEN ap.pnl_pct <= -0.08 THEN '已触发 SELL，且浮亏超过 8%；建议优先在纸交易预览中确认卖出。'
                   WHEN ap.pnl_pct < 0 THEN '已触发 SELL，当前浮亏；建议在纸交易预览中确认卖出。'
                   ELSE '已触发 SELL；建议在纸交易预览中确认卖出或减仓。'
               END AS decision
        FROM active_positions ap
        JOIN sell_signals ss ON ap.symbol = ss.symbol
        LEFT JOIN stock_info si ON ap.symbol = si.symbol
        ORDER BY ap.pnl_pct ASC NULLS LAST, ss.confidence DESC, ap.market_value DESC
        LIMIT 50
        """,
        current_position_params,
    ).fetchdf()
    records = _records(df)
    for row in records:
        row["estimated_release_cash"] = round(_estimate_sell_release_cash(row.get("market_value"), row.get("market")), 2)
    return records


def _estimate_sell_release_cash(market_value: Any, market: Any) -> float:
    value = max(_safe_float(market_value), 0.0)
    if value <= 0:
        return 0.0
    try:
        markets = load_config().get("markets", {})
    except Exception:
        markets = {}
    market_key = "hk" if str(market or "").upper() == "HK" else "cn"
    cost_cfg = markets.get(market_key, markets.get("cn", {}))
    commission = float(cost_cfg.get("commission_rate", 0.00025))
    stamp_duty = float(cost_cfg.get("stamp_duty_rate", 0.001))
    min_fee = 10.0 if market_key == "hk" else 5.0
    fee = max(value * (commission + stamp_duty), min_fee)
    return max(value - fee, 0.0)


def _load_one_lot_gaps(
    conn: duckdb.DuckDBPyConnection,
    thresholds: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    thresholds = thresholds or _load_execution_thresholds()
    min_confidence = float(thresholds.get("min_confidence", 0.75))
    min_rank_score = float(thresholds.get("min_rank_score", 0.50))
    df = conn.execute(
        """
        WITH latest_signal AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        ),
        latest_price AS (
            SELECT symbol, close
            FROM daily_price
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        ),
        grouped AS (
            SELECT s.symbol,
                   COALESCE(si.name, s.symbol) AS name,
                   CASE
                       WHEN si.name IS NOT NULL THEN si.name || '（' || s.symbol || '）'
                       ELSE s.symbol
                   END AS display_name,
                   MAX(lp.close) * 100 AS one_lot_cash,
                   MAX(s.confidence) AS confidence,
                   MAX(s.score) AS score,
                   MAX(s.confidence * CASE WHEN COALESCE(s.score, 0) > 0 THEN s.score ELSE 0 END) AS rank_score,
                   BOOL_OR(
                       COALESCE(s.confidence, 0) >= ?
                       AND s.confidence * CASE WHEN COALESCE(s.score, 0) > 0 THEN s.score ELSE 0 END >= ?
                   ) AS passes_execution_threshold,
                   STRING_AGG(DISTINCT s.model_name, ',' ORDER BY s.model_name) AS model_name,
                   COUNT(DISTINCT s.model_name || ':' || COALESCE(s.model_version, '')) AS signal_count
            FROM signals s
            JOIN latest_signal ls ON CAST(s.signal_ts AS DATE) = ls.signal_date
            LEFT JOIN latest_price lp ON s.symbol = lp.symbol
            LEFT JOIN stock_info si ON s.symbol = si.symbol
            WHERE s.side = 'BUY'
              AND s.status = 'ACTIVE'
              AND lp.close IS NOT NULL
            GROUP BY s.symbol, si.name
        )
        SELECT *
        FROM grouped
        WHERE passes_execution_threshold
        ORDER BY passes_execution_threshold DESC, rank_score DESC, confidence DESC, one_lot_cash DESC
        LIMIT 20
        """,
        [min_confidence, min_rank_score],
    ).fetchdf()
    return _records(df)


def _load_threshold_filtered_buy_candidate_count(
    conn: duckdb.DuckDBPyConnection,
    thresholds: dict[str, float] | None = None,
) -> int:
    thresholds = thresholds or _load_execution_thresholds()
    min_confidence = float(thresholds.get("min_confidence", 0.75))
    min_rank_score = float(thresholds.get("min_rank_score", 0.50))
    row = conn.execute(
        """
        WITH latest_signal AS (
            SELECT MAX(CAST(signal_ts AS DATE)) AS signal_date FROM signals
        ),
        latest_price AS (
            SELECT symbol, close
            FROM daily_price
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        ),
        grouped AS (
            SELECT s.symbol,
                   BOOL_OR(
                       COALESCE(s.confidence, 0) >= ?
                       AND s.confidence * CASE WHEN COALESCE(s.score, 0) > 0 THEN s.score ELSE 0 END >= ?
                   ) AS passes_execution_threshold
            FROM signals s
            JOIN latest_signal ls ON CAST(s.signal_ts AS DATE) = ls.signal_date
            LEFT JOIN latest_price lp ON s.symbol = lp.symbol
            WHERE s.side = 'BUY'
              AND s.status = 'ACTIVE'
              AND lp.close IS NOT NULL
            GROUP BY s.symbol
        )
        SELECT COUNT(*)
        FROM grouped
        WHERE NOT passes_execution_threshold
        """,
        [min_confidence, min_rank_score],
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _load_latest_holdings(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    from src.portfolio.current_holdings import current_positions_cte

    current_positions, current_position_params = current_positions_cte()
    df = conn.execute(
        f"""
        WITH {current_positions},
        first_positive AS (
            SELECT strategy_name, symbol, MIN(trade_date) AS first_trade_date
            FROM paper_positions
            WHERE COALESCE(quantity, 0) > 0
            GROUP BY strategy_name, symbol
        ),
        prior_7d AS (
            SELECT cp.strategy_name, cp.symbol, pp.weight AS prior_weight_7d,
                   ROW_NUMBER() OVER (
                       PARTITION BY cp.strategy_name, cp.symbol
                       ORDER BY pp.trade_date DESC
                   ) AS rn
            FROM current_positions cp
            LEFT JOIN paper_positions pp
              ON pp.strategy_name = cp.strategy_name
             AND pp.symbol = cp.symbol
             AND pp.trade_date <= cp.trade_date - INTERVAL 7 DAY
        ),
        prior_20d AS (
            SELECT cp.strategy_name, cp.symbol, pp.weight AS prior_weight_20d,
                   ROW_NUMBER() OVER (
                       PARTITION BY cp.strategy_name, cp.symbol
                       ORDER BY pp.trade_date DESC
                   ) AS rn
            FROM current_positions cp
            LEFT JOIN paper_positions pp
              ON pp.strategy_name = cp.strategy_name
             AND pp.symbol = cp.symbol
             AND pp.trade_date <= cp.trade_date - INTERVAL 20 DAY
        ),
        latest_price AS (
            SELECT symbol, pe_ttm, pb
            FROM daily_price
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        ),
        latest_prediction AS (
            SELECT symbol, prediction_date, rank, confidence, score, model_version
            FROM qlib_predictions
            WHERE model_name = 'alpha158'
              AND mode = 'production_inference'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY prediction_date DESC, selected DESC, rank ASC
            ) = 1
        ),
        latest_signal_ts AS (
            SELECT symbol, MAX(signal_ts) AS signal_ts
            FROM signals
            GROUP BY symbol
        ),
        latest_signal_rows AS (
            SELECT s.symbol, s.side, s.status, s.confidence, s.signal_ts
            FROM signals s
            JOIN latest_signal_ts latest
              ON s.symbol = latest.symbol
             AND s.signal_ts = latest.signal_ts
        ),
        latest_signal_side AS (
            SELECT symbol, string_agg(side, ',' ORDER BY side) AS latest_signal_side
            FROM (SELECT DISTINCT symbol, side FROM latest_signal_rows)
            GROUP BY symbol
        ),
        latest_signal_status AS (
            SELECT symbol, string_agg(status, ',' ORDER BY status) AS latest_signal_status
            FROM (SELECT DISTINCT symbol, status FROM latest_signal_rows)
            GROUP BY symbol
        ),
        latest_signal_meta AS (
            SELECT symbol, MAX(signal_ts) AS latest_signal_ts,
                   MAX(confidence) AS latest_signal_confidence,
                   COUNT(*) AS latest_signal_count
            FROM latest_signal_rows
            GROUP BY symbol
        )
        SELECT p.strategy_name, p.trade_date, p.symbol, COALESCE(si.name, p.symbol) AS name,
               CASE WHEN si.name IS NOT NULL THEN si.name || '（' || p.symbol || '）' ELSE p.symbol END AS display_name,
               p.quantity, p.avg_cost, p.current_price, p.market_value, p.pnl, p.pnl_pct, p.weight,
               si.industry, si.market_cap, lp.pe_ttm, lp.pb,
               fp.first_trade_date,
               date_diff('day', fp.first_trade_date, p.trade_date) AS holding_days,
               CASE WHEN p7.prior_weight_7d IS NULL THEN NULL ELSE ROUND(p.weight - p7.prior_weight_7d, 4) END AS weight_change_7d,
               CASE WHEN p20.prior_weight_20d IS NULL THEN NULL ELSE ROUND(p.weight - p20.prior_weight_20d, 4) END AS weight_change_20d,
               pred.prediction_date AS qlib_prediction_date,
               pred.rank AS qlib_rank,
               pred.confidence AS qlib_confidence,
               pred.score AS qlib_score,
               lss.latest_signal_side,
               lst.latest_signal_status,
               lsm.latest_signal_ts,
               lsm.latest_signal_confidence,
               lsm.latest_signal_count
        FROM current_positions p
        LEFT JOIN stock_info si ON p.symbol = si.symbol
        LEFT JOIN latest_price lp ON p.symbol = lp.symbol
        LEFT JOIN first_positive fp ON p.strategy_name = fp.strategy_name AND p.symbol = fp.symbol
        LEFT JOIN prior_7d p7 ON p.strategy_name = p7.strategy_name AND p.symbol = p7.symbol AND p7.rn = 1
        LEFT JOIN prior_20d p20 ON p.strategy_name = p20.strategy_name AND p.symbol = p20.symbol AND p20.rn = 1
        LEFT JOIN latest_prediction pred ON p.symbol = pred.symbol
        LEFT JOIN latest_signal_side lss ON p.symbol = lss.symbol
        LEFT JOIN latest_signal_status lst ON p.symbol = lst.symbol
        LEFT JOIN latest_signal_meta lsm ON p.symbol = lsm.symbol
        ORDER BY p.market_value DESC, p.symbol
        LIMIT 100
        """,
        current_position_params,
    ).fetchdf()
    records = _records(df)
    for row in records:
        row["qlib_prediction_date"] = _date_only_iso(row.get("qlib_prediction_date"))
        row["entry_strategy_label"] = _strategy_label(row.get("strategy_name"))
        alignment, reason = _qlib_alignment_for_holding(row)
        row["qlib_alignment"] = alignment
        row["qlib_alignment_reason"] = reason
    return records


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
        detail = snapshot.get("detail", pd.DataFrame())
        return {
            "summary": _records(snapshot.get("summary", pd.DataFrame())),
            "monthly": _records(snapshot.get("monthly", pd.DataFrame())),
            "detail": _records(detail.head(30)),
            "state": _build_signal_outcome_state(conn, detail),
        }
    except Exception as exc:
        return {
            "summary": [],
            "monthly": [],
            "detail": [],
            "state": {
                "status": "error",
                "message": f"信号收益跟踪暂不可用：{exc}",
                "ready_count": 0,
                "pending_count": 0,
                "total_count": 0,
                "next_ready_date": None,
            },
            "error": str(exc),
        }


def _build_actionable_risk_alerts(
    warnings: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    exposure: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for warning in warnings:
        metric = str(warning.get("metric") or "")
        affected = _affected_holdings_for_metric(metric, holdings, exposure)
        severity = str(warning.get("severity") or warning.get("status") or "INFO").upper()
        alerts.append({
            **warning,
            "level": _risk_level(severity),
            "affected_holdings": affected,
            "suggested_actions": _risk_suggested_actions(metric, severity),
            "severity_reason": _risk_severity_reason(metric, affected, severity),
        })
    return alerts


def _affected_holdings_for_metric(
    metric: str,
    holdings: list[dict[str, Any]],
    exposure: dict[str, Any],
) -> list[dict[str, Any]]:
    if not holdings:
        return []
    if metric == "top1_weight":
        return [_holding_brief(max(holdings, key=lambda row: _safe_float(row.get("weight"))))]
    if metric == "top5_weight":
        return [_holding_brief(row) for row in sorted(holdings, key=lambda row: _safe_float(row.get("weight")), reverse=True)[:5]]
    if metric == "max_industry_weight":
        industry = _top_exposure_name(exposure.get("industry", []), "industry")
        return [_holding_brief(row) for row in holdings if str(row.get("industry") or "未知行业") == industry][:8]
    if metric == "unknown_industry_weight":
        return [
            _holding_brief(row)
            for row in holdings
            if str(row.get("industry") or "").strip() in {"", "未知行业", "None"}
        ][:8]
    if metric == "pe_coverage":
        return [_holding_brief(row) for row in holdings if _safe_float(row.get("pe_ttm")) <= 0][:8]
    if metric == "pb_coverage":
        return [_holding_brief(row) for row in holdings if _safe_float(row.get("pb")) <= 0][:8]
    return [_holding_brief(row) for row in sorted(holdings, key=lambda row: _safe_float(row.get("weight")), reverse=True)[:5]]


def _risk_level(severity: str) -> str:
    if severity in {"CRITICAL", "ERROR", "FAILED"}:
        return "error"
    if severity in {"WARN", "WARNING"}:
        return "warning"
    if severity == "OK":
        return "ok"
    return "info"


def _risk_suggested_actions(metric: str, severity: str) -> list[str]:
    prefix = "当前未超限；" if severity == "OK" else ""
    suggestions = {
        "top1_weight": ["不新增该标的，优先等待 SELL 或再平衡信号。"],
        "top5_weight": ["暂停新增 Top5 标的，新增仓位优先让给低相关、低权重候选。"],
        "max_industry_weight": ["暂停新增该行业，优先处理低置信度、亏损扩大或已有 SELL 信号的标的。"],
        "unknown_industry_weight": ["先补 stock_info.industry；覆盖恢复前降低未知行业标的新增优先级。"],
        "pe_coverage": ["先补 PE(TTM) 字段；覆盖不足时不要把估值便宜作为加仓理由。"],
        "pb_coverage": ["先补 PB 字段；覆盖不足时不要把低 PB 作为加仓理由。"],
    }
    return [prefix + item for item in suggestions.get(metric, ["先确认数据口径，再在下一次调仓计划中处理。"])]


def _risk_severity_reason(metric: str, affected: list[dict[str, Any]], severity: str) -> str:
    if not affected:
        return "未定位到具体持仓；请先确认持仓与 stock_info 覆盖。"
    name = str(affected[0].get("display_name") or affected[0].get("symbol") or "该标的")
    if metric == "max_industry_weight":
        industry = str(affected[0].get("industry") or "该行业")
        return f"主要由{industry}行业持仓贡献，代表标的是{name}。"
    if metric in {"pe_coverage", "pb_coverage", "unknown_industry_weight"}:
        return f"主要缺口来自{name}等 {len(affected)} 只标的。"
    if severity == "OK":
        return f"当前最大贡献标的是{name}，仍在规则范围内。"
    return f"主要由{name}贡献。"


def _build_exposure_insights(
    exposure: dict[str, Any],
    holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    industry = _top_exposure_row(exposure.get("industry", []), "industry")
    if industry:
        industry_name = str(industry.get("industry") or "未知行业")
        affected = [
            _holding_brief(row)
            for row in sorted(holdings, key=lambda item: _safe_float(item.get("weight")), reverse=True)
            if str(row.get("industry") or "未知行业") == industry_name
        ][:5]
        insights.append({
            "key": "industry",
            "title": "最大行业暴露",
            "message": f"{industry_name}权重最高，组合可能被单一行业波动牵动。",
            "value": _safe_float(industry.get("weight")),
            "benchmark_value": _safe_float(industry.get("benchmark_weight")),
            "suggested_action": "暂停新增该行业，优先处理低置信度、亏损扩大或已有 SELL 信号的标的。",
            "affected_holdings": affected,
        })

    size = _top_exposure_row(exposure.get("size", []), "size_bucket")
    if size:
        size_name = str(size.get("size_bucket") or "未知市值")
        insights.append({
            "key": "size",
            "title": "最大市值风格",
            "message": f"{size_name}占比最高；如果短期风格切换，组合波动会先反映在这类持仓上。",
            "value": _safe_float(size.get("weight")),
            "suggested_action": "新增仓位优先看与当前市值风格不同、且置信度过门槛的候选。",
            "affected_holdings": [
                _holding_brief(row)
                for row in sorted(holdings, key=lambda item: _safe_float(item.get("weight")), reverse=True)[:5]
            ],
        })

    summary = dict(exposure.get("summary") or {})
    pe_coverage = _safe_float(summary.get("pe_coverage"))
    pb_coverage = _safe_float(summary.get("pb_coverage"))
    missing_valuation = [
        _holding_brief(row)
        for row in holdings
        if _safe_float(row.get("pe_ttm")) <= 0 or _safe_float(row.get("pb")) <= 0
    ][:5]
    insights.append({
        "key": "valuation",
        "title": "估值覆盖质量",
        "message": f"PE覆盖 {pe_coverage:.1%}，PB覆盖 {pb_coverage:.1%}；覆盖不足时估值判断只能作为弱证据。",
        "pe_coverage": pe_coverage,
        "pb_coverage": pb_coverage,
        "suggested_action": "优先补齐 pe_ttm / pb 数据；缺失标的不要因为“看起来便宜”而加仓。",
        "affected_holdings": missing_valuation,
    })
    return insights


def _top_exposure_name(rows: list[dict[str, Any]], key: str) -> str | None:
    row = _top_exposure_row(rows, key)
    return str(row.get(key)) if row and row.get(key) is not None else None


def _top_exposure_row(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    valid = [row for row in rows if str(row.get(key) or "").strip()]
    if not valid:
        return None
    return max(valid, key=lambda row: _safe_float(row.get("weight")))


def _holding_brief(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "")
    display_name = row.get("display_name") or (
        f"{row.get('name')}（{symbol}）" if row.get("name") and symbol and str(row.get("name")) != symbol else symbol
    )
    return {
        "symbol": symbol,
        "name": row.get("name"),
        "display_name": display_name,
        "industry": row.get("industry"),
        "market_value": round(_safe_float(row.get("market_value")), 2),
        "weight": round(_safe_float(row.get("weight")), 4),
        "pnl_pct": _nullable_float(row.get("pnl_pct")),
        "holding_days": row.get("holding_days"),
        "latest_signal_side": row.get("latest_signal_side"),
    }


def _strategy_label(strategy_name: Any) -> str:
    labels = {
        "alpha158": "Alpha158 多因子",
        "trend_following": "趋势跟踪",
        "mean_reversion": "均值回归",
        "industry_rotation": "行业轮动",
        "value_quality": "价值质量",
    }
    name = str(strategy_name or "")
    return labels.get(name, name or "未知策略")


def _qlib_alignment_for_holding(row: dict[str, Any]) -> tuple[str, str]:
    strategy_name = str(row.get("strategy_name") or "")
    if strategy_name == "alpha158":
        return "Qlib持仓", "该持仓由 Alpha158/Qlib 策略自身产生。"

    prediction_date = _date_only_iso(row.get("qlib_prediction_date"))
    trade_date = _date_only_iso(row.get("trade_date"))
    if not prediction_date:
        return "Qlib缺失", "没有可用的 Alpha158 production 预测，不能用 Qlib 排名判断这笔持仓。"

    stale_days = 0
    if trade_date:
        try:
            stale_days = max((date.fromisoformat(trade_date) - date.fromisoformat(prediction_date)).days, 0)
        except ValueError:
            stale_days = 0
    if stale_days > 3:
        return "Qlib过期", f"Qlib 预测日期 {prediction_date}，距离持仓日期 {trade_date} 已 {stale_days} 天。"

    rank = _safe_float(row.get("qlib_rank"))
    confidence = _safe_float(row.get("qlib_confidence"))
    if rank <= 100 and confidence >= 0.45:
        return "Qlib共振", f"规则策略买入，Alpha158 排名 {int(rank)} 且置信度 {confidence:.1%}。"
    if rank > 500 or confidence < 0.45:
        return "Qlib背离", f"规则策略买入，但 Alpha158 排名 {int(rank)}、置信度 {confidence:.1%}。"
    return "Qlib中性", f"规则策略买入，Alpha158 排名 {int(rank)}、置信度 {confidence:.1%}。"


def _build_signal_outcome_state(conn: duckdb.DuckDBPyConnection, detail: pd.DataFrame) -> dict[str, Any]:
    if detail is None or detail.empty:
        return {
            "status": "empty",
            "message": "暂无信号收益数据：尚未产生可跟踪的纸交易成交；成交后需等待 T+1/T+5/T+20 到期。",
            "ready_count": 0,
            "pending_count": 0,
            "total_count": 0,
            "next_ready_date": None,
        }
    statuses = detail["status"].fillna("PENDING").astype(str).str.upper() if "status" in detail else pd.Series(dtype=str)
    ready_count = int((statuses == "READY").sum())
    pending_count = int((statuses == "PENDING").sum())
    next_ready_date = None
    if pending_count and "outcome_date" in detail:
        pending_dates = pd.to_datetime(detail.loc[statuses == "PENDING", "outcome_date"], errors="coerce").dropna()
        if not pending_dates.empty:
            next_ready_date = _date_to_iso(pending_dates.min().date())
    if ready_count:
        status = "ready"
        message = "已有成熟信号收益样本，可用于复盘模型效果。"
    elif pending_count:
        status = "pending"
        message = "信号收益正在等待 T+1/T+5/T+20 到期。"
    else:
        status = "empty"
        message = "暂无可用信号收益样本。"
    return {
        "status": status,
        "message": message,
        "ready_count": ready_count,
        "pending_count": pending_count,
        "total_count": int(len(detail)),
        "next_ready_date": next_ready_date,
    }


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


def _load_scheduled_jobs() -> list[dict[str, Any]]:
    watchdog_label = "com.quant.scheduler-watchdog"
    watchdog_plist = Path.home() / "Library" / "LaunchAgents" / f"{watchdog_label}.plist"
    watchdog_state = _load_scheduler_watchdog_state()
    jobs = [
        {
            "job_key": "daily_close",
            "label": "收盘闭环",
            "trigger": "工作日 20:00（watchdog 每 5 分钟检查）",
        },
        {
            "job_key": "open_paper_trade",
            "label": "开盘纸交易",
            "trigger": "工作日 09:40（watchdog 每 5 分钟检查）",
        },
    ]
    loaded_labels = _launchctl_labels()
    rows = []
    plist_exists = watchdog_plist.exists()
    loaded = watchdog_label in loaded_labels
    script = _script_from_launch_plist(watchdog_plist) if plist_exists else None
    plist_status = "存在" if plist_exists else "缺失"
    launch_status_label = "watchdog 已加载" if loaded else ("watchdog 未加载" if plist_exists else "缺少 watchdog 配置")
    for job in jobs:
        job_state = watchdog_state["jobs"].get(str(job["job_key"]), {})
        watchdog_status = str(job_state.get("status") or "UNKNOWN")
        status = "ok" if loaded and plist_exists and watchdog_status not in {"FAILED", "MISSED"} else "warning"
        if watchdog_status in {"FAILED", "MISSED"}:
            status = "failed"
        rows.append({
            **job,
            "launch_label": watchdog_label,
            "plist_path": str(watchdog_plist),
            "script": script,
            "status": status,
            "status_label": launch_status_label,
            "plist_status": plist_status,
            "watchdog_status": watchdog_status,
            "watchdog_status_label": job_state.get("status_label") or _scheduled_status_label(watchdog_status),
            "last_run_date": job_state.get("last_run_date"),
            "last_started_at": job_state.get("started_at"),
            "last_ended_at": job_state.get("ended_at"),
            "next_due_at": job_state.get("next_due_at"),
            "last_result": job_state.get("last_result") or job_state.get("result"),
            "action_hint": "由 StartInterval watchdog 检查执行窗口并防重复；Dashboard 只展示状态和异常提醒。",
        })
    return rows


def _load_scheduler_watchdog_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or (Path(PROJECT_ROOT) / "output" / "scheduler_state.json")
    empty = {"version": 1, "updated_at": None, "jobs": {}}
    if not state_path.exists():
        return empty
    try:
        data = json.loads(state_path.read_text(errors="replace"))
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    raw_jobs = data.get("jobs")
    if not isinstance(raw_jobs, dict):
        raw_jobs = {}
    jobs: dict[str, dict[str, Any]] = {}
    for job_key, raw_job in raw_jobs.items():
        if not isinstance(raw_job, dict):
            continue
        status = str(raw_job.get("status") or "UNKNOWN")
        jobs[str(job_key)] = {
            **raw_job,
            "status": status,
            "status_label": _scheduled_status_label(status),
            "last_result": raw_job.get("result"),
        }
    return {
        "version": data.get("version") or 1,
        "updated_at": data.get("updated_at"),
        "jobs": jobs,
    }


def _load_scheduled_job_history(limit: int = 12) -> list[dict[str, Any]]:
    output_dir = Path(PROJECT_ROOT) / "output"
    rows: list[dict[str, Any]] = []
    for job_key, job_name, filename in [
        ("daily_close", "收盘闭环", "cron.log"),
        ("open_paper_trade", "开盘纸交易", "open_trade.log"),
    ]:
        path = output_dir / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        rows.extend(_parse_scheduled_job_log(job_key, job_name, text, filename))
    rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    return rows[:limit]


def _latest_scheduled_job_run(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not history:
        return None
    row = history[0]
    status = str(row.get("status") or "UNKNOWN")
    status_label = str(row.get("status_label") or _scheduled_status_label(status))
    step = {
        "key": str(row.get("job_key") or "scheduled_job"),
        "label": f"{row.get('scheduled_time') or '-'} 定时执行",
        "status": status,
        "status_label": status_label,
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_seconds": row.get("duration_seconds"),
        "log_excerpt": row.get("result"),
    }
    return {
        "run_id": f"SCHEDULED-{row.get('job_key') or 'job'}-{str(row.get('started_at') or '').replace(' ', 'T')}",
        "job_key": row.get("job_key"),
        "job_label": row.get("job_name"),
        "job_type": "scheduled",
        "status": status,
        "status_label": status_label,
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "exit_code": 0 if status in {"SUCCEEDED", "DEGRADED"} else (None if status == "RUNNING" else 1),
        "current_step": None,
        "log_path": row.get("source_log"),
        "result": row.get("result"),
        "schedule_alignment": row.get("schedule_alignment"),
        "schedule_note": row.get("schedule_note"),
        "steps": [step],
    }


def _latest_operational_job_run() -> dict[str, Any] | None:
    scheduled = _latest_scheduled_job_run(_load_scheduled_job_history())
    if scheduled:
        return scheduled
    return _job_to_dict(job_manager.latest_run())


def _scheduled_failure_diagnostic(latest_run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not latest_run:
        return None
    if latest_run.get("status") not in {"FAILED", "MISSED"}:
        return None
    return {
        "run_id": latest_run.get("run_id"),
        "job_key": latest_run.get("job_key"),
        "job_label": latest_run.get("job_label"),
        "step_key": latest_run.get("job_key"),
        "step_label": latest_run.get("job_label"),
        "status": latest_run.get("status"),
        "exit_code": latest_run.get("exit_code"),
        "cmd_text": str(latest_run.get("schedule_note") or "定时任务执行异常"),
        "started_at": latest_run.get("started_at"),
        "ended_at": latest_run.get("ended_at"),
        "duration_seconds": None,
        "log_excerpt": str(latest_run.get("result") or ""),
    }


def _parse_scheduled_job_log(job_key: str, job_name: str, log_text: str, source_log: str) -> list[dict[str, Any]]:
    if job_key == "open_paper_trade":
        start_pattern = re.compile(r"^=== (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 开盘纸交易任务开始 ===$")
        end_pattern = re.compile(r"^=== (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 开盘纸交易任务结束 ===$")
        scheduled_time = "09:40"
    else:
        start_pattern = re.compile(r"^=== (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 开始每日(?:收盘流程|数据更新) ===$")
        end_pattern = re.compile(r"^=== (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 结束 ===$")
        scheduled_time = "20:00"

    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    block_lines: list[str] = []
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        start = start_pattern.match(line)
        if start:
            if current is not None:
                current.update(_summarize_scheduled_job_block(block_lines, None))
                _annotate_schedule_alignment(current)
                _mark_interrupted_scheduled_run(current)
                rows.append(current)
            current = {
                "job_key": job_key,
                "job_name": job_name,
                "scheduled_time": scheduled_time,
                "started_at": start.group("ts"),
                "ended_at": None,
                "duration_seconds": None,
                "source_log": source_log,
            }
            block_lines = []
            continue
        if current is None:
            continue
        end = end_pattern.match(line)
        if end:
            ended_at = end.group("ts")
            current["ended_at"] = ended_at
            current["duration_seconds"] = _seconds_between(current.get("started_at"), ended_at)
            current.update(_summarize_scheduled_job_block(block_lines, ended_at))
            _annotate_schedule_alignment(current)
            rows.append(current)
            current = None
            block_lines = []
            continue
        block_lines.append(line)

    if current is not None:
        current.update(_summarize_scheduled_job_block(block_lines, None))
        _annotate_schedule_alignment(current)
        rows.append(current)
    return rows


def _annotate_schedule_alignment(row: dict[str, Any]) -> None:
    started_at = str(row.get("started_at") or "")
    scheduled_time = str(row.get("scheduled_time") or "")
    try:
        started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        scheduled_clock = datetime.strptime(scheduled_time, "%H:%M").time()
    except Exception:
        row["schedule_alignment"] = "未知"
        row["schedule_note"] = None
        return

    scheduled = datetime.combine(started.date(), scheduled_clock)
    delta_minutes = int(round((started - scheduled).total_seconds() / 60))
    allowed_late_minutes = 50 if row.get("job_key") == "open_paper_trade" else 210
    if 0 <= delta_minutes <= allowed_late_minutes:
        row["schedule_alignment"] = "按计划"
        row["schedule_note"] = f"计划 {scheduled_time}，实际 {started.strftime('%H:%M')}"
        return

    row["schedule_alignment"] = "异常时间"
    row["schedule_note"] = f"计划 {scheduled_time}，实际 {started.strftime('%H:%M')}，偏离 {delta_minutes} 分钟"
    status_label = str(row.get("status_label") or "")
    if status_label and "异常时间" not in status_label:
        row["status_label"] = f"{status_label}（异常时间）"


def _mark_interrupted_scheduled_run(row: dict[str, Any]) -> None:
    if row.get("status") != "RUNNING":
        return
    result = str(row.get("result") or "")
    row["status"] = "FAILED"
    row["status_label"] = "未正常结束"
    row["result"] = "未找到结束记录" if not result or result == "运行中" else f"未找到结束记录；{result}"


def _summarize_scheduled_job_block(lines: list[str], ended_at: str | None) -> dict[str, Any]:
    exit_codes = [int(match.group(1)) for line in lines if (match := re.search(r"(?:退出码:|exit=)\s*(-?\d+)", line))]
    target_json = _last_open_target_summary(lines)
    target_status = str(target_json.get("status") or "").upper()
    status = "RUNNING" if ended_at is None else "SUCCEEDED"
    if any(code != 0 for code in exit_codes):
        status = "FAILED"
    elif target_status == "DEGRADED":
        status = "DEGRADED"

    result_parts: list[str] = []
    explicit_exit = next((code for code in exit_codes if code != 0), None)
    if explicit_exit is not None:
        result_parts.append(f"退出码 {explicit_exit}")
    if target_json:
        result_parts.append(
            "目标更新 "
            f"targets={int(target_json.get('targets') or 0)} "
            f"updated={int(target_json.get('updated') or 0)} "
            f"no_data={int(target_json.get('no_data') or 0)}"
        )
    summary_line = _last_summary_line(lines)
    if summary_line:
        result_parts.append(summary_line)
    if not result_parts:
        result_parts.append("运行中" if ended_at is None else "执行完成")
    return {
        "status": status,
        "status_label": _scheduled_status_label(status),
        "result": "；".join(result_parts),
    }


def _last_open_target_summary(lines: list[str]) -> dict[str, Any]:
    for line in reversed(lines):
        if "OPEN_TARGET_UPDATE_SUMMARY_JSON:" not in line:
            continue
        raw = line.split("OPEN_TARGET_UPDATE_SUMMARY_JSON:", 1)[1].strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _last_summary_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if "Paper engine:" in line:
            return line.split(" - ", 1)[-1].strip()
        if "增量更新汇总:" in line:
            return line.split(" - ", 1)[-1].strip()
    return None


def _scheduled_status_label(status: str) -> str:
    return {
        "SUCCEEDED": "成功",
        "DEGRADED": "部分成功",
        "FAILED": "失败",
        "RUNNING": "运行中",
        "WAITING": "等待窗口",
        "MISSED": "已错过",
        "UNKNOWN": "未知",
    }.get(status, status)


def _seconds_between(started_at: Any, ended_at: Any) -> int | None:
    try:
        start = datetime.strptime(str(started_at), "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(str(ended_at), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return max(int((end - start).total_seconds()), 0)


def _launchctl_labels() -> set[str]:
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=2, check=False)
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    return {line.split()[-1] for line in result.stdout.splitlines() if line.strip()}


def _script_from_launch_plist(plist_path: Path) -> str | None:
    try:
        data = plistlib.loads(plist_path.read_bytes())
    except Exception:
        return None
    args = data.get("ProgramArguments") or []
    if not isinstance(args, list):
        return None
    for arg in args:
        value = str(arg)
        if value.endswith(".py") or value.endswith(".sh"):
            return value
    return None


def _load_field_coverage(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    latest = _latest_trade_date(conn)
    if latest is None:
        return []
    scopes = [
        {
            "scope": "current_holdings",
            "scope_label": "当前持仓",
            "decision_use": "直接影响组合体检可信度",
            "symbols": _current_holding_symbols(conn),
        },
        {
            "scope": "signal_candidates",
            "scope_label": "今日候选",
            "decision_use": "直接影响调仓建议可信度",
            "symbols": _latest_active_signal_symbols(conn),
        },
        {
            "scope": "target_universe",
            "scope_label": "目标交易池",
            "decision_use": "生产信号/模型推理的主要数据底座",
            "symbols": _target_universe_symbols(conn, latest),
        },
        {
            "scope": "local_market",
            "scope_label": "本地全市场",
            "decision_use": "市场观察/研究扩展，不阻塞今日调仓",
            "symbols": _local_market_symbols(conn, latest),
        },
    ]
    rows: list[dict[str, Any]] = []
    for scope in scopes:
        symbols = scope["symbols"]
        total = len(symbols)
        coverage = _coverage_counts_for_symbols(conn, symbols, latest)
        for field, label in [
            ("industry", "行业"),
            ("market_cap", "总市值"),
            ("pe_ttm", "PE(TTM)"),
            ("pb", "PB"),
        ]:
            covered = int(coverage.get(field, 0))
            ratio = covered / total if total else 0.0
            rows.append({
                "scope": scope["scope"],
                "scope_label": scope["scope_label"],
                "field": field,
                "field_label": label,
                "covered": covered,
                "total": total,
                "covered_display": f"{covered}/{total}",
                "coverage": ratio,
                "coverage_status": _coverage_status(ratio, total),
                "decision_use": scope["decision_use"],
            })
    return rows


def _current_holding_symbols(conn: duckdb.DuckDBPyConnection) -> list[str]:
    if not _table_exists(conn, "paper_positions"):
        return []
    from src.portfolio.current_holdings import load_current_position_symbols

    return load_current_position_symbols(conn)


def _latest_active_signal_symbols(conn: duckdb.DuckDBPyConnection) -> list[str]:
    if not _table_exists(conn, "signals"):
        return []
    latest_signal_date = conn.execute("""
        SELECT MAX(CAST(signal_ts AS DATE))
        FROM signals
        WHERE status = 'ACTIVE'
    """).fetchone()[0]
    if latest_signal_date is None:
        return []
    rows = conn.execute("""
        SELECT DISTINCT symbol
        FROM signals
        WHERE status = 'ACTIVE'
          AND CAST(signal_ts AS DATE) = ?
        ORDER BY symbol
    """, [latest_signal_date]).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _target_universe_symbols(conn: duckdb.DuckDBPyConnection, latest: Any) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT dp.symbol
        FROM daily_price dp
        JOIN stock_info si ON si.symbol = dp.symbol
        WHERE dp.trade_date = ?
          AND si.country = 'CN'
        ORDER BY dp.symbol
    """, [latest]).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _local_market_symbols(conn: duckdb.DuckDBPyConnection, latest: Any) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT symbol
        FROM daily_price
        WHERE trade_date = ?
        ORDER BY symbol
    """, [latest]).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _coverage_counts_for_symbols(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    latest: Any,
) -> dict[str, int]:
    if not symbols:
        return {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0}
    placeholders = ",".join(["?"] * len(symbols))
    row = conn.execute(
        f"""
        WITH scope_symbols AS (
            SELECT symbol
            FROM (VALUES {",".join(["(?)"] * len(symbols))}) AS t(symbol)
        ),
        latest_price AS (
            SELECT symbol, pe_ttm, pb
            FROM daily_price
            WHERE symbol IN ({placeholders})
              AND trade_date <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        )
        SELECT
            SUM(CASE WHEN NULLIF(TRIM(COALESCE(si.industry, si.sector, '')), '') IS NOT NULL THEN 1 ELSE 0 END) AS industry,
            SUM(CASE WHEN TRY_CAST(si.market_cap AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS market_cap,
            SUM(CASE WHEN TRY_CAST(lp.pe_ttm AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS pe_ttm,
            SUM(CASE WHEN TRY_CAST(lp.pb AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS pb
        FROM scope_symbols ss
        LEFT JOIN stock_info si ON si.symbol = ss.symbol
        LEFT JOIN latest_price lp ON lp.symbol = ss.symbol
        """,
        [*symbols, *symbols, latest],
    ).fetchone()
    return {
        "industry": int(row[0] or 0),
        "market_cap": int(row[1] or 0),
        "pe_ttm": int(row[2] or 0),
        "pb": int(row[3] or 0),
    }


def _coverage_status(coverage: float, total: int) -> str:
    if total <= 0:
        return "无样本"
    if coverage >= 0.8:
        return "可用"
    if coverage > 0:
        return "部分可用"
    return "缺失"


def _load_qlib_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    production = _load_production_model(conn)
    recent_ic = _load_recent_ic(conn)
    return {
        "production_available": bool(production),
        "production_model": production,
        "recent_ic": recent_ic,
        "signal_freshness": _load_alpha158_signal_freshness(conn),
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


def _date_only_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text_value = str(value)
    return text_value[:10] if text_value else None


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
