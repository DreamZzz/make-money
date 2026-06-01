"""G6: 组合风险归因测试。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np

from src.data_pipeline.loader import init_db
from src.funds.risk_attribution import attribute_portfolio_risk


def _seed_two_holdings(conn, monkeypatch, *,
                       a_vol=0.02, b_vol=0.005,  # 日波动
                       a_value=70000, b_value=30000):
    """seed 两支基金:A 高波动, B 低波动;权重 70/30。"""
    from src.index_funds.config import FundWatchItem
    items = [
        FundWatchItem(fund_code="A", name="高波动", fund_type="ETF",
                      tracking_index="000300", tracking_index_name="x",
                      market="CN", currency="CNY", target_weight=0.0,
                      category="equity_index", intent="active"),
        FundWatchItem(fund_code="B", name="低波动", fund_type="ETF",
                      tracking_index="000300", tracking_index_name="x",
                      market="CN", currency="CNY", target_weight=0.0,
                      category="equity_index", intent="active"),
    ]
    monkeypatch.setattr("src.funds.evaluation.get_watchlist", lambda: items)
    # 300 天 nav,各自波动
    base = date(2026, 5, 29) - timedelta(days=300)
    np.random.seed(42)
    nav_a = 1.0
    nav_b = 1.0
    rows = []
    for i in range(300):
        d = base + timedelta(days=i)
        nav_a *= 1 + np.random.normal(0.0003, a_vol)
        nav_b *= 1 + np.random.normal(0.0001, b_vol)
        rows.append(("A", d, nav_a))
        rows.append(("B", d, nav_b))
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?,?,?)", rows)
    for code in ["A", "B"]:
        conn.execute(
            "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
            "VALUES (?, ?, 'ETF', '000300', 'CN', 'CNY', TRUE)",
            [code, f"测试{code}"],
        )
    # 持仓:A ¥70k / B ¥30k
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('SA', DATE '2026-05-29', 'A', ?, ?)",
        [a_value / nav_a, a_value],
    )
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('SB', DATE '2026-05-29', 'B', ?, ?)",
        [b_value / nav_b, b_value],
    )
    # account 必须存在(evaluation 需要)
    conn.execute(
        "INSERT INTO account_daily (account_id, trade_date, cash, position_value, total_value, "
        "net_contribution, nav, daily_return, drawdown) "
        "VALUES ('default', DATE '2026-05-29', 0, ?, ?, ?, 1, 0, 0)",
        [a_value + b_value, a_value + b_value, a_value + b_value],
    )


def test_risk_attribution_basic(monkeypatch):
    """A 70% 权重 + 4x B 波动 → A 占组合风险应远大于 70%。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_two_holdings(conn, monkeypatch)
    p = attribute_portfolio_risk(conn)
    assert p.portfolio_annual_volatility is not None
    assert p.portfolio_annual_volatility > 0
    assert len(p.sleeves) == 2
    sleeves = {s.fund_code: s for s in p.sleeves}
    # A 市值权重 = 70%, 但 A 自身波动 ~4x B → A 风险贡献应 >> 70%
    assert sleeves["A"].market_weight > 0.6
    assert sleeves["A"].risk_contribution_pct > sleeves["A"].market_weight  # 风险集中
    assert sleeves["A"].risk_to_weight_ratio is not None
    assert sleeves["A"].risk_to_weight_ratio > 1.0
    # B 风险贡献 < B 市值权重
    assert sleeves["B"].risk_contribution_pct < sleeves["B"].market_weight
    # 风险贡献和接近 1
    total_rc = sum(s.risk_contribution_pct for s in p.sleeves)
    assert 0.95 < total_rc < 1.05


def test_risk_attribution_marks_concentration_in_headline(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_two_holdings(conn, monkeypatch, a_vol=0.04, b_vol=0.005)  # 极端
    p = attribute_portfolio_risk(conn)
    assert "risk_concentration" in p.risk_tags
    assert "A" in p.headline or "市值" in p.headline


def test_risk_attribution_no_holdings():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    p = attribute_portfolio_risk(conn)
    assert p.sleeves == []
    assert "无可计算" in p.headline


def test_risk_attribution_correlation_diagonal_one(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_two_holdings(conn, monkeypatch)
    p = attribute_portfolio_risk(conn)
    assert len(p.correlation_matrix) == 2
    # 对角线 ≈ 1
    assert abs(p.correlation_matrix[0][0] - 1.0) < 1e-6
    assert abs(p.correlation_matrix[1][1] - 1.0) < 1e-6
    # 对称
    assert abs(p.correlation_matrix[0][1] - p.correlation_matrix[1][0]) < 1e-9
