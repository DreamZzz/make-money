"""G1: 基金每日入口 — 端到端 smoke。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb

from src.data_pipeline.loader import init_db
from src.funds.daily import run_daily


def _seed_minimal(conn):
    conn.execute("INSERT INTO market_state (trade_date, benchmark, stage, stage_score) "
                 "VALUES (DATE '2026-05-29', '000300', '强势上升', 100)")
    conn.execute("INSERT INTO market_exposure (trade_date, benchmark, target_exposure, action) "
                 "VALUES (DATE '2026-05-29', '000300', 0.87, 'ADD')")
    conn.execute("INSERT INTO account_daily (account_id, trade_date, cash, position_value, "
                 "total_value, net_contribution, nav, daily_return, drawdown) "
                 "VALUES ('default', DATE '2026-05-29', 100000, 400000, 500000, 500000, 1, 0, 0)")
    conn.execute("INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, "
                 "etf_subcategory, market, currency, enabled) "
                 "VALUES ('X','测试','ETF','000300','broad','CN','CNY',TRUE)")
    base = date(2026, 5, 29) - timedelta(days=200)
    rows = [("000300", base + timedelta(days=i), 100 + i * 0.05) for i in range(200)]
    conn.executemany("INSERT INTO index_daily (index_code, trade_date, close) VALUES (?,?,?)", rows)
    rows = [("X", base + timedelta(days=i), 1.0 + i * 0.001) for i in range(200)]
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?,?,?)", rows)


def test_run_daily_returns_ok_and_populates_tables(monkeypatch):
    from src.index_funds.config import FundWatchItem
    monkeypatch.setattr("src.funds.evaluation.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="X", name="测试", fund_type="ETF",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33,
                            category="equity_index", intent="active")])
    monkeypatch.setattr("src.funds.monitoring.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="X", name="测试", fund_type="ETF",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33,
                            category="equity_index", intent="active")])
    monkeypatch.setattr("src.funds.recommendations.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="X", name="测试", fund_type="ETF",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33,
                            category="equity_index", intent="active")])
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_minimal(conn)

    out = run_daily(conn)
    assert out["status"] == "OK"
    assert out["scanner_count"] >= 1
    # fund_screening_results 落库
    n = conn.execute("SELECT COUNT(*) FROM fund_screening_results").fetchone()[0]
    assert n >= 1
    conn.close()
