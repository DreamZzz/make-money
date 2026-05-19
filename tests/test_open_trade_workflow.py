from __future__ import annotations

from copy import deepcopy
from datetime import date

import duckdb
import pandas as pd
import pytest

from src.config import DEFAULT_CONFIG
from src.data_pipeline.loader import init_db
from src.portfolio import paper_engine as pe


def _empty_status(status: str) -> pd.DataFrame:
    df = pd.DataFrame()
    df.attrs["source_status"] = status
    return df


def _patch_temp_db(monkeypatch, tmp_path, portfolio_overrides: dict | None = None) -> str:
    cfg = deepcopy(DEFAULT_CONFIG)
    db_path = tmp_path / "market.db"
    cfg["data"]["duckdb_path"] = str(db_path)
    cfg["portfolio"]["initial_capital_cn"] = 100000
    if portfolio_overrides:
        cfg["portfolio"].update(portfolio_overrides)

    from src.data_pipeline import loader
    from src.portfolio import cashbook

    monkeypatch.setattr(loader, "get_config", lambda: cfg)
    monkeypatch.setattr(pe, "_load_config", lambda: cfg)
    monkeypatch.setattr(cashbook, "_load_config", lambda: cfg)
    return str(db_path)


def _seed_paper_engine_failure_db(db_path: str) -> None:
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '空仓卖出'), ('000002', 'CN', '异常买入')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES
          ('000001', DATE '2024-01-03', 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-03', 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('sell_empty', 'trend_following', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'SELL', 1, 0.9, 0.1, FALSE, 'ACTIVE'),
          ('buy_crash', 'trend_following', '1.0', '000002',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.9, 0.1, FALSE, 'ACTIVE')
    """)
    conn.close()


def test_current_position_helpers_ignore_stale_positions_after_strategy_is_flat():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '已清仓')
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2024-01-02', '000001', 1000, 10, 10, 10000)
    """)
    conn.execute("""
        INSERT INTO portfolio_nav (
            strategy_name, trade_date, nav, daily_return, cash, position_value,
            total_value, external_flow, net_contribution, investment_nav, drawdown, sharpe_rolling
        )
        VALUES
            ('alpha158', DATE '2024-01-02', 1, 0, 90000, 10000, 100000, 0, 100000, 1, 0, 0),
            ('alpha158', DATE '2024-01-03', 1, 0, 100000, 0, 100000, 0, 100000, 1, 0, 0)
    """)

    assert pe._latest_position_qty(conn, "alpha158", "000001") == 0.0
    assert pe._load_active_position_symbols(conn) == set()
    conn.close()


def test_current_position_helpers_fallback_to_latest_position_without_nav():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '持仓')
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2024-01-02', '000001', 1000, 10, 10, 10000)
    """)

    assert pe._latest_position_qty(conn, "alpha158", "000001") == 1000.0
    assert pe._load_active_position_symbols(conn) == {"000001"}
    conn.close()


def test_paper_engine_rolls_back_signal_status_when_batch_fails(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    _seed_paper_engine_failure_db(db_path)

    def flaky_open_quote(_conn, symbol, _trade_date):
        if symbol == "000002":
            raise RuntimeError("price source exploded")
        return {"open": 10.0, "pre_close": 10.0, "is_st": False, "is_suspended": False}

    monkeypatch.setattr(pe, "_get_open_quote", flaky_open_quote)

    with pytest.raises(RuntimeError, match="price source exploded"):
        pe.run("trend_following", market="CN")

    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT executed, status, status_reason
            FROM signals WHERE signal_id = 'sell_empty'
        """).fetchone()
    finally:
        conn.close()

    assert row == (False, "ACTIVE", None)


