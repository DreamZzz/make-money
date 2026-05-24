from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

from src.data_pipeline.loader import init_db
from src.market.state import (
    build_market_state,
    compute_breadth,
    compute_heat_score,
    compute_relative_strength,
    compute_stage,
)


def test_compute_stage_uptrend_and_downtrend():
    up = pd.Series(np.linspace(100, 200, 260))
    stage, score = compute_stage(up)
    assert score > 50
    assert stage in {"强势上升", "温和上升"}

    down = pd.Series(np.linspace(200, 100, 260))
    stage2, score2 = compute_stage(down)
    assert score2 < 0
    assert stage2 in {"弱势整理", "下跌"}


def test_compute_stage_crisis():
    # 先涨到高位再腰斩 + 高波动 → 危机
    series = list(np.linspace(100, 200, 200)) + list(200 * (1 + 0.05 * np.sin(np.arange(60)) - np.linspace(0, 0.45, 60)))
    stage, _ = compute_stage(pd.Series(series))
    assert stage == "危机"


def test_compute_breadth():
    dates = pd.date_range("2024-01-01", periods=210, freq="D")
    # A 持续上涨，B 持续下跌
    panel = pd.DataFrame({
        "A": np.linspace(10, 20, 210),
        "B": np.linspace(20, 10, 210),
    }, index=dates)
    b = compute_breadth(panel)
    assert 0 <= b["above_ma50"] <= 1
    assert b["advance_ratio"] == 0.5  # 一涨一跌
    assert b["above_ma200"] == 0.5


def test_compute_relative_strength_picks_leader():
    s_strong = pd.Series(np.linspace(100, 150, 130))
    s_weak = pd.Series(np.linspace(100, 105, 130))
    leader, rs = compute_relative_strength({"000300": s_weak, "000905": s_strong})
    assert leader == "000905"
    assert rs["000905"] > rs["000300"]


def test_compute_heat_score_bounded():
    assert 0 <= compute_heat_score(3.0, 0.10, 0.6, 0.95) <= 100
    assert 0 <= compute_heat_score(0.3, 0.0, 0.05, 0.1) <= 100
    assert compute_heat_score(None, None, None, None) == 50.0


def test_build_market_state_integration():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    base = date(2024, 1, 1)
    # 指数上涨序列 + 量
    for code in ["000300", "000905", "HSTECH"]:
        for i in range(260):
            conn.execute(
                "INSERT INTO index_daily (index_code, trade_date, close, volume) VALUES (?, ?, ?, ?)",
                [code, base + timedelta(days=i), 3000 + i * 5, 1e8 + i],
            )
    # 两只股票 260 天
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001','CN','A'),('000002','CN','B')")
    for i in range(260):
        d = base + timedelta(days=i)
        conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('000001', ?, ?)", [d, 10 + i * 0.05])
        conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('000002', ?, ?)", [d, 20 - i * 0.02])
    conn.execute(
        "INSERT INTO market_valuation (trade_date, pe_ttm_pct_10y, pb_pct_10y) VALUES (?, 0.84, 0.81)",
        [base + timedelta(days=259)],
    )

    state = build_market_state(conn)
    assert state is not None
    assert state.stage in {"强势上升", "温和上升", "震荡整理"}
    assert state.pe_pct_10y == 0.84
    assert state.rs_leader in {"000300", "000905", "HSTECH"}
    assert isinstance(state.summary, str) and len(state.summary) > 0
    # 已落表
    assert conn.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 1
    conn.close()
