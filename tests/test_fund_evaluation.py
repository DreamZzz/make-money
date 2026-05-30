"""D1: 基金评估服务测试。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from src.data_pipeline.loader import init_db
from src.funds.evaluation import evaluate_funds, load_latest_evaluations


def _seed_basic(conn, *, eval_today=date(2026, 5, 29)):
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('012963','沪深300基金A','OPEN','000300','CN','CNY',TRUE)"
    )
    # 100 天指数日线
    start = eval_today - timedelta(days=200)
    rows = []
    close = 100.0
    for i in range(200):
        d = start + timedelta(days=i)
        close *= 1.001 if i % 3 else 0.999
        rows.append(("000300", d, close))
    conn.executemany("INSERT INTO index_daily (index_code, trade_date, close) VALUES (?,?,?)", rows)
    # 净值
    for i in range(20):
        d = eval_today - timedelta(days=i)
        conn.execute("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?,?,?)",
                     ["012963", d, 1.8 + 0.001 * i])
    # 快照(5天前 → 不算 stale)
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES ('S1', ?, '012963', 50000, 90000)",
        [eval_today - timedelta(days=2)],
    )
    # M4 权重
    conn.execute(
        "INSERT INTO index_allocation (trade_date, fund_code, weight, equity_budget) "
        "VALUES (?, '012963', 0.435, 1.0)",
        [eval_today],
    )
    # 宏观目标仓位
    conn.execute(
        "INSERT INTO market_exposure (trade_date, benchmark, target_exposure, action) "
        "VALUES (?, '000300', 0.87, 'ADD')",
        [eval_today],
    )
    # 账户
    conn.execute(
        "INSERT INTO account_daily (account_id, trade_date, cash, position_value, total_value, net_contribution, nav, daily_return, drawdown) "
        "VALUES ('default', ?, 100000, 400000, 500000, 500000, 1.0, 0.0, 0.0)",
        [eval_today],
    )


def test_evaluate_funds_smoke(monkeypatch):
    """端到端:把所有数据 seed 进 in-memory DB,看 evaluation 是否合理。"""
    from src.index_funds.config import FundWatchItem

    def fake_watchlist():
        return [FundWatchItem(
            fund_code="012963", name="沪深300基金A", fund_type="OPEN",
            tracking_index="000300", tracking_index_name="沪深300",
            market="CN", currency="CNY", target_weight=0.33,
        )]
    monkeypatch.setattr("src.funds.evaluation.get_watchlist", fake_watchlist)

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_basic(conn)
    evals = evaluate_funds(conn, eval_date=date(2026, 5, 29))
    assert len(evals) == 1
    e = evals[0]
    assert e.fund_code == "012963"
    assert e.target_weight_m4 == pytest.approx(0.435)
    assert e.equity_exposure == pytest.approx(0.87)
    # target_account_weight = 0.87 * 0.435 ≈ 0.378
    assert e.target_account_weight == pytest.approx(0.87 * 0.435)
    # target_value = 500000 * 0.378 = 189225
    assert e.target_value == pytest.approx(500000 * 0.87 * 0.435)
    # current_value = 50000 * 最新 nav ≈ 50000 * 1.8
    assert e.current_value is not None and e.current_value > 0
    # delta_amount = target_value - current_value
    assert e.delta_amount == pytest.approx(e.target_value - e.current_value)
    assert e.delta_shares == pytest.approx(e.delta_amount / e.nav)
    assert "snapshot_stale" not in e.risk_tags  # 仅 2 天
    conn.close()


def test_evaluate_funds_marks_snapshot_stale(monkeypatch):
    from src.index_funds.config import FundWatchItem
    monkeypatch.setattr("src.funds.evaluation.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="012963", name="x", fund_type="OPEN",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33)])
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_basic(conn)
    # 把快照日期改成 20 天前 → snapshot_stale
    conn.execute("UPDATE index_fund_snapshots SET snapshot_date = ?",
                 [date(2026, 5, 9)])
    evals = evaluate_funds(conn, eval_date=date(2026, 5, 29))
    e = evals[0]
    assert e.snapshot_stale_days == 20
    assert "snapshot_stale" in e.risk_tags
    assert "20 天未刷新" in e.thesis
    conn.close()


def test_evaluate_funds_no_snapshot_still_works(monkeypatch):
    """没快照时,持仓字段为 None 但 evaluation 仍出 (信号、目标值仍可算)。"""
    from src.index_funds.config import FundWatchItem
    monkeypatch.setattr("src.funds.evaluation.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="012963", name="x", fund_type="OPEN",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33)])
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_basic(conn)
    conn.execute("DELETE FROM index_fund_snapshots")
    evals = evaluate_funds(conn, eval_date=date(2026, 5, 29))
    e = evals[0]
    assert e.shares is None and e.current_value is None
    assert "no_snapshot" in e.risk_tags
    # 目标值仍能算
    assert e.target_value is not None
    conn.close()


def test_evaluate_funds_persist_writes_fund_evaluations_table(monkeypatch):
    from src.index_funds.config import FundWatchItem
    monkeypatch.setattr("src.funds.evaluation.get_watchlist",
                        lambda: [FundWatchItem(
                            fund_code="012963", name="x", fund_type="OPEN",
                            tracking_index="000300", tracking_index_name="x",
                            market="CN", currency="CNY", target_weight=0.33)])
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_basic(conn)
    evaluate_funds(conn, eval_date=date(2026, 5, 29), persist=True)
    rows = load_latest_evaluations(conn)
    assert len(rows) == 1
    assert rows[0]["fund_code"] == "012963"
    assert rows[0]["action"] in {"HOLD", "ADD", "BUY", "REDUCE", "PAUSE"}
    conn.close()
