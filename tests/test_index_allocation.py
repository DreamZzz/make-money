from __future__ import annotations

import json

import duckdb

from src.data_pipeline.loader import init_db
from src.market.index_allocation import build_index_allocation, compute_index_weights


def test_compute_index_weights_tilts_to_leader():
    rs = {"000905": 0.30, "000300": 0.10, "HSTECH": 0.20}
    w = compute_index_weights(rs, equity_budget=0.9)
    # 最强(000905)权重最大，最弱(000300)最小，三者和=权益预算
    assert w["000905"] > w["HSTECH"] > w["000300"]
    assert abs(sum(w.values()) - 0.9) < 1e-6


def test_compute_index_weights_cash_from_low_budget():
    rs = {"000905": 0.30, "000300": 0.10, "HSTECH": 0.20}
    w = compute_index_weights(rs, equity_budget=0.6)
    assert abs(sum(w.values()) - 0.6) < 1e-6  # 现金 40%


def test_compute_index_weights_empty():
    assert compute_index_weights({}, 0.9) == {}
    assert compute_index_weights({"a": 1.0}, 0.0) == {}


def test_build_index_allocation_integration(monkeypatch):
    # E3: M4 池现在过滤 category/intent;测试要显式 patch watchlist 才有 active equity 标的
    def _fake_load_config():
        return {"index_funds": {"watchlist": [
            {"fund_code": "012963", "tracking_index": "000300", "category": "equity_index",
             "intent": "active", "enabled": True},
            {"fund_code": "004192", "tracking_index": "000905", "category": "equity_index",
             "intent": "active", "enabled": True},
            {"fund_code": "013308", "tracking_index": "HSTECH", "category": "qdii",
             "intent": "active", "enabled": True},
        ]}}
    monkeypatch.setattr("src.market.index_allocation.load_config", _fake_load_config)
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO market_state (trade_date, benchmark, stage, rs_json)
        VALUES ('2026-05-22','000300','强势上升', ?)
        """,
        [json.dumps({"000300": 0.05, "000905": 0.18, "HSTECH": 0.12})],
    )
    conn.execute(
        "INSERT INTO market_exposure (trade_date, benchmark, stage, target_exposure) "
        "VALUES ('2026-05-22','000300','强势上升',0.87)"
    )
    allocs = build_index_allocation(conn)
    assert len(allocs) >= 1
    by_index = {a.index_code: a for a in allocs}
    # 中证500 动量最强 → rank 1，权重最高
    assert by_index["000905"].rs_rank == 1
    assert by_index["000905"].weight == max(a.weight for a in allocs)
    # 权重和 ≈ 权益预算 0.87
    assert abs(sum(a.weight for a in allocs) - 0.87) < 0.01
    # 已落表
    assert conn.execute("SELECT COUNT(*) FROM index_allocation").fetchone()[0] == len(allocs)
    conn.close()
