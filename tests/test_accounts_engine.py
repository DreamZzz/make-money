from __future__ import annotations

import duckdb

from src.accounts.config import AccountConfig
from src.accounts.engine import rebuild_account_state, run_account_forward
from src.accounts.registry import upsert_account
from src.config import load_config
from src.data_pipeline.loader import init_db


def _seed_market(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001','CN','A'),('000002','CN','B')")
    for sym, op, pc in [("000001", 10.0, 9.9), ("000002", 20.0, 19.8)]:
        for d in ("2026-05-20", "2026-05-21"):
            conn.execute(
                "INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume) "
                f"VALUES (?, DATE '{d}', ?, ?, ?, ?, ?, 1000000)",
                [sym, op, op, op, op, pc],
            )
    return conn


def _insert_signal(conn, signal_id, model_name, symbol, score=0.9, confidence=0.9):
    conn.execute(
        """
        INSERT INTO signals (signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status)
        VALUES (?, ?, '1.0', ?, TIMESTAMP '2026-05-20 15:00:00', 'BUY', ?, ?, 0.10, FALSE, 'ACTIVE')
        """,
        [signal_id, model_name, symbol, score, confidence],
    )


def _alpha_account(account_id: str, capital: float = 100_000.0):
    return AccountConfig(
        models=("alpha158",),
        portfolio_overrides={"allocation": {"core_target_pct": 0.0, "satellite_target_pct": 0.95, "cash_target_pct": 0.05}},
    ), capital


def test_run_account_forward_fills_subscribed_buy():
    conn = _seed_market(conn=duckdb.connect(":memory:"))
    cfg, cap = _alpha_account("acc_a")
    acc = upsert_account(conn, "acc_a", "A", cfg, initial_capital=cap)
    _insert_signal(conn, "sig-a158", "alpha158", "000001")

    result = run_account_forward(conn, acc, config=load_config())

    assert result.accepted == 1
    assert result.filled == 1
    orders = conn.execute(
        "SELECT account_id, symbol, side, order_qty, source FROM account_orders WHERE account_id='acc_a'"
    ).fetchall()
    assert len(orders) == 1
    assert orders[0][1] == "000001"
    assert orders[0][2] == "BUY"
    assert orders[0][4] == "forward"
    conn.close()


def test_accounts_have_isolated_cash():
    conn = _seed_market(conn=duckdb.connect(":memory:"))
    cfg, cap = _alpha_account("acc_a")
    acc_a = upsert_account(conn, "acc_a", "A", cfg, initial_capital=cap)
    acc_b = upsert_account(conn, "acc_b", "B", cfg, initial_capital=cap)
    _insert_signal(conn, "sig-a158", "alpha158", "000001")

    run_account_forward(conn, acc_a, config=load_config())
    run_account_forward(conn, acc_b, config=load_config())

    state_a = rebuild_account_state(conn, "acc_a", cap)
    state_b = rebuild_account_state(conn, "acc_b", cap)
    # 两个账户各自独立成交同一信号，现金相同且都从自己的初始资金扣减（互不影响）
    assert state_a.qty("000001") > 0
    assert state_b.qty("000001") == state_a.qty("000001")
    assert abs(state_a.cash - state_b.cash) < 1e-6
    assert state_a.cash < cap  # 确实扣了钱
    conn.close()


def test_account_only_trades_subscribed_models():
    conn = _seed_market(conn=duckdb.connect(":memory:"))
    cfg, cap = _alpha_account("acc_a")
    acc = upsert_account(conn, "acc_a", "A", cfg, initial_capital=cap)
    # 只有一个 trend_following 信号，alpha158 账户不应订阅/成交它
    _insert_signal(conn, "sig-trend", "trend_following", "000002")

    result = run_account_forward(conn, acc, config=load_config())

    assert result.decisions == 0
    assert conn.execute("SELECT COUNT(*) FROM account_orders WHERE account_id='acc_a'").fetchone()[0] == 0
    conn.close()


def test_rebuild_account_state_replays_orders():
    conn = _seed_market(conn=duckdb.connect(":memory:"))
    conn.execute(
        """
        INSERT INTO account_orders (account_id, order_id, signal_id, symbol, side,
            order_qty, order_price, order_value, fee, order_ts, source, status)
        VALUES
        ('acc_a','o1','s1','000001','BUY',1000,10.0,10000.0,5.0, TIMESTAMP '2026-05-21 09:30:00','forward','FILLED'),
        ('acc_a','o2','s2','000001','SELL',400,11.0,4400.0,5.0, TIMESTAMP '2026-05-22 09:30:00','forward','FILLED')
        """
    )
    state = rebuild_account_state(conn, "acc_a", 100_000.0)
    # 买 1000 卖 400 = 持 600；现金 = 100000 - 10005 + 4395
    assert state.qty("000001") == 600
    assert abs(state.cash - (100_000 - 10_005 + 4_395)) < 1e-6
    conn.close()
