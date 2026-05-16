from datetime import date

import duckdb
import pytest

from src.dashboard.signal_outcome_service import load_signal_outcome_snapshot
from src.data_pipeline.loader import init_db


def test_signal_outcome_dashboard_returns_stable_empty_frames():
    conn = duckdb.connect(":memory:")
    init_db(conn)

    snapshot = load_signal_outcome_snapshot(conn)

    assert snapshot["summary"].empty
    assert snapshot["monthly"].empty
    assert snapshot["detail"].empty
    assert snapshot["summary"].columns.tolist() == [
        "model_name",
        "horizon_days",
        "sample_count",
        "pending_count",
        "hit_count",
        "hit_rate",
        "avg_return",
        "avg_alpha_vs_benchmark",
        "median_return",
    ]
    conn.close()


def test_signal_outcome_dashboard_aggregates_by_model_month_and_horizon():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO signal_outcomes (
            signal_id, horizon_days, model_name, model_version, symbol, side,
            signal_date, execution_date, execution_price, outcome_date,
            outcome_price, return_pct, benchmark_code, benchmark_return_pct, alpha_vs_benchmark, status
        )
        VALUES
            ('a1', 1, 'alpha158', 'v1', '000001', 'BUY', DATE '2026-05-01', DATE '2026-05-02', 10, DATE '2026-05-03', 11, 0.10, '000300', 0.03, 0.07, 'READY'),
            ('a2', 1, 'alpha158', 'v1', '000002', 'BUY', DATE '2026-05-08', DATE '2026-05-09', 10, DATE '2026-05-10', 9.5, -0.05, '000300', 0.01, -0.06, 'READY'),
            ('a3', 5, 'alpha158', 'v1', '000003', 'BUY', DATE '2026-05-08', DATE '2026-05-09', 10, NULL, NULL, NULL, '000300', NULL, NULL, 'PENDING'),
            ('t1', 1, 'trend', 'v1', '000004', 'SELL', DATE '2026-06-01', DATE '2026-06-02', 10, DATE '2026-06-03', 8, 0.25, '000300', -0.02, 0.27, 'READY')
    """)

    snapshot = load_signal_outcome_snapshot(conn)
    summary = snapshot["summary"].set_index(["model_name", "horizon_days"])
    monthly = snapshot["monthly"].set_index(["model_name", "execution_month", "horizon_days"])
    detail = snapshot["detail"]

    assert summary.loc[("alpha158", 1), "sample_count"] == 2
    assert summary.loc[("alpha158", 1), "pending_count"] == 0
    assert summary.loc[("alpha158", 1), "hit_count"] == 1
    assert summary.loc[("alpha158", 1), "hit_rate"] == pytest.approx(0.5)
    assert summary.loc[("alpha158", 1), "avg_return"] == pytest.approx(0.025)
    assert summary.loc[("alpha158", 1), "avg_alpha_vs_benchmark"] == pytest.approx(0.005)
    assert summary.loc[("alpha158", 5), "sample_count"] == 0
    assert summary.loc[("alpha158", 5), "pending_count"] == 1
    assert monthly.loc[("alpha158", date(2026, 5, 1), 1), "sample_count"] == 2
    assert monthly.loc[("alpha158", date(2026, 5, 1), 1), "avg_alpha_vs_benchmark"] == pytest.approx(0.005)
    assert monthly.loc[("trend", date(2026, 6, 1), 1), "hit_rate"] == pytest.approx(1.0)
    assert monthly.loc[("trend", date(2026, 6, 1), 1), "avg_alpha_vs_benchmark"] == pytest.approx(0.27)
    assert detail["signal_id"].tolist() == ["t1", "a3", "a2", "a1"]
    assert detail.loc[detail["signal_id"] == "a1", "alpha_vs_benchmark"].iloc[0] == pytest.approx(0.07)
    conn.close()
