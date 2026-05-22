from __future__ import annotations

from datetime import date, timedelta

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


def _seed_model_registry(
    conn: duckdb.DuckDBPyConnection,
    *,
    model_name: str = "alpha158",
    model_version: str = "alpha158-prod",
) -> None:
    conn.execute(
        """
        INSERT INTO qlib_model_registry (
            model_version, experiment_id, model_name, status, market, published_at
        )
        VALUES (?, ?, ?, 'production', 'CN', TIMESTAMP '2026-05-20 22:30:00')
        """,
        [model_version, f"EXP-{model_version}", model_name],
    )


def _seed_prediction(
    conn: duckdb.DuckDBPyConnection,
    *,
    model_name: str = "alpha158",
    model_version: str = "alpha158-prod",
    symbol: str = "000001",
    rank: int = 100,
    confidence: float = 0.60,
    prediction_date: date = date(2026, 5, 20),
    selected: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES (?, ?, ?, 'production_inference', ?, ?, 0.8, ?, ?, ?)
        """,
        [
            f"EXP-{model_version}",
            model_name,
            model_version,
            prediction_date,
            symbol,
            rank,
            confidence,
            selected,
        ],
    )


def _seed_production_prediction(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str = "000001",
    rank: int = 100,
    confidence: float = 0.60,
) -> None:
    _seed_model_registry(conn)
    _seed_prediction(conn, symbol=symbol, rank=rank, confidence=confidence)


def _insert_signal(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    model_name: str,
    symbol: str,
    side: str = "BUY",
    confidence: float = 0.90,
    score: float = 0.90,
    signal_ts: str = "2026-05-20 15:00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES (?, ?, '1.0', ?, ?, ?, ?, ?, 0.10, FALSE, 'ACTIVE')
        """,
        [signal_id, model_name, symbol, signal_ts, side, score, confidence],
    )


def _arbiter_config(consensus_baselines: list[str]) -> dict:
    return {
        "portfolio": {
            "min_rebalance_buy_confidence": 0.75,
            "min_rebalance_buy_rank_score": 0.50,
            "signal_arbiter": {
                "enabled": True,
                "consensus_baselines": consensus_baselines,
                "max_prediction_stale_days": 3,
                "max_rule_buy_rank": 500,
                "min_rule_buy_confidence": 0.45,
                "block_when_missing": True,
            },
        }
    }


def test_arbiter_rejects_rule_buy_when_fresh_qlib_prediction_disagrees():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    _seed_model_registry(conn)
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
    _seed_model_registry(conn)
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
    _seed_model_registry(conn)
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
    _seed_model_registry(conn)
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


