"""Monitor production model predictions, signals, and realized outcomes."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

MIN_READY_OUTCOMES = 5
MIN_SIGNALS_FOR_RATE_ALERT = 5
MIN_ALPHA_VS_BENCHMARK = 0.0
MIN_HIT_RATE = 0.45
MAX_NO_ACTION_RATE = 0.50
PREDICTION_READY_BLOCKING_METRICS = {
    "production_model_missing",
    "production_prediction_missing",
    "production_prediction_stale",
}


def evaluate_production_model(conn: Any, as_of: date | None = None) -> dict[str, Any]:
    """Build a production model monitoring snapshot without mutating storage."""
    production = _load_production_model(conn)
    alert_date = as_of or date.today()
    if production is None:
        alert = _alert(
            model_name="alpha158",
            model_version=None,
            experiment_id=None,
            alert_date=alert_date,
            severity="CRITICAL",
            metric_name="production_model_missing",
            message="Qlib production 模型不可用",
        )
        return _snapshot("failed", None, None, {}, [alert])

    latest_data = _latest_trade_date(conn, as_of=as_of)
    prediction = _prediction_metrics(conn, production, latest_data)
    signals = _signal_metrics(conn, production)
    outcomes = _outcome_metrics(conn, production)
    alerts = []
    alerts.extend(_prediction_alerts(production, alert_date, latest_data, prediction))
    alerts.extend(_signal_alerts(production, alert_date, signals))
    alerts.extend(_outcome_alerts(production, alert_date, outcomes))

    status = _status_from_alerts(alerts)
    return _snapshot(
        status,
        production,
        latest_data,
        {
            "prediction": prediction,
            "signals": signals,
            "outcomes": outcomes,
        },
        alerts,
    )


def update_production_model_monitor(conn: Any | None = None, as_of: date | None = None) -> dict[str, Any]:
    """Evaluate production monitoring and persist the current alert state."""
    owns_connection = conn is None
    if conn is None:
        from src.data_pipeline.loader import get_connection, init_db

        conn = get_connection()
        init_db(conn)
    try:
        result = evaluate_production_model(conn, as_of=as_of)
        _persist_alerts(conn, result)
        result["active_alert_count"] = len(result.get("alerts", []))
        return result
    finally:
        if owns_connection:
            conn.close()


def assert_production_prediction_ready(as_of: date | None = None) -> dict[str, Any]:
    """Persist monitor state and return whether production prediction is fresh enough for arbitration."""
    result = update_production_model_monitor(as_of=as_of)
    blocking = [
        alert["metric_name"]
        for alert in result.get("alerts", [])
        if alert.get("metric_name") in PREDICTION_READY_BLOCKING_METRICS
    ]
    return {
        "ready": not blocking,
        "status": "ok" if not blocking else "failed",
        "blocking_metrics": blocking,
        "latest_data_date": result.get("latest_data_date"),
        "production_model": result.get("production_model"),
    }


def auto_demote_production_model(
    conn: Any | None = None,
    *,
    model_name: str = "alpha158",
    min_consecutive_days: int = 8,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Demote a production model to staging after consecutive active critical alerts."""
    owns_connection = conn is None
    if conn is None:
        from src.data_pipeline.loader import get_connection, init_db

        conn = get_connection()
        init_db(conn)
    try:
        production = _load_production_model(conn, model_name=model_name)
        alert_date = as_of or date.today()
        if production is None:
            return {
                "status": "production_model_missing",
                "demoted": False,
                "model_name": model_name,
                "model_version": None,
                "critical_streak_days": 0,
                "min_consecutive_days": min_consecutive_days,
            }

        streak_days = _active_critical_alert_streak_days(conn, production, as_of=alert_date)
        demoted = streak_days >= min_consecutive_days
        if demoted:
            conn.execute("""
                UPDATE qlib_model_registry
                SET status = 'staging'
                WHERE model_name = ?
                  AND model_version = ?
                  AND status = 'production'
            """, [production["model_name"], production["model_version"]])

        return {
            "status": "demoted" if demoted else "monitoring",
            "demoted": demoted,
            "model_name": production["model_name"],
            "model_version": production["model_version"],
            "critical_streak_days": streak_days,
            "min_consecutive_days": min_consecutive_days,
        }
    finally:
        if owns_connection:
            conn.close()


