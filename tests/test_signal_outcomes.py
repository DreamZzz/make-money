from datetime import date

import duckdb
import pytest

from src.data_pipeline.loader import init_db
from src.signals.outcome_tracker import update_signal_outcomes


def test_update_signal_outcomes_persists_buy_forward_returns_and_pending_horizons():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '测试股')")
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts, side,
            executed, execution_price, execution_date, status
        )
        VALUES (
            'buy_signal', 'alpha158', '1.0', '000001', TIMESTAMP '2026-05-01 15:00:00',
            'BUY', TRUE, 10, DATE '2026-05-02', 'FILLED'
        )
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close)
        VALUES
            ('000001', DATE '2026-05-03', 11),
            ('000001', DATE '2026-05-04', 12),
            ('000001', DATE '2026-05-05', 13)
    """)

    result = update_signal_outcomes(conn, horizons=(1, 5))
    rows = conn.execute("""
        SELECT signal_id, horizon_days, outcome_date, outcome_price, return_pct, status
        FROM signal_outcomes
        ORDER BY horizon_days
    """).fetchall()

    assert result == {"updated": 2, "ready": 1, "pending": 1}
    assert rows[0] == pytest.approx(("buy_signal", 1, date(2026, 5, 3), 11, 0.1, "READY"))
    assert rows[1][:2] == ("buy_signal", 5)
    assert rows[1][5] == "PENDING"
    conn.close()


def test_update_signal_outcomes_scores_sell_positive_when_price_falls():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '测试股')")
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts, side,
            executed, execution_price, execution_date, status
        )
        VALUES (
            'sell_signal', 'trend', '1.0', '000001', TIMESTAMP '2026-05-01 15:00:00',
            'SELL', TRUE, 10, DATE '2026-05-02', 'FILLED'
        )
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close)
        VALUES ('000001', DATE '2026-05-03', 8)
    """)

    result = update_signal_outcomes(conn, horizons=(1,))
    row = conn.execute("""
        SELECT return_pct, status
        FROM signal_outcomes
        WHERE signal_id = 'sell_signal' AND horizon_days = 1
    """).fetchone()

    assert result == {"updated": 1, "ready": 1, "pending": 0}
    assert row == pytest.approx((0.25, "READY"))
    conn.close()