def test_paper_engine_marks_unaffordable_lot_as_no_action(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('688001', 'CN', '高价股')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES ('688001', DATE '2024-01-03', 1000, 1000, 1000, 1000, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('high_price_buy', 'alpha158', '1.0', '688001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 1, 0.05, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 0
    assert result["skipped_lot"] == 1
    assert result["handled_without_order"] == 1
    assert result["pending"] == 0

    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT executed, status, status_reason, execution_date
            FROM signals WHERE signal_id='high_price_buy'
        """).fetchone()
    finally:
        conn.close()

    assert row[0] is True
    assert row[1] == "NO_ACTION"
    assert "不足一手" in row[2]
    assert row[3] == date(2024, 1, 3)


def test_paper_engine_respects_small_account_stock_count_limit(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path, {"risk_profile": "small"})
    conn = duckdb.connect(db_path)
    init_db(conn)
    rows = ", ".join([f"('00000{i}', 'CN', '持仓{i}')" for i in range(1, 7)])
    conn.execute(f"INSERT INTO stock_info (symbol, country, name) VALUES {rows}")
    price_rows = ", ".join([
        f"('00000{i}', DATE '2024-01-03', 10, 10, 10, 10, 1000)"
        for i in range(1, 7)
    ])
    conn.execute(f"""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES {price_rows}
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 50000, 50000, 100000, 100000, 1, 0, 0)
    """)
    position_rows = ", ".join([
        f"('alpha158', DATE '2024-01-02', '00000{i}', 1000, 10, 10, 10000)"
        for i in range(1, 6)
    ])
    conn.execute(f"""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES {position_rows}
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('new_stock_buy', 'alpha158', '1.0', '000006',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 1, 0.20, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 0
    assert result["skipped_profile"] == 1
    assert result["handled_without_order"] == 1

    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT status, status_reason
            FROM signals WHERE signal_id='new_stock_buy'
        """).fetchone()
    finally:
        conn.close()

    assert row[0] == "NO_ACTION"
    assert "小资金档" in row[1]
    assert "持仓数量上限" in row[1]


def test_paper_engine_marks_cn_limit_open_as_no_action(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '涨停股')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES ('000001', DATE '2024-01-03', 11, 11, 11, 11, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('limit_buy', 'alpha158', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 1, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 0
    assert result["skipped_untradeable"] == 1
    assert result["handled_without_order"] == 1
    assert result["pending"] == 0

    conn = duckdb.connect(db_path, read_only=True)
    try:
        signal = conn.execute("""
            SELECT executed, status, status_reason, execution_date
            FROM signals WHERE signal_id='limit_buy'
        """).fetchone()
        orders = conn.execute("SELECT COUNT(*) FROM paper_orders WHERE signal_id='limit_buy'").fetchone()[0]
    finally:
        conn.close()

    assert signal[0] is True
    assert signal[1] == "NO_ACTION"
    assert "涨跌停" in signal[2]
    assert signal[3] == date(2024, 1, 3)
    assert orders == 0


def test_paper_engine_caps_buys_by_latest_satellite_budget(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '预算内'), ('000002', 'CN', '预算外')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES
          ('000001', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO allocation_plans (
            plan_id, plan_date, account_id, total_value, cash,
            core_target_pct, satellite_target_pct, core_value, satellite_value,
            core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        )
        VALUES (
            'ALLOC-DEFAULT-20240102', DATE '2024-01-02', 'default', 100000, 100000,
            0.6, 0.4, 0, 0, 85000, 15000, -0.6, -0.4, 'ACTIVE'
        )
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('buy_budget_first', 'alpha158', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 1, 0.10, FALSE, 'ACTIVE'),
          ('buy_budget_second', 'alpha158', '1.0', '000002',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 1
    assert result["skipped_budget"] == 1
    assert result["pending"] == 1

    conn = duckdb.connect(db_path, read_only=True)
    try:
        orders = conn.execute("""
            SELECT signal_id, side, order_value
            FROM paper_orders
            ORDER BY order_ts
        """).fetchall()
        statuses = dict(conn.execute("""
            SELECT signal_id, status
            FROM signals
            ORDER BY signal_id
        """).fetchall())
    finally:
        conn.close()

    assert orders == [("buy_budget_first", "BUY", 10000.0)]
    assert statuses == {"buy_budget_first": "FILLED", "buy_budget_second": "ACTIVE"}


def test_paper_engine_allows_sells_when_satellite_budget_is_zero(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '持仓股')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES ('000001', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 0, 10000, 10000, 10000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2024-01-02', '000001', 1000, 10, 10, 10000)
    """)
    conn.execute("""
        INSERT INTO allocation_plans (
            plan_id, plan_date, account_id, total_value, cash,
            core_target_pct, satellite_target_pct, core_value, satellite_value,
            core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        )
        VALUES (
            'ALLOC-DEFAULT-20240102', DATE '2024-01-02', 'default', 10000, 0,
            0.6, 0.4, 0, 10000, 0, 0, -0.6, 0.6, 'ACTIVE'
        )
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('sell_with_zero_budget', 'alpha158', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'SELL', 1, 1, 0, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 1
    assert result["skipped_budget"] == 0
    conn = duckdb.connect(db_path, read_only=True)
    try:
        order = conn.execute("""
            SELECT signal_id, side, order_value
            FROM paper_orders
            WHERE signal_id = 'sell_with_zero_budget'
        """).fetchone()
    finally:
        conn.close()

    assert order == ("sell_with_zero_budget", "SELL", 10000.0)


def test_paper_engine_adds_sell_proceeds_to_satellite_buy_budget(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '卖出释放'), ('000002', 'CN', '买入承接')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES
          ('000001', DATE '2024-01-03', 11, 11, 11, 11, 11, 1000),
          ('000002', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 0, 11000, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2024-01-02', '000001', 1000, 10, 11, 11000)
    """)
    conn.execute("""
        INSERT INTO allocation_plans (
            plan_id, plan_date, account_id, total_value, cash,
            core_target_pct, satellite_target_pct, core_value, satellite_value,
            core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        )
        VALUES (
            'ALLOC-DEFAULT-20240102', DATE '2024-01-02', 'default', 100000, 0,
            0.6, 0.4, 0, 11000, 0, 0, -0.6, -0.29, 'ACTIVE'
        )
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('sell_to_fund_buy', 'alpha158', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'SELL', 1, 1, 0, FALSE, 'ACTIVE'),
          ('buy_after_sell', 'alpha158', '1.0', '000002',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 2
    assert result["skipped_budget"] == 0
    conn = duckdb.connect(db_path, read_only=True)
    try:
        orders = conn.execute("""
            SELECT signal_id, side, order_value
            FROM paper_orders
            ORDER BY order_ts
        """).fetchall()
    finally:
        conn.close()

    assert orders == [
        ("sell_to_fund_buy", "SELL", 11000.0),
        ("buy_after_sell", "BUY", 10000.0),
    ]


def test_paper_engine_caps_buys_by_daily_turnover_and_keeps_high_confidence(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path, {"max_daily_turnover_pct": 0.15})
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '低分'), ('000002', 'CN', '高分')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES
          ('000001', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('low_conf_older', 'alpha158', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.80, 0.10, FALSE, 'ACTIVE'),
          ('high_conf_newer', 'alpha158', '1.0', '000002',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1, 0.99, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 1
    assert result["skipped_turnover"] == 1
    conn = duckdb.connect(db_path, read_only=True)
    try:
        orders = conn.execute("""
            SELECT signal_id, side, order_value
            FROM paper_orders
            ORDER BY order_ts
        """).fetchall()
        statuses = dict(conn.execute("""
            SELECT signal_id, status
            FROM signals
            ORDER BY signal_id
        """).fetchall())
    finally:
        conn.close()

    assert orders == [("high_conf_newer", "BUY", 10000.0)]
    assert statuses == {"high_conf_newer": "FILLED", "low_conf_older": "ACTIVE"}


def test_paper_engine_allows_sells_when_buy_turnover_cap_is_low(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path, {"max_daily_turnover_pct": 0.01})
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '持仓股')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES ('000001', DATE '2024-01-03', 10, 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 0, 10000, 10000, 10000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2024-01-02', '000001', 1000, 10, 10, 10000)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('sell_low_turnover_cap', 'alpha158', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'SELL', 1, 1, 0, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 1
    assert result["skipped_turnover"] == 0


def test_paper_engine_deduplicates_same_strategy_symbol_side_execution_day(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('600808', 'CN', '马钢股份')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES ('600808', DATE '2024-01-03', 3.33, 3.33, 3.33, 3.33, 3.30, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('duplicate_lower', 'mean_reversion', '1.0', '600808',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 0.8, 0.85, 0.10, FALSE, 'ACTIVE'),
          ('duplicate_best', 'mean_reversion', '1.0', '600808',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1.0, 0.90, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("mean_reversion", market="CN")

    assert result["executed"] == 1
    assert result["handled_without_order"] == 1
    assert result["pending"] == 0

    conn = duckdb.connect(db_path, read_only=True)
    try:
        orders = conn.execute("""
            SELECT signal_id, symbol, side, order_value
            FROM paper_orders
            ORDER BY order_ts
        """).fetchall()
        statuses = {
            row[0]: row[1:]
            for row in conn.execute("""
            SELECT signal_id, executed, status, status_reason
            FROM signals
            ORDER BY signal_id
        """).fetchall()
        }
    finally:
        conn.close()

    assert orders == [("duplicate_best", "600808", "BUY", 9990.0)]
    assert statuses["duplicate_best"] == (True, "FILLED", "成交")
    assert statuses["duplicate_lower"][0] is True
    assert statuses["duplicate_lower"][1] == "NO_ACTION"
    assert "同日同标的同方向" in statuses["duplicate_lower"][2]


def test_paper_engine_deduplicate_keeps_different_strategies_separate(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('600808', 'CN', '马钢股份')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pre_close, volume)
        VALUES ('600808', DATE '2024-01-03', 3.33, 3.33, 3.33, 3.33, 3.30, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2024-01-02', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('mean_reversion_buy', 'mean_reversion', '1.0', '600808',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.90, 0.10, FALSE, 'ACTIVE'),
          ('alpha158_buy', 'alpha158', '1.0', '600808',
           TIMESTAMP '2024-01-02 15:01:00', 'BUY', 1, 0.90, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run_all_strategies(initial_capital=100000)

    assert result["mean_reversion"]["executed"] == 1
    assert result["alpha158"]["executed"] == 1

    conn = duckdb.connect(db_path, read_only=True)
    try:
        orders = conn.execute("""
            SELECT signal_id, symbol, side
            FROM paper_orders
            ORDER BY order_ts
        """).fetchall()
    finally:
        conn.close()

    assert orders == [
        ("alpha158_buy", "600808", "BUY"),
        ("mean_reversion_buy", "600808", "BUY"),
    ]


def test_prioritize_signals_releases_cash_before_older_buys():
    signals = pd.DataFrame({
        "signal_id": ["old_buy", "new_sell"],
        "symbol": ["000002", "000001"],
        "side": ["BUY", "SELL"],
        "signal_date": [date(2024, 1, 2), date(2024, 1, 3)],
        "confidence": [0.99, 0.60],
        "score": [0.99, 0.60],
    })

    ordered = pe._prioritize_signals(signals)

    assert ordered["signal_id"].tolist() == ["new_sell", "old_buy"]


def test_execution_price_can_use_market_snapshot_without_daily_price():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '平安银行')")
    conn.execute("""
        INSERT INTO market_snapshot (
            symbol, market, trade_date, update_time, last_price, open, high, low, source
        )
        VALUES ('000001', 'CN', DATE '2024-01-03',
                TIMESTAMP '2024-01-03 09:31:00', 10.5, 10.2, 10.8, 10.1, 'open_snapshot')
    """)

    assert pe._get_next_trading_day(conn, date(2024, 1, 2), "CN") == date(2024, 1, 3)
    assert pe._get_open_price(conn, "000001", date(2024, 1, 3)) == 10.2
    conn.close()


def test_open_target_fetch_opens_yfinance_circuit_after_rate_limit(monkeypatch):
    from scripts import open_paper_trade as opt

    calls = {"yf": 0}

    monkeypatch.setattr(opt.ak, "fetch_cn_stock_daily", lambda *_args, **_kwargs: _empty_status("source_error"))

    def fake_yf(*_args, **_kwargs):
        calls["yf"] += 1
        return _empty_status("rate_limited")

    monkeypatch.setattr(opt.yf, "fetch_cn_daily", fake_yf)

    state = opt.FetchState()
    first, first_source = opt._fetch_symbol("000001", "CN", date(2024, 1, 3), date(2024, 1, 3), state=state)
    second, second_source = opt._fetch_symbol("000002", "CN", date(2024, 1, 3), date(2024, 1, 3), state=state)

    assert first.empty and second.empty
    assert calls["yf"] == 1
    assert "yfinance:rate_limited" in first_source
    assert "yfinance:circuit_skip" in second_source


def test_open_target_fetch_hk_keeps_akshare_fallback_after_yfinance_circuit(monkeypatch):
    from scripts import open_paper_trade as opt

    calls = {"yf": 0, "ak": 0}

    def fake_hk_yf(*_args, **_kwargs):
        calls["yf"] += 1
        return _empty_status("rate_limited")

    def fake_hk_ak(symbol, *_args, **_kwargs):
        calls["ak"] += 1
        return pd.DataFrame({
            "symbol": [symbol],
            "trade_date": [pd.Timestamp("2024-01-03")],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [1000],
        })

    monkeypatch.setattr(opt.yf, "fetch_hk_daily", fake_hk_yf)
    monkeypatch.setattr(opt.ak, "fetch_hk_stock_daily", fake_hk_ak)

    state = opt.FetchState()
    first, first_source = opt._fetch_symbol("00700", "HK", date(2024, 1, 3), date(2024, 1, 3), state=state)
    second, second_source = opt._fetch_symbol("09988", "HK", date(2024, 1, 3), date(2024, 1, 3), state=state)

    assert not first.empty and not second.empty
    assert calls == {"yf": 1, "ak": 2}
    assert "yfinance:rate_limited" in first_source
    assert "yfinance:circuit_skip" in second_source


def test_open_target_update_summary_marks_partial_data_as_degraded():
    from scripts.open_paper_trade import OpenTargetUpdateSummary

    summary = OpenTargetUpdateSummary(targets=62, updated=38, no_data=24, skipped=0, snapshot_ready=0)

    assert summary.status == "DEGRADED"
    assert summary.exit_code == 2
    assert summary.to_log_line() == "目标行情更新汇总: targets=62 updated=38 no_data=24 skipped=0 snapshot_ready=0 status=DEGRADED"


def test_open_target_update_expires_stale_signals_before_loading_targets(monkeypatch, tmp_path):
    from scripts import open_paper_trade as opt

    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '过期信号'), ('000002', 'CN', '有效信号')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES
          ('000001', DATE '2024-01-03', 10, 10, 10, 10, 1000),
          ('000001', DATE '2024-01-04', 10, 10, 10, 10, 1000),
          ('000001', DATE '2024-01-05', 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-05', 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES
          ('stale_buy', 'trend_following', '1.0', '000001',
           TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.9, 0.1, FALSE, 'ACTIVE'),
          ('fresh_buy', 'trend_following', '1.0', '000002',
           TIMESTAMP '2024-01-04 15:00:00', 'BUY', 1, 0.9, 0.1, FALSE, 'ACTIVE')
    """)
    conn.close()

    fetched: list[str] = []

    def fake_fetch(symbol, *_args, **_kwargs):
        fetched.append(symbol)
        return pd.DataFrame(), "test:no_data"

    monkeypatch.setattr(opt, "_fetch_symbol", fake_fetch)

    assert opt._update_target_symbols() == 2
    assert fetched == ["000002"]

    conn = duckdb.connect(db_path, read_only=True)
    try:
        rows = dict(conn.execute("""
            SELECT signal_id, status FROM signals ORDER BY signal_id
        """).fetchall())
    finally:
        conn.close()

    assert rows == {"fresh_buy": "ACTIVE", "stale_buy": "EXPIRED"}