def test_arbiter_uses_current_production_model_when_other_predictions_are_newer():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_model_registry (
            model_version, experiment_id, model_name, status, market, published_at
        )
        VALUES ('alpha158-prod', 'EXP-PROD', 'alpha158', 'production', 'CN', TIMESTAMP '2026-05-20 22:30:00')
    """)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES
          ('EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
           DATE '2026-05-20', '000001', 0.9, 1, 0.95, TRUE),
          ('EXP-CANDIDATE', 'alpha158', 'alpha158-candidate', 'production_inference',
           DATE '2026-05-21', '000001', -0.2, 650, 0.30, FALSE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('rule_buy_refresh', 'trend_following', '1.0', '000001',
                TIMESTAMP '2026-05-20 15:00:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)
    conn.execute("""
        INSERT INTO signal_decisions (
            decision_id, signal_id, decision_date, model_name, symbol, side,
            signal_ts, decision, decision_reason, consensus_status,
            arbiter_version, qlib_prediction_date, qlib_rank, qlib_confidence
        )
        VALUES (
            'DEC-OLD', 'rule_buy_refresh', DATE '2026-05-20', 'trend_following', '000001', 'BUY',
            TIMESTAMP '2026-05-20 15:00:00', 'REJECTED', '旧模型过期', 'STALE',
            'signal_arbiter_v1', DATE '2026-05-15', 650, 0.30
        )
    """)

    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=DEFAULT_CONFIG)

    row = conn.execute("""
        SELECT decision, consensus_status, qlib_prediction_date, qlib_rank, qlib_confidence
        FROM signal_decisions
        WHERE signal_id = 'rule_buy_refresh'
    """).fetchone()
    assert result.accepted == 1
    assert row == ("ACCEPTED", "CONSENSUS", date(2026, 5, 20), 1, 0.95)
    conn.close()


def test_alpha158_consensus_baseline_preserves_rule_buy_behavior():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    _seed_production_prediction(conn, symbol="000001", rank=100, confidence=0.60)
    _insert_signal(
        conn,
        signal_id="trend-buy",
        model_name="trend_following",
        symbol="000001",
        confidence=0.90,
        score=0.90,
    )
    _insert_signal(
        conn,
        signal_id="alpha-buy",
        model_name="alpha158",
        symbol="000002",
        confidence=0.90,
        score=0.90,
    )

    result = arbitrate_pending_signals(
        conn,
        as_of=date(2026, 5, 20),
        config=_arbiter_config(["alpha158"]),
    )

    assert result.accepted == 2
    rows = {
        row[0]: row[1:]
        for row in conn.execute("""
            SELECT signal_id, decision, consensus_status
            FROM signal_decisions
            WHERE signal_id IN ('trend-buy', 'alpha-buy')
            ORDER BY signal_id
        """).fetchall()
    }
    assert rows == {
        "alpha-buy": ("ACCEPTED", "BASELINE_SELF"),
        "trend-buy": ("ACCEPTED", "CONSENSUS"),
    }
    conn.close()


def test_rule_buy_accepts_when_any_configured_baseline_agrees():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    _seed_model_registry(conn, model_name="alpha158", model_version="alpha158-prod")
    _seed_model_registry(conn, model_name="low_vol", model_version="low-vol-prod")
    _seed_prediction(
        conn,
        model_name="alpha158",
        model_version="alpha158-prod",
        symbol="000001",
        rank=650,
        confidence=0.30,
    )
    _seed_prediction(
        conn,
        model_name="low_vol",
        model_version="low-vol-prod",
        symbol="000001",
        rank=120,
        confidence=0.62,
    )
    _insert_signal(
        conn,
        signal_id="trend-buy-or",
        model_name="trend_following",
        symbol="000001",
        confidence=0.90,
        score=0.90,
    )

    result = arbitrate_pending_signals(
        conn,
        as_of=date(2026, 5, 20),
        config=_arbiter_config(["alpha158", "low_vol"]),
    )

    assert result.accepted == 1
    reason = conn.execute("""
        SELECT decision_reason
        FROM signal_decisions
        WHERE signal_id = 'trend-buy-or'
    """).fetchone()[0]
    assert "low_vol" in reason
    conn.close()


def test_empty_consensus_baselines_disables_rule_buy_consensus_gate():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    _insert_signal(
        conn,
        signal_id="trend-buy-no-baseline",
        model_name="trend_following",
        symbol="000001",
        confidence=0.90,
        score=0.90,
    )

    result = arbitrate_pending_signals(
        conn,
        as_of=date(2026, 5, 20),
        config=_arbiter_config([]),
    )

    assert result.accepted == 1
    row = conn.execute("""
        SELECT consensus_status
        FROM signal_decisions
        WHERE signal_id = 'trend-buy-no-baseline'
    """).fetchone()
    assert row == ("NO_BASELINE_REQUIRED",)
    conn.close()


def test_regime_policy_blocks_all_buy_signals_before_execution():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    _seed_production_prediction(conn, symbol="000001", rank=10, confidence=0.95)
    _insert_signal(
        conn,
        signal_id="alpha-buy-risk-off",
        model_name="alpha158",
        symbol="000001",
        confidence=0.95,
        score=0.95,
    )
    _insert_signal(
        conn,
        signal_id="trend-sell-risk-off",
        model_name="trend_following",
        symbol="000002",
        side="SELL",
        confidence=0.90,
        score=0.90,
    )
    start = date(2026, 1, 1)
    close = 100.0
    rows = []
    for offset in range(130):
        trade_date = start + timedelta(days=offset)
        close = close * 0.998
        if offset == 129:
            close = 70.0
        rows.append(("000300", trade_date, close))
    conn.executemany(
        "INSERT INTO index_daily (index_code, trade_date, close) VALUES (?, ?, ?)",
        rows,
    )

    config = _arbiter_config(["alpha158"])
    config["portfolio"]["regime_policy"] = {
        "enabled": True,
        "benchmark_index": "000300",
        "lookback_days": 260,
    }
    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=config)

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
    assert rows["trend-sell-risk-off"][0] == "ACCEPTED"
    assert rows["alpha-buy-risk-off"][0] == "REJECTED"
    assert rows["alpha-buy-risk-off"][1] == "MACRO_BLOCK"
    assert "宏观风控暂停BUY" in rows["alpha-buy-risk-off"][2]
    signal = conn.execute("""
        SELECT executed, status, status_reason
        FROM signals WHERE signal_id = 'alpha-buy-risk-off'
    """).fetchone()
    assert signal[0] is True
    assert signal[1] == "NO_ACTION"
    assert "宏观风控暂停BUY" in signal[2]
    conn.close()
