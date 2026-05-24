from __future__ import annotations

from datetime import date

import duckdb

from src.accounts.config import AccountConfig
from src.accounts.leaderboard import AccountMetrics, compute_account_metrics
from src.accounts.promotion import (
    PromotionThresholds,
    evaluate_account_promotion,
    evaluate_tournament,
    promote_account,
)
from src.accounts.registry import get_account, upsert_account
from src.data_pipeline.loader import init_db


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_db(conn)
    return conn


def _seed_nav(conn, account_id, navs, start_day=2):
    for i, nv in enumerate(navs):
        d = date(2024, 1, start_day + i)
        dr = 0.0 if i == 0 else navs[i] / navs[i - 1] - 1
        conn.execute(
            "INSERT INTO account_nav (account_id, trade_date, nav, daily_return, cash, position_value, total_value, drawdown) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, 0)",
            [account_id, d, nv, dr, nv * 1e6, nv * 1e6],
        )


def _flat_benchmark(conn, days=5):
    for i in range(days):
        conn.execute(
            "INSERT INTO index_daily (index_code, trade_date, close) VALUES ('000300', ?, 100.0)",
            [date(2024, 1, 2 + i)],
        )


def test_compute_metrics_with_round_trip_hit_rate():
    conn = _conn()
    upsert_account(conn, "m1", "M1", AccountConfig(benchmark_index="000300"))
    _seed_nav(conn, "m1", [1.0, 1.05, 1.10])
    _flat_benchmark(conn)
    # 两笔已平仓：000001 盈利、000002 亏损 → 命中率 0.5
    conn.execute(
        """
        INSERT INTO account_orders (account_id, order_id, symbol, side, order_qty, order_price, order_value, fee, order_ts, source, status) VALUES
        ('m1','o1','000001','BUY',100,10,1000,5, TIMESTAMP '2024-01-02 09:30:00','replay','FILLED'),
        ('m1','o2','000001','SELL',100,12,1200,5, TIMESTAMP '2024-01-03 09:30:00','replay','FILLED'),
        ('m1','o3','000002','BUY',100,10,1000,5, TIMESTAMP '2024-01-02 09:31:00','replay','FILLED'),
        ('m1','o4','000002','SELL',100,9,900,5, TIMESTAMP '2024-01-03 09:31:00','replay','FILLED')
        """
    )
    m = compute_account_metrics(conn, get_account(conn, "m1"))
    assert m is not None
    assert m.sample_days == 3
    assert abs(m.cumulative_return - 0.10) < 1e-6
    assert m.ready_outcomes == 2
    assert abs(m.hit_rate - 0.5) < 1e-9
    assert m.benchmark_return is not None  # 基准接上
    conn.close()


def _metrics(account_id, excess, **kw):
    base = dict(
        account_id=account_id, as_of_date=date(2026, 5, 22), window_label="replay",
        sample_days=600, annual_return=0.2, cumulative_return=0.4, annual_volatility=0.2,
        sharpe_ratio=1.0, max_drawdown=-0.2, turnover=2.0, hit_rate=0.5,
        benchmark_return=0.05, excess_return=excess, info_ratio=0.8, ready_outcomes=150,
    )
    base.update(kw)
    return AccountMetrics(**base)


def test_promotion_gate_pass_and_fail():
    ok = evaluate_account_promotion(_metrics("a", 0.10))
    assert ok.eligible is True and ok.reasons == []

    bad = evaluate_account_promotion(_metrics("b", -0.05, sharpe_ratio=0.1, ready_outcomes=10))
    assert bad.eligible is False
    assert any("超额" in r for r in bad.reasons)
    assert any("已结算交易" in r for r in bad.reasons)
    assert any("Sharpe" in r for r in bad.reasons)


def test_tournament_selection_bias_blocks_close_race():
    conn = _conn()
    _flat_benchmark(conn, days=4)
    # 两账户都达标，但超额接近 → 选择偏差守卫不应晋级冠军
    upsert_account(conn, "a", "A", AccountConfig(benchmark_index="000300"))
    upsert_account(conn, "b", "B", AccountConfig(benchmark_index="000300"))
    # 两账户战绩相同 → 超额差 0 < buffer，守卫应拦截晋级
    _seed_nav(conn, "a", [1.0, 1.20, 1.21])
    _seed_nav(conn, "b", [1.0, 1.20, 1.21])
    lenient = PromotionThresholds(min_sample_days=2, min_closed_trades=0, min_sharpe=-99,
                                  min_info_ratio=-99, max_drawdown_floor=-0.99,
                                  selection_bias_excess_buffer=0.50)
    res = evaluate_tournament(conn, thresholds=lenient)
    assert res["eligible_count"] == 2
    assert res["recommended_winner"] is None  # 差距 < buffer
    assert "选择偏差" in res["selection_note"]
    conn.close()


def test_tournament_promotes_clear_leader_and_marks_candidate():
    conn = _conn()
    _flat_benchmark(conn, days=4)
    upsert_account(conn, "a", "A", AccountConfig(benchmark_index="000300"))
    upsert_account(conn, "b", "B", AccountConfig(benchmark_index="000300"))
    _seed_nav(conn, "a", [1.0, 1.50, 1.60])  # 明显领先
    _seed_nav(conn, "b", [1.0, 1.02, 1.01])
    lenient = PromotionThresholds(min_sample_days=2, min_closed_trades=0, min_sharpe=-99,
                                  min_info_ratio=-99, max_drawdown_floor=-0.99,
                                  selection_bias_excess_buffer=0.05)
    res = evaluate_tournament(conn, thresholds=lenient)
    assert res["recommended_winner"] == "a"

    promote_account(conn, "a")
    a = get_account(conn, "a")
    assert a.is_real_candidate is True
    assert a.status == "PROMOTED"
    conn.close()