def _load_production_model(conn: Any, model_name: str = "alpha158") -> dict[str, Any] | None:
    row = conn.execute("""
        SELECT model_name, model_version, experiment_id, published_at, metrics_json
        FROM qlib_model_registry
        WHERE model_name = ? AND status = 'production'
        ORDER BY published_at DESC NULLS LAST, created_at DESC
        LIMIT 1
    """, [model_name]).fetchone()
    if row is None:
        return None
    return {
        "model_name": row[0],
        "model_version": row[1],
        "experiment_id": row[2],
        "published_at": _jsonable(row[3]),
        "metrics": _loads_json(row[4]),
    }


def _active_critical_alert_streak_days(conn: Any, production: dict[str, Any], *, as_of: date) -> int:
    rows = conn.execute("""
        SELECT DISTINCT alert_date
        FROM model_monitor_alerts
        WHERE model_name = ?
          AND model_version = ?
          AND severity = 'CRITICAL'
          AND status = 'ACTIVE'
          AND alert_date <= ?
        ORDER BY alert_date DESC
    """, [production["model_name"], production["model_version"], as_of]).fetchall()
    critical_dates = {row[0] for row in rows}
    streak = 0
    current_date = as_of
    while current_date in critical_dates:
        streak += 1
        current_date -= timedelta(days=1)
    return streak


def _latest_trade_date(conn: Any, as_of: date | None = None) -> date | None:
    if as_of is None:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    else:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date <= ?", [as_of]).fetchone()
    return row[0] if row and row[0] is not None else None


def _prediction_metrics(conn: Any, production: dict[str, Any], latest_data: date | None) -> dict[str, Any]:
    row = conn.execute("""
        SELECT MAX(prediction_date), COUNT(*),
               SUM(CASE WHEN selected THEN 1 ELSE 0 END)
        FROM qlib_predictions
        WHERE experiment_id = ?
          AND model_version = ?
          AND mode = 'production_inference'
    """, [production["experiment_id"], production["model_version"]]).fetchone()
    latest_prediction = row[0] if row else None
    latest_selected = 0
    latest_rows = 0
    if latest_prediction is not None:
        latest_row = conn.execute("""
            SELECT COUNT(*), SUM(CASE WHEN selected THEN 1 ELSE 0 END)
            FROM qlib_predictions
            WHERE experiment_id = ?
              AND model_version = ?
              AND mode = 'production_inference'
              AND prediction_date = ?
        """, [production["experiment_id"], production["model_version"], latest_prediction]).fetchone()
        latest_rows = int(latest_row[0] or 0)
        latest_selected = int(latest_row[1] or 0)
    stale_days = None
    if latest_data is not None and latest_prediction is not None:
        stale_days = max((pd.Timestamp(latest_data) - pd.Timestamp(latest_prediction)).days, 0)
    return {
        "latest_date": _date_to_iso(latest_prediction),
        "latest_data_date": _date_to_iso(latest_data),
        "row_count": int(row[1] or 0) if row else 0,
        "latest_row_count": latest_rows,
        "latest_selected_count": latest_selected,
        "stale_days": stale_days,
    }


