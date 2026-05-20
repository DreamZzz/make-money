from __future__ import annotations

import duckdb

from src.config import DEFAULT_CONFIG
from src.data_pipeline.loader import init_db
from src.signals.arbiter import arbitrate_pending_signals


def _seed_base(conn: duckdb.DuckDBPyConnection) -> None:
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '测试A'), ('000002', 'CN', '测试B')
    """)


def test_arbiter_rejects_rule_buy_when_fresh_qlib_prediction_disagrees():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2024-01-02', '000001', -0.2, 650, 0.30, FALSE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('rule_buy_low_qlib', 'trend_following', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)

    result = arbitrate_pending_signals(conn, config=DEFAULT_CONFIG)

    assert result.accepted == 0
    assert result.rejected == 1
    decision = conn.execute("""
        SELECT decision, consensus_status, decision_reason, qlib_rank, qlib_confidence
        FROM signal_decisions WHERE signal_id = 'rule_buy_low_qlib'
    """).fetchone()
    signal = conn.execute("""
        SELECT executed, status, status_reason
        FROM signals WHERE signal_id = 'rule_buy_low_qlib'
    """).fetchone()

    assert decision[0] == "REJECTED"
    assert decision[1] == "DIVERGENCE"
    assert "Qlib共识不足" in decision[2]
    assert decision[3] == 650
    assert decision[4] == 0.30
    assert signal[0] is True
    assert signal[1] == "NO_ACTION"
    assert "Qlib共识不足" in signal[2]
    conn.close()


def test_arbiter_accepts_rule_buy_when_qlib_agrees():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2024-01-02', '000001', 0.8, 10, 0.80, TRUE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('rule_buy_high_qlib', 'trend_following', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)

    result = arbitrate_pending_signals(conn, config=DEFAULT_CONFIG)

    assert result.accepted == 1
    assert result.rejected == 0
    decision = conn.execute("""
        SELECT decision, consensus_status, decision_reason
        FROM signal_decisions WHERE signal_id = 'rule_buy_high_qlib'
    """).fetchone()
    signal = conn.execute("""
        SELECT executed, status
        FROM signals WHERE signal_id = 'rule_buy_high_qlib'
    """).fetchone()

    assert decision[0] == "ACCEPTED"
    assert decision[1] == "CONSENSUS"
    assert "Alpha158 rank=10" in decision[2]
    assert signal == (False, "ACTIVE")
    conn.close()


def test_arbiter_rejects_buy_when_same_symbol_has_sell():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2024-01-02', '000001', 0.8, 10, 0.80, TRUE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('rule_buy_conflict', 'trend_following', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE'),
          ('alpha_sell_conflict', 'alpha158', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:01:00', 'SELL', 1, 0.60, 0.00, FALSE, 'ACTIVE')
    """)

    result = arbitrate_pending_signals(conn, config=DEFAULT_CONFIG)

    assert result.accepted == 1
    assert result.rejected == 1
    rows = {
        row[0]: row[1:]
        for row in conn.execute("""
            SELECT signal_id, decision, consensus_status, decision_reason
            FROM signal_decisions
            ORDER BY signal_id
        """).fetchall()
    }
    assert rows["alpha_sell_conflict"][0] == "ACCEPTED"
    assert rows["rule_buy_conflict"][0] == "REJECTED"
    assert rows["rule_buy_conflict"][1] == "CONFLICT_SELL"
    assert "SELL风险释放" in rows["rule_buy_conflict"][2]
    conn.close()


def test_arbiter_keeps_only_best_same_symbol_same_side_signal():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2024-01-02', '000001', 0.8, 10, 0.80, TRUE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('rule_buy_lower', 'mean_reversion', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 0.8, 0.85, 0.10, FALSE, 'ACTIVE'),
          ('rule_buy_best', 'trend_following', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1.0, 0.95, 0.10, FALSE, 'ACTIVE')
    """)

    result = arbitrate_pending_signals(conn, config=DEFAULT_CONFIG)

    assert result.accepted == 1
    assert result.rejected == 1
    rows = {
        row[0]: row[1:]
        for row in conn.execute("""
            SELECT signal_id, decision, decision_reason
            FROM signal_decisions
            ORDER BY signal_id
        """).fetchall()
    }
    assert rows["rule_buy_best"][0] == "ACCEPTED"
    assert rows["rule_buy_lower"][0] == "REJECTED"
    assert "同标的同方向" in rows["rule_buy_lower"][1]
    conn.close()


def test_arbiter_keeps_all_sell_signals_for_same_symbol():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('trend_sell', 'trend_following', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'SELL', 1, 0.90, 0.00, FALSE, 'ACTIVE'),
          ('alpha_sell', 'alpha158', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:01:00', 'SELL', 1, 0.80, 0.00, FALSE, 'ACTIVE')
    """)

    result = arbitrate_pending_signals(conn, config=DEFAULT_CONFIG)

    assert result.accepted == 2
    assert result.rejected == 0
    rows = conn.execute("""
        SELECT signal_id, decision, consensus_status
        FROM signal_decisions
        ORDER BY signal_id
    """).fetchall()
    assert rows == [
        ("alpha_sell", "ACCEPTED", "SELL"),
        ("trend_sell", "ACCEPTED", "SELL"),
    ]
    conn.close()
