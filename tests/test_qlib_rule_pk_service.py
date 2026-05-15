import duckdb
import pandas as pd
import pytest

from src.data_pipeline.loader import init_db
from src.dashboard.qlib_rule_pk_service import (
    evaluate_ab_tracking,
    load_rule_qlib_pk,
    record_ab_snapshot,
    resolve_champion_experiment,
)


def _conn():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES
          ('A', 'CN', '规则共振'),
          ('B', 'CN', '规则卖出冲突'),
          ('C', 'CN', '规则低排'),
          ('D', 'CN', 'Qlib独立')
    """)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES
          ('E1', 'alpha158', 'v1', 'walk_forward', DATE '2024-01-02', 'A', 0.90, 1, 0.95, TRUE),
          ('E1', 'alpha158', 'v1', 'walk_forward', DATE '2024-01-02', 'B', 0.80, 2, 0.85, TRUE),
          ('E1', 'alpha158', 'v1', 'walk_forward', DATE '2024-01-02', 'D', 0.70, 3, 0.75, FALSE),
          ('E1', 'alpha158', 'v1', 'walk_forward', DATE '2024-01-02', 'C', 0.10, 4, 0.10, FALSE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('S1', 'trend_following', '1.0', 'A', TIMESTAMP '2024-01-02 15:00:00',
           'BUY', 0.9, 0.9, 0.1, FALSE, 'ACTIVE'),
          ('S2', 'mean_reversion', '1.0', 'B', TIMESTAMP '2024-01-02 15:00:00',
           'SELL', 0.8, 0.8, 0.1, FALSE, 'ACTIVE'),
          ('S3', 'trend_following', '1.0', 'C', TIMESTAMP '2024-01-02 15:00:00',
           'BUY', 0.7, 0.7, 0.1, FALSE, 'ACTIVE')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES
          ('A', DATE '2024-01-02', 10, 10, 10, 10, 1000),
          ('A', DATE '2024-01-03', 11, 11, 11, 11, 1000),
          ('B', DATE '2024-01-02', 10, 10, 10, 10, 1000),
          ('B', DATE '2024-01-03', 12, 12, 12, 12, 1000),
          ('C', DATE '2024-01-02', 10, 10, 10, 10, 1000),
          ('C', DATE '2024-01-03', 9, 9, 9, 9, 1000),
          ('D', DATE '2024-01-02', 10, 10, 10, 10, 1000),
          ('D', DATE '2024-01-03', 10.5, 10.5, 10.5, 10.5, 1000)
    """)
    return conn


def test_rule_qlib_pk_classifies_latest_cross_section():
    conn = _conn()

    report = load_rule_qlib_pk(conn, top_n=2, horizons=(1,))
    summary = report["summary"]
    details = report["details"].set_index("symbol")

    assert report["status"] == "OK"
    assert summary["rule_symbols"] == 3
    assert summary["rule_buy_symbols"] == 2
    assert summary["rule_sell_symbols"] == 1
    assert summary["overlap_top_n"] == 2
    assert summary["consensus_buy"] == 1
    assert summary["conflict_sell_high_rank"] == 1
    assert details.loc["A", "classification"] == "共振买入"
    assert details.loc["B", "classification"] == "冲突：规则卖出/Qlib高分"
    assert details.loc["C", "classification"] == "规则买入/Qlib弱"
    conn.close()


def test_rule_qlib_pk_computes_directional_forward_returns():
    conn = _conn()

    report = load_rule_qlib_pk(conn, top_n=2, horizons=(1,))
    history = report["history"]

    rule_buy = history[(history["group"] == "规则买入") & (history["horizon"] == 1)].iloc[0]
    rule_sell = history[(history["group"] == "规则卖出") & (history["horizon"] == 1)].iloc[0]
    qlib_top = history[(history["group"] == "Qlib Top2") & (history["horizon"] == 1)].iloc[0]
    consensus = history[(history["group"] == "共振买入") & (history["horizon"] == 1)].iloc[0]

    assert rule_buy["observations"] == 2
    assert rule_buy["avg_forward_return"] == pytest.approx(0.0)
    assert rule_sell["avg_forward_return"] == pytest.approx(0.20)
    assert rule_sell["avg_directional_return"] == pytest.approx(-0.20)
    assert qlib_top["observations"] == 2
    assert qlib_top["avg_forward_return"] == pytest.approx(0.15)
    assert consensus["avg_forward_return"] == pytest.approx(0.10)
    conn.close()


