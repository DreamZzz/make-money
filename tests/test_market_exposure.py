from __future__ import annotations

import duckdb

from src.data_pipeline.loader import init_db
from src.market.exposure import compute_exposure, derive_exposure


def _state(stage, pe=0.5, breadth=0.5, heat=50.0):
    return {"stage": stage, "benchmark": "000300", "pe_pct_10y": pe,
            "breadth_above_ma200": breadth, "heat_score": heat}


def test_stage_drives_base_exposure():
    assert derive_exposure(_state("强势上升")).target_exposure >= 0.9
    assert derive_exposure(_state("危机")).target_exposure <= 0.30


def test_uptrend_stays_high_despite_expensive_narrow_overheated():
    # 关键反悔测试：强势上升 + 估值95分位 + 宽度25% + 过热85
    # 微调被限幅，仓位仍应保持高位(>=0.70)，绝不被压到低仓——杜绝 regime_gated 的错误
    sig = derive_exposure(_state("强势上升", pe=0.95, breadth=0.25, heat=85))
    assert sig.target_exposure >= 0.70
    assert sig.valuation_adj < 0 and sig.heat_adj < 0  # 确实做了下调，只是限幅


def test_only_crisis_cuts_hard():
    # 只有阶段恶化到危机/下跌才低仓，微调不能单独造成低仓
    assert derive_exposure(_state("危机", pe=0.5)).target_exposure <= 0.30
    assert derive_exposure(_state("下跌", pe=0.5)).target_exposure <= 0.50
    assert derive_exposure(_state("温和上升", pe=0.95, breadth=0.2, heat=90)).target_exposure >= 0.60


def test_cheap_valuation_adds():
    expensive = derive_exposure(_state("震荡整理", pe=0.95)).target_exposure
    cheap = derive_exposure(_state("震荡整理", pe=0.10)).target_exposure
    assert cheap > expensive


def test_action_deadband():
    # 强势上升目标~0.95
    add = derive_exposure(_state("强势上升"), current_exposure=0.50)
    assert add.action == "ADD"
    hold = derive_exposure(_state("强势上升"), current_exposure=0.93)
    assert hold.action == "HOLD"
    reduce = derive_exposure(_state("危机"), current_exposure=0.80)
    assert reduce.action == "REDUCE"


def test_compute_exposure_persists():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO market_state (trade_date, benchmark, stage, stage_score, heat_score,
            breadth_above_ma200, pe_pct_10y) VALUES ('2026-05-22','000300','强势上升',60,58,0.44,0.84)
        """
    )
    sig = compute_exposure(conn, current_exposure=0.5)
    assert sig is not None
    assert sig.stage == "强势上升"
    assert sig.action == "ADD"  # 当前0.5远低于目标
    row = conn.execute("SELECT stage, action, target_exposure FROM market_exposure").fetchone()
    assert row[0] == "强势上升"
    conn.close()
