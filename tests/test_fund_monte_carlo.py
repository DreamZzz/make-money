"""G7: 蒙特卡洛模拟测试。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np

from src.data_pipeline.loader import init_db
from src.funds.monte_carlo import _block_bootstrap_paths, _max_drawdown, simulate_portfolio


def test_max_drawdown_basic():
    # 1.0 → 1.2 → 0.9 → 1.1;最大回撤从 1.2 到 0.9 = -25%
    path = np.array([1.0, 1.2, 0.9, 1.1])
    assert abs(_max_drawdown(path) - (-0.25)) < 1e-9


def test_max_drawdown_monotonic_up():
    path = np.array([1.0, 1.1, 1.2, 1.3])
    assert _max_drawdown(path) == 0.0  # 无回撤


def test_block_bootstrap_paths_shape_and_starts_at_one():
    daily = np.random.RandomState(1).normal(0, 0.01, 500)
    paths = _block_bootstrap_paths(daily, n_paths=100, horizon=252, block_size=5, seed=1)
    assert paths.shape == (100, 253)  # 1 + horizon
    assert np.all(paths[:, 0] == 1.0)  # 第 0 天净值 1


def test_block_bootstrap_paths_seed_reproducible():
    daily = np.random.RandomState(1).normal(0, 0.01, 500)
    p1 = _block_bootstrap_paths(daily, n_paths=10, horizon=50, block_size=5, seed=42)
    p2 = _block_bootstrap_paths(daily, n_paths=10, horizon=50, block_size=5, seed=42)
    assert np.allclose(p1, p2)


def _seed_two_holdings_with_long_history(conn, monkeypatch, *,
                                          a_vol=0.015, b_vol=0.005):
    from src.index_funds.config import FundWatchItem
    items = [
        FundWatchItem(fund_code="A", name="A", fund_type="ETF",
                      tracking_index="000300", tracking_index_name="x",
                      market="CN", currency="CNY", target_weight=0.0,
                      category="equity_index", intent="active"),
        FundWatchItem(fund_code="B", name="B", fund_type="ETF",
                      tracking_index="000300", tracking_index_name="x",
                      market="CN", currency="CNY", target_weight=0.0,
                      category="equity_index", intent="active"),
    ]
    monkeypatch.setattr("src.funds.evaluation.get_watchlist", lambda: items)
    base = date(2026, 5, 29) - timedelta(days=1500)  # 4+ 年
    np.random.seed(7)
    nav_a = nav_b = 1.0
    rows = []
    for i in range(1500):
        d = base + timedelta(days=i)
        nav_a *= 1 + np.random.normal(0.0003, a_vol)
        nav_b *= 1 + np.random.normal(0.0002, b_vol)
        rows.append(("A", d, nav_a))
        rows.append(("B", d, nav_b))
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?,?,?)", rows)
    for code in ["A", "B"]:
        conn.execute(
            "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
            "VALUES (?, ?, 'ETF', '000300', 'CN', 'CNY', TRUE)",
            [code, code],
        )
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('SA', DATE '2026-05-29', 'A', ?, 50000)",
        [50000 / nav_a],
    )
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('SB', DATE '2026-05-29', 'B', ?, 50000)",
        [50000 / nav_b],
    )
    conn.execute(
        "INSERT INTO account_daily (account_id, trade_date, cash, position_value, total_value, "
        "net_contribution, nav, daily_return, drawdown) "
        "VALUES ('default', DATE '2026-05-29', 0, 100000, 100000, 100000, 1, 0, 0)",
    )


def test_simulate_returns_valid_percentiles(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_two_holdings_with_long_history(conn, monkeypatch)
    r = simulate_portfolio(conn, n_paths=500)
    # 完整结构
    assert r.return_percentiles
    assert {"p5", "p25", "p50", "p75", "p95"} <= set(r.return_percentiles.keys())
    assert r.drawdown_percentiles
    # 分位单调
    p = r.return_percentiles
    assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]
    # 中位数与期望接近(大样本)
    assert abs(r.expected_return - p["p50"]) < 0.20
    # prob_loss 在 [0, 1]
    assert 0 <= r.prob_loss <= 1
    # 历史足够,无 short_history 标
    assert "short_history" not in r.risk_tags


def test_simulate_no_holdings():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    r = simulate_portfolio(conn, n_paths=100)
    assert r.n_paths == 0
    assert "no_holdings" in r.risk_tags


def test_simulate_short_history_rejects(monkeypatch):
    """nav 只有 100 天 → 拒绝模拟。"""
    from src.index_funds.config import FundWatchItem
    monkeypatch.setattr("src.funds.evaluation.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="X", name="X", fund_type="ETF",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.0,
                            category="equity_index", intent="active")])
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('X', 'X', 'ETF', '000300', 'CN', 'CNY', TRUE)"
    )
    base = date(2026, 5, 29) - timedelta(days=100)
    rows = [("X", base + timedelta(days=i), 1.0 + i * 0.001) for i in range(100)]
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?,?,?)", rows)
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('S', DATE '2026-05-29', 'X', 1000, 1000)"
    )
    conn.execute(
        "INSERT INTO account_daily (account_id, trade_date, cash, position_value, total_value, "
        "net_contribution, nav, daily_return, drawdown) "
        "VALUES ('default', DATE '2026-05-29', 0, 1100, 1100, 1100, 1, 0, 0)"
    )
    r = simulate_portfolio(conn, n_paths=100)
    assert "short_history" in r.risk_tags
