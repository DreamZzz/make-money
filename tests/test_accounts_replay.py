from __future__ import annotations

from datetime import date

import duckdb

from src.accounts.config import AccountConfig
from src.accounts.engine import AccountState
from src.accounts.registry import upsert_account
from src.accounts.replay import (
    generate_historical_alpha158_signals,
    mark_to_market,
    replay_account,
)
from src.config import load_config
from src.data_pipeline.loader import init_db

DAYS = ["2026-05-18", "2026-05-19", "2026-05-20"]


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001','CN','A'),('000002','CN','B')")
    for d in DAYS:
        for sym in ("000001", "000002"):
            conn.execute(
                "INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume) "
                f"VALUES (?, DATE '{d}', 10.0, 10.5, 9.5, 10.0, 9.9, 1000000)",
                [sym],
            )
    # alpha158 历史预测：walk_forward（防 look-ahead 的历史预测源），D1 top-rank 000001
    conn.execute(
        """
        INSERT INTO qlib_predictions (experiment_id, model_name, model_version, mode,
            prediction_date, symbol, score, rank, confidence, selected)
        VALUES ('QLIB-WALK_FORWARD-20260518-TEST','alpha158','alpha158-wf','walk_forward',
                DATE '2026-05-18', '000001', 0.03, 1, 0.7, FALSE)
        """
    )


def _alpha_account(conn, capital=100_000.0):
    cfg = AccountConfig(
        models=("alpha158",),
        portfolio_overrides={"allocation": {"core_target_pct": 0.0, "satellite_target_pct": 0.95, "cash_target_pct": 0.05}},
    )
    return upsert_account(conn, "alpha_pure", "Alpha", cfg, initial_capital=capital)


def test_generate_historical_alpha158_signals():
    conn = duckdb.connect(":memory:")
    _seed(conn)
    sig = generate_historical_alpha158_signals(conn, date(2026, 5, 18), date(2026, 5, 20))
    assert len(sig) == 1
    row = sig.iloc[0]
    assert row["model_name"] == "alpha158"
    assert row["symbol"] == "000001"
    assert row["side"] == "BUY"
    assert abs(row["confidence"] - 0.7) < 1e-9
    conn.close()


def test_mark_to_market_no_lookahead():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001','CN','A')")
    conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('000001', DATE '2026-05-18', 10.0)")
    conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('000001', DATE '2026-05-20', 20.0)")
    state = AccountState(cash=0.0, positions={"000001": {"qty": 100, "avg_cost": 10.0, "price": 10.0}})
    # 在 D1 估值不能看到 D3 的价格
    mark_to_market(conn, state, date(2026, 5, 18))
    assert state.positions["000001"]["price"] == 10.0
    # 到 D3 才用 20
    mark_to_market(conn, state, date(2026, 5, 20))
    assert state.positions["000001"]["price"] == 20.0
    conn.close()


def test_replay_account_end_to_end():
    conn = duckdb.connect(":memory:")
    _seed(conn)
    acc = _alpha_account(conn)
    result = replay_account(conn, acc, date(2026, 5, 18), date(2026, 5, 20), config=load_config())

    assert result.signal_dates == 1
    assert result.orders == 1
    orders = conn.execute(
        "SELECT symbol, side, source FROM account_orders WHERE account_id='alpha_pure'"
    ).fetchall()
    assert orders == [("000001", "BUY", "replay")]

    # NAV 覆盖全部交易日；D1 不应包含 D2 才成交的持仓（防 look-ahead）
    nav = conn.execute(
        "SELECT trade_date, position_value FROM account_nav WHERE account_id='alpha_pure' ORDER BY trade_date"
    ).fetchall()
    assert len(nav) == 3
    assert nav[0][1] == 0.0  # D1 收盘时还没成交
    assert nav[1][1] > 0.0   # D2 成交后有持仓
    conn.close()


def test_replay_is_idempotent():
    conn = duckdb.connect(":memory:")
    _seed(conn)
    acc = _alpha_account(conn)
    replay_account(conn, acc, date(2026, 5, 18), date(2026, 5, 20), config=load_config())
    replay_account(conn, acc, date(2026, 5, 18), date(2026, 5, 20), config=load_config())
    # 重复回放不应翻倍订单
    assert conn.execute(
        "SELECT COUNT(*) FROM account_orders WHERE account_id='alpha_pure' AND source='replay'"
    ).fetchone()[0] == 1
    conn.close()