def _signal_metrics(conn: Any, production: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute("""
        SELECT COUNT(*), MAX(CAST(signal_ts AS DATE)),
               SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status = 'NO_ACTION' THEN 1 ELSE 0 END),
               SUM(CASE WHEN executed THEN 1 ELSE 0 END)
        FROM signals
        WHERE model_name = ?
          AND model_version = ?
    """, [production["model_name"], production["model_version"]]).fetchone()
    total = int(row[0] or 0)
    no_action = int(row[4] or 0)
    return {
        "signal_count": total,
        "latest_signal_date": _date_to_iso(row[1]),
        "active_count": int(row[2] or 0),
        "filled_count": int(row[3] or 0),
        "no_action_count": no_action,
        "executed_count": int(row[5] or 0),
        "no_action_rate": no_action / total if total else None,
    }


def _outcome_metrics(conn: Any, production: dict[str, Any]) -> list[dict[str, Any]]:
    df = conn.execute("""
        SELECT horizon_days,
               COUNT(*) AS total_count,
               SUM(CASE WHEN status = 'READY' THEN 1 ELSE 0 END) AS ready_count,
               SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending_count,
               AVG(CASE WHEN status = 'READY' THEN return_pct END) AS avg_return,
               AVG(CASE WHEN status = 'READY' THEN alpha_vs_benchmark END) AS avg_alpha,
               AVG(CASE WHEN status = 'READY' AND COALESCE(alpha_vs_benchmark, return_pct) > 0
                        THEN 1.0
                        WHEN status = 'READY' THEN 0.0
                        ELSE NULL END) AS hit_rate
        FROM signal_outcomes
        WHERE model_name = ?
          AND model_version = ?
        GROUP BY horizon_days
        ORDER BY horizon_days
    """, [production["model_name"], production["model_version"]]).fetchdf()
    return [_jsonable(row) for row in df.to_dict("records")]


def _prediction_alerts(
    production: dict[str, Any],
    alert_date: date,
    latest_data: date | None,
    prediction: dict[str, Any],
) -> list[dict[str, Any]]:
    if prediction["latest_date"] is None:
        context = {"latest_data_date": _date_to_iso(latest_data)}
        context.update(_qlib_runtime_context())
        return [
            _alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name="production_prediction_missing",
                message="production 模型尚未生成生产预测截面",
                context=context,
            )
        ]
    if latest_data is not None and prediction["latest_date"] < _date_to_iso(latest_data):
        return [
            _alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name="production_prediction_stale",
                observed_value=float(prediction["stale_days"] or 0),
                threshold_value=0.0,
                message="production 预测落后于最新行情日期",
                context=prediction,
            )
        ]
    return []


def _qlib_runtime_context() -> dict[str, Any]:
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "qlib_installed": importlib.util.find_spec("qlib") is not None,
    }


def _signal_alerts(production: dict[str, Any], alert_date: date, signals: dict[str, Any]) -> list[dict[str, Any]]:
    if signals["signal_count"] == 0:
        return [
            _alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name="production_signal_missing",
                message="production 模型尚未写入交易信号",
            )
        ]
    no_action_rate = signals.get("no_action_rate")
    if signals["signal_count"] >= MIN_SIGNALS_FOR_RATE_ALERT and no_action_rate is not None and no_action_rate > MAX_NO_ACTION_RATE:
        return [
            _alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name="signal_no_action_rate",
                observed_value=float(no_action_rate),
                threshold_value=MAX_NO_ACTION_RATE,
                message="production 信号纸交易 NO_ACTION 比例过高",
                context=signals,
            )
        ]
    return []


def _outcome_alerts(production: dict[str, Any], alert_date: date, outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in outcomes:
        horizon = int(row.get("horizon_days") or 0)
        ready_count = int(row.get("ready_count") or 0)
        if ready_count < MIN_READY_OUTCOMES:
            continue
        avg_alpha = row.get("avg_alpha")
        hit_rate = row.get("hit_rate")
        if avg_alpha is not None and float(avg_alpha) < MIN_ALPHA_VS_BENCHMARK:
            alerts.append(_alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name=f"alpha_h{horizon}",
                observed_value=float(avg_alpha),
                threshold_value=MIN_ALPHA_VS_BENCHMARK,
                message=f"production 信号 {horizon} 日平均超额收益转负",
                context=row,
            ))
        if hit_rate is not None and float(hit_rate) < MIN_HIT_RATE:
            alerts.append(_alert(
                production=production,
                alert_date=alert_date,
                severity="WARN",
                metric_name=f"hit_rate_h{horizon}",
                observed_value=float(hit_rate),
                threshold_value=MIN_HIT_RATE,
                message=f"production 信号 {horizon} 日命中率低于阈值",
                context=row,
            ))
    return alerts


