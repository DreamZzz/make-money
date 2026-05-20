import json
from datetime import date

import duckdb

from src.data_pipeline.loader import init_db
from src.monitoring.model_monitor import evaluate_production_model, update_production_model_monitor


def _conn():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    return conn


def _seed_production_model(conn, model_version: str = "alpha158-prod", experiment_id: str = "EXP-PROD") -> None:
    conn.execute("""
        INSERT INTO qlib_model_registry (
            model_version, experiment_id, model_name, status, market, metrics_json, published_at
        )
        VALUES (?, ?, 'alpha158', 'production', 'CN', '{}', TIMESTAMP '2026-05-15 16:00:00')
    """, [model_version, experiment_id])
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close)
        VALUES ('000001', DATE '2026-05-15', 10)
    """)


def test_model_monitor_alerts_when_production_prediction_is_missing():
    conn = _conn()
    _seed_production_model(conn)

    result = update_production_model_monitor(conn, as_of=date(2026, 5, 15))
    alerts = conn.execute("""
        SELECT metric_name, severity, status, message
        FROM model_monitor_alerts
        ORDER BY metric_name
    """).fetchall()

    assert result["status"] == "degraded"
    assert ("production_prediction_missing", "WARN", "ACTIVE", "production 模型尚未生成生产预测截面") in alerts
    conn.close()


def test_model_monitor_prediction_missing_includes_runtime_context():
    conn = _conn()
    _seed_production_model(conn)

    update_production_model_monitor(conn, as_of=date(2026, 5, 15))
    context_json = conn.execute("""
        SELECT context_json
        FROM model_monitor_alerts
        WHERE metric_name = 'production_prediction_missing'
    """).fetchone()[0]
    context = json.loads(context_json)

    assert "qlib_installed" in context
    assert "python_version" in context
    conn.close()


def test_model_monitor_passes_with_fresh_prediction_signal_and_positive_alpha():
    conn = _conn()
    _seed_production_model(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '测试股')
    """)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2026-05-15', '000001', 0.9, 1, 1.0, TRUE)
    """)
    for idx in range(6):
        signal_id = f"sig-{idx}"
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, model_version, symbol, signal_ts, side,
                executed, execution_price, execution_date, status
            )
            VALUES (?, 'alpha158', 'alpha158-prod', '000001', TIMESTAMP '2026-05-15 15:00:00',
                    'BUY', TRUE, 10, DATE '2026-05-18', 'FILLED')
        """, [signal_id])
        conn.execute("""
            INSERT INTO signal_outcomes (
                signal_id, horizon_days, model_name, model_version, symbol, side,
                signal_date, execution_date, execution_price, outcome_date,
                outcome_price, return_pct, benchmark_code, benchmark_return_pct,
                alpha_vs_benchmark, status
            )
            VALUES (?, 5, 'alpha158', 'alpha158-prod', '000001', 'BUY',
                    DATE '2026-05-15', DATE '2026-05-18', 10, DATE '2026-05-25',
                    11, 0.10, '000300', 0.02, 0.08, 'READY')
        """, [signal_id])

    result = update_production_model_monitor(conn, as_of=date(2026, 5, 15))
    active_count = conn.execute("""
        SELECT COUNT(*) FROM model_monitor_alerts WHERE status = 'ACTIVE'
    """).fetchone()[0]

    assert result["status"] == "ok"
    assert result["metrics"]["prediction"]["latest_date"] == "2026-05-15"
    assert result["metrics"]["outcomes"][0]["ready_count"] == 6
    assert active_count == 0
    conn.close()


def test_model_monitor_resolves_previous_active_alert_when_condition_clears():
    conn = _conn()
    _seed_production_model(conn)

    update_production_model_monitor(conn, as_of=date(2026, 5, 15))
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2026-05-15', '000001', 0.9, 1, 1.0, TRUE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts, side,
            executed, execution_price, execution_date, status
        )
        VALUES ('sig-1', 'alpha158', 'alpha158-prod', '000001', TIMESTAMP '2026-05-15 15:00:00',
                'BUY', FALSE, NULL, NULL, 'ACTIVE')
    """)

    result = update_production_model_monitor(conn, as_of=date(2026, 5, 15))
    row = conn.execute("""
        SELECT status
        FROM model_monitor_alerts
        WHERE metric_name = 'production_prediction_missing'
    """).fetchone()

    assert result["status"] == "ok"
    assert row == ("RESOLVED",)
    conn.close()


def test_evaluate_production_model_reports_low_alpha_alert():
    conn = _conn()
    _seed_production_model(conn)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2026-05-15', '000001', 0.9, 1, 1.0, TRUE)
    """)
    for idx in range(5):
        signal_id = f"sig-low-{idx}"
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, model_version, symbol, signal_ts, side,
                executed, execution_price, execution_date, status
            )
            VALUES (?, 'alpha158', 'alpha158-prod', '000001', TIMESTAMP '2026-05-15 15:00:00',
                    'BUY', TRUE, 10, DATE '2026-05-18', 'FILLED')
        """, [signal_id])
        conn.execute("""
            INSERT INTO signal_outcomes (
                signal_id, horizon_days, model_name, model_version, symbol, side,
                signal_date, execution_date, execution_price, outcome_date,
                outcome_price, return_pct, benchmark_code, benchmark_return_pct,
                alpha_vs_benchmark, status
            )
            VALUES (?, 5, 'alpha158', 'alpha158-prod', '000001', 'BUY',
                    DATE '2026-05-15', DATE '2026-05-18', 10, DATE '2026-05-25',
                    9.9, -0.01, '000300', 0.02, -0.03, 'READY')
        """, [signal_id])

    result = evaluate_production_model(conn, as_of=date(2026, 5, 15))

    assert result["status"] == "degraded"
    assert any(alert["metric_name"] == "alpha_h5" for alert in result["alerts"])
    conn.close()
