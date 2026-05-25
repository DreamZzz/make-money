from __future__ import annotations

from datetime import date

import duckdb

from src.data_pipeline.loader import init_db
from src.portfolio.chain_health import check_recent_days, check_trading_day


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_db(conn)
    return conn


def _signal(conn, sid, model, symbol, side="BUY", status="FILLED", executed=True, exec_date="2026-05-20"):
    conn.execute(
        """
        INSERT INTO signals (signal_id, model_name, model_version, symbol, signal_ts, side,
            score, confidence, max_position_pct, executed, status, execution_date)
        VALUES (?, ?, '1.0', ?, TIMESTAMP '2026-05-19 15:00:00', ?, 0.9, 0.9, 0.1, ?, ?, ?)
        """,
        [sid, model, symbol, side, executed, status, exec_date],
    )


def _order(conn, oid, sid, symbol, side="BUY", ts="2026-05-20 09:30:00"):
    conn.execute(
        """
        INSERT INTO paper_orders (order_id, signal_id, symbol, side, order_qty, order_price, order_ts, status)
        VALUES (?, ?, ?, ?, 100, 10.0, CAST(? AS TIMESTAMP), 'FILLED')
        """,
        [oid, sid, symbol, side, ts],
    )


def test_clean_day():
    conn = _conn()
    _signal(conn, "s1", "trend_following", "000001")
    _signal(conn, "s2", "alpha158", "000002")
    _order(conn, "o1", "s1", "000001")
    _order(conn, "o2", "s2", "000002")
    h = check_trading_day(conn, date(2026, 5, 20))
    assert h.clean is True
    assert h.orders == 2
    assert h.issues == []
    conn.close()


def test_detects_duplicate_same_day_orders():
    conn = _conn()
    # 同模型/标的/方向两笔（600808 双买 bug）
    _signal(conn, "s1", "mean_reversion", "600808")
    _signal(conn, "s2", "mean_reversion", "600808")
    _order(conn, "o1", "s1", "600808")
    _order(conn, "o2", "s2", "600808")
    h = check_trading_day(conn, date(2026, 5, 20))
    assert h.clean is False
    assert h.duplicate_orders == 1
    assert any("重复成交" in i for i in h.issues)
    conn.close()


def test_detects_terminal_status_unexecuted():
    conn = _conn()
    _signal(conn, "s1", "trend_following", "000001", status="NO_ACTION", executed=False)
    h = check_trading_day(conn, date(2026, 5, 20))
    assert h.clean is False
    assert h.terminal_status_unexecuted == 1
    conn.close()


def test_deferred_budget_unexecuted_is_not_flagged():
    # DEFERRED_BUDGET 是 pending（择日重试），executed=FALSE 属正常，不应算违例
    conn = _conn()
    _signal(conn, "s1", "trend_following", "000001", status="DEFERRED_BUDGET", executed=False)
    h = check_trading_day(conn, date(2026, 5, 20))
    assert h.terminal_status_unexecuted == 0
    assert h.clean is True
    conn.close()


def test_detects_midnight_order_ts():
    conn = _conn()
    _signal(conn, "s1", "trend_following", "000001")
    _order(conn, "o1", "s1", "000001", ts="2026-05-20 00:00:00")
    h = check_trading_day(conn, date(2026, 5, 20))
    assert h.clean is False
    assert h.midnight_orders == 1
    conn.close()


def test_clean_streak_breaks_on_dirty_day():
    conn = _conn()
    days = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]
    for d in days:
        conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('000001', ?, 10.0)", [d])
    # 全部干净，但最早一天放一个重复单 → streak 从最近往回数应为 4
    _signal(conn, "d1", "trend_following", "600808", exec_date="2026-05-18")
    _signal(conn, "d2", "trend_following", "600808", exec_date="2026-05-18")
    _order(conn, "od1", "d1", "600808", ts="2026-05-18 09:30:00")
    _order(conn, "od2", "d2", "600808", ts="2026-05-18 09:31:00")

    report = check_recent_days(conn, end_date=date(2026, 5, 22), n=5)
    assert len(report["days"]) == 5
    assert report["clean_streak"] == 4  # 最近4天干净，第5天(最早)有重复单
    assert report["gate_met"] is False
    conn.close()