def _persist_alerts(conn: Any, result: dict[str, Any]) -> None:
    production = result.get("production_model") or {}
    model_name = production.get("model_name") or "alpha158"
    model_version = production.get("model_version")
    current_metric_names = {alert["metric_name"] for alert in result.get("alerts", [])}
    conn.execute("""
        UPDATE model_monitor_alerts
        SET status = 'RESOLVED',
            resolved_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE model_name = ?
          AND status = 'ACTIVE'
          AND COALESCE(model_version, '__NULL__') <> COALESCE(?, '__NULL__')
    """, [model_name, model_version])
    if current_metric_names:
        placeholders = ",".join(["?"] * len(current_metric_names))
        params = [model_name, model_version, *sorted(current_metric_names)]
        conn.execute(f"""
            UPDATE model_monitor_alerts
            SET status = 'RESOLVED',
                resolved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = ?
              AND COALESCE(model_version, '__NULL__') = COALESCE(?, '__NULL__')
              AND status = 'ACTIVE'
              AND metric_name NOT IN ({placeholders})
        """, params)
    else:
        conn.execute("""
            UPDATE model_monitor_alerts
            SET status = 'RESOLVED',
                resolved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = ?
              AND COALESCE(model_version, '__NULL__') = COALESCE(?, '__NULL__')
              AND status = 'ACTIVE'
        """, [model_name, model_version])

    for alert in result.get("alerts", []):
        conn.execute("""
            INSERT OR REPLACE INTO model_monitor_alerts (
                alert_id, model_name, model_version, experiment_id, alert_date,
                severity, metric_name, observed_value, threshold_value, status,
                message, context_json, updated_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, CURRENT_TIMESTAMP, NULL)
        """, [
            alert["alert_id"],
            alert["model_name"],
            alert["model_version"],
            alert["experiment_id"],
            alert["alert_date"],
            alert["severity"],
            alert["metric_name"],
            alert.get("observed_value"),
            alert.get("threshold_value"),
            alert["message"],
            json.dumps(alert.get("context") or {}, ensure_ascii=False, default=str),
        ])


def _alert(
    *,
    alert_date: date,
    severity: str,
    metric_name: str,
    message: str,
    production: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    experiment_id: str | None = None,
    observed_value: float | None = None,
    threshold_value: float | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    production = production or {}
    resolved_model_name = model_name or production.get("model_name")
    resolved_model_version = model_version or production.get("model_version")
    resolved_experiment_id = experiment_id or production.get("experiment_id")
    alert_id = _alert_id(resolved_model_name, resolved_model_version, alert_date, metric_name)
    return {
        "alert_id": alert_id,
        "model_name": resolved_model_name,
        "model_version": resolved_model_version,
        "experiment_id": resolved_experiment_id,
        "alert_date": _date_to_iso(alert_date),
        "severity": severity,
        "metric_name": metric_name,
        "observed_value": observed_value,
        "threshold_value": threshold_value,
        "status": "ACTIVE",
        "message": message,
        "context": context or {},
    }


def _alert_id(model_name: str | None, model_version: str | None, alert_date: date, metric_name: str) -> str:
    raw = f"{model_name or 'unknown'}-{model_version or 'none'}-{_date_to_iso(alert_date)}-{metric_name}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-").upper()
    return f"MMA-{cleaned[:180]}"


def _snapshot(
    status: str,
    production: dict[str, Any] | None,
    latest_data: date | None,
    metrics: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": status,
        "latest_data_date": _date_to_iso(latest_data),
        "production_model": production,
        "metrics": metrics,
        "alerts": alerts,
    }


def _status_from_alerts(alerts: list[dict[str, Any]]) -> str:
    if any(alert.get("severity") == "CRITICAL" for alert in alerts):
        return "failed"
    if any(alert.get("severity") == "WARN" for alert in alerts):
        return "degraded"
    return "ok"


def _date_to_iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _loads_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production model monitoring")
    sub = parser.add_subparsers(dest="command")
    p_update = sub.add_parser("update", help="evaluate and persist production model alerts")
    p_update.add_argument("--as-of", default=None)
    p_ready = sub.add_parser("assert-prediction-ready", help="fail if production prediction is missing or stale")
    p_ready.add_argument("--as-of", default=None)
    p_demote = sub.add_parser("auto-demote", help="demote production model after consecutive CRITICAL alerts")
    p_demote.add_argument("--model-name", default="alpha158")
    p_demote.add_argument("--min-consecutive-days", type=int, default=8)
    p_demote.add_argument("--as-of", default=None)
    args = parser.parse_args(argv)

    if args.command == "update":
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        result = update_production_model_monitor(as_of=as_of)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result.get("status") != "failed" else 1
    if args.command == "assert-prediction-ready":
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        result = assert_production_prediction_ready(as_of=as_of)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result.get("ready") else 2
    if args.command == "auto-demote":
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        result = auto_demote_production_model(
            model_name=args.model_name,
            min_consecutive_days=args.min_consecutive_days,
            as_of=as_of,
        )
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