def test_rule_qlib_pk_handles_missing_predictions():
    conn = duckdb.connect(":memory:")
    init_db(conn)

    report = load_rule_qlib_pk(conn)

    assert report["status"] == "NO_QLIB_PREDICTIONS"
    assert report["details"].empty
    assert report["history"].empty
    assert report["summary"]["rule_symbols"] == 0
    conn.close()


def test_record_ab_snapshot_persists_rule_and_qlib_arms_idempotently():
    conn = _conn()

    first = record_ab_snapshot(conn, top_n=2, horizons=(1,))
    second = record_ab_snapshot(conn, top_n=2, horizons=(1,))

    assert first["status"] == "RECORDED"
    assert second["status"] == "RECORDED"
    assert first["run_id"] == second["run_id"]
    rows = conn.execute("""
        SELECT arm, symbol
        FROM rule_qlib_ab_members
        WHERE run_id = ?
        ORDER BY arm, symbol
    """, [first["run_id"]]).fetchall()
    assert rows == [
        ("A_RULE_BUY", "A"),
        ("A_RULE_BUY", "C"),
        ("B_QLIB_TOPN", "A"),
        ("B_QLIB_TOPN", "B"),
        ("C_CONSENSUS", "A"),
    ]
    count = conn.execute("SELECT COUNT(*) FROM rule_qlib_ab_snapshots").fetchone()[0]
    assert count == 1
    conn.close()


def test_record_ab_snapshot_defaults_to_best_candidate_champion():
    conn = _conn()
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES
          ('E2', 'alpha158', 'v2', 'walk_forward', DATE '2024-01-02', 'A', 0.20, 2, 0.2, FALSE),
          ('E2', 'alpha158', 'v2', 'walk_forward', DATE '2024-01-02', 'D', 0.95, 1, 0.95, TRUE)
    """)
    conn.execute("""
        INSERT INTO qlib_candidate_results (
            candidate_id, batch_id, experiment_id, model_name, model_family,
            model_variant, status, mode, best_benchmark, best_top_n,
            best_holding_days, best_rebalance_freq, best_buffer_n,
            annual_return, sharpe_ratio, max_drawdown, turnover,
            benchmark_return, excess_return, ic_mean, icir,
            rank_ic_mean, rank_ic_positive_rate, score
        )
        VALUES
          ('low', 'B1', 'E1', 'alpha158', 'lgbm', 'low', 'SUCCEEDED',
           'walk_forward', 'MIXED_EQUAL', 2, 5, 'monthly', 3,
           0.1, 0.2, -0.2, 10, 0.01, 0.02, 0.01, 0.1, 0.01, 0.5, 0.1),
          ('winner', 'B1', 'E2', 'alpha158', 'lgbm', 'winner', 'SUCCEEDED',
           'walk_forward', 'MIXED_EQUAL', 1, 9, 'monthly', 2,
           0.2, 0.5, -0.1, 8, 0.01, 0.05, 0.02, 0.3, 0.02, 0.6, 0.9)
    """)

    champion = resolve_champion_experiment(conn, prediction_date=pd.Timestamp("2024-01-02").date())
    snapshot = record_ab_snapshot(conn, horizons=(1,))

    assert champion["experiment_id"] == "E2"
    assert champion["top_n"] == 1
    assert snapshot["experiment_id"] == "E2"
    assert snapshot["top_n"] == 1
    members = conn.execute("""
        SELECT arm, symbol FROM rule_qlib_ab_members
        WHERE run_id = ? ORDER BY arm, symbol
    """, [snapshot["run_id"]]).fetchall()
    assert ("B_QLIB_TOPN", "D") in members
    assert ("B_QLIB_TOPN", "A") not in members
    conn.close()


def test_evaluate_ab_tracking_compares_shadow_arms():
    conn = _conn()
    snapshot = record_ab_snapshot(conn, top_n=2, horizons=(1,))

    report = evaluate_ab_tracking(conn, horizons=(1,))
    by_arm = report["arm_summary"].set_index("arm")
    members = report["member_returns"]

    assert snapshot["status"] == "RECORDED"
    assert by_arm.loc["A_RULE_BUY", "avg_forward_return_1d"] == pytest.approx(0.0)
    assert by_arm.loc["B_QLIB_TOPN", "avg_forward_return_1d"] == pytest.approx(0.15)
    assert by_arm.loc["C_CONSENSUS", "avg_forward_return_1d"] == pytest.approx(0.10)
    assert set(members["arm"]) == {"A_RULE_BUY", "B_QLIB_TOPN", "C_CONSENSUS"}
    conn.close()
