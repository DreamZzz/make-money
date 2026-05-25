"""T+1 增减仓信号：把市场状态映射成目标权益仓位 + 加仓/减仓/维持建议。

设计原则（吸取 regime_gated 在牛市降仓致败的教训）：**长期偏多**。
- 仓位基准由"阶段"决定，上升趋势始终高仓位；
- 估值/宽度/热度只做小幅微调，总调整限幅 [-0.20, +0.12]；
- 只有阶段跌到"下跌/危机"才大幅降仓——即只在真崩盘降仓，不被牛市回调吓跑。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np
from loguru import logger

# 各阶段基准权益仓位
STAGE_BASE = {
    "强势上升": 0.95,
    "温和上升": 0.85,
    "震荡整理": 0.65,
    "弱势整理": 0.50,
    "下跌": 0.35,
    "危机": 0.25,
    "数据不足": 0.50,
}
MIN_ADJ, MAX_ADJ = -0.20, 0.12  # 微调总限幅，防止把强势趋势压垮
DEADBAND = 0.05  # 目标与当前偏离超过此值才建议动作


@dataclass
class ExposureSignal:
    trade_date: date | None
    benchmark: str
    stage: str
    base_exposure: float
    valuation_adj: float
    breadth_adj: float
    heat_adj: float
    target_exposure: float
    current_exposure: float | None
    action: str
    advice: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _valuation_adj(pe_pct: float | None) -> float:
    if pe_pct is None:
        return 0.0
    if pe_pct >= 0.90:
        return -0.12
    if pe_pct >= 0.80:
        return -0.08
    if pe_pct <= 0.20:
        return 0.10
    if pe_pct <= 0.35:
        return 0.05
    return 0.0


def _breadth_adj(above_ma200: float | None) -> float:
    if above_ma200 is None:
        return 0.0
    if above_ma200 < 0.30:
        return -0.05  # 普涨退化为窄幅行情，谨慎
    if above_ma200 > 0.70:
        return 0.05   # 宽度健康
    return 0.0


def _heat_adj(heat: float | None) -> float:
    if heat is None:
        return 0.0
    if heat >= 80:
        return -0.05  # 过热有泡沫
    if heat <= 20:
        return 0.05   # 冰点，逆向小幅加
    return 0.0


def derive_exposure(state: dict[str, Any], current_exposure: float | None = None) -> ExposureSignal:
    """从市场状态推导目标权益仓位与 T+1 动作（纯函数）。"""
    stage = str(state.get("stage") or "数据不足")
    base = STAGE_BASE.get(stage, 0.50)
    v_adj = _valuation_adj(state.get("pe_pct_10y"))
    b_adj = _breadth_adj(state.get("breadth_above_ma200"))
    h_adj = _heat_adj(state.get("heat_score"))
    total_adj = float(np.clip(v_adj + b_adj + h_adj, MIN_ADJ, MAX_ADJ))
    target = float(np.clip(base + total_adj, 0.20, 1.0))

    action, advice = _action_and_advice(stage, target, current_exposure, v_adj, b_adj, h_adj)
    return ExposureSignal(
        trade_date=state.get("trade_date") if isinstance(state.get("trade_date"), date) else None,
        benchmark=str(state.get("benchmark") or "000300"),
        stage=stage, base_exposure=round(base, 3),
        valuation_adj=round(v_adj, 3), breadth_adj=round(b_adj, 3), heat_adj=round(h_adj, 3),
        target_exposure=round(target, 3), current_exposure=current_exposure,
        action=action, advice=advice,
    )


def _action_and_advice(stage, target, current, v_adj, b_adj, h_adj) -> tuple[str, str]:
    notes = []
    if v_adj < 0:
        notes.append("估值偏贵下调")
    elif v_adj > 0:
        notes.append("估值偏低上调")
    if b_adj < 0:
        notes.append("宽度不足谨慎")
    if h_adj < 0:
        notes.append("情绪过热降温")
    elif h_adj > 0:
        notes.append("情绪冰点逆向")
    note_txt = "，".join(notes) if notes else "按阶段基准"

    if current is None:
        return "HOLD", f"{stage}，目标权益仓位约 {target:.0%}（{note_txt}）；当前仓位未知，按目标执行"
    delta = target - current
    if delta > DEADBAND:
        return "ADD", f"{stage}，建议加仓：当前 {current:.0%} → 目标 {target:.0%}（{note_txt}）"
    if delta < -DEADBAND:
        return "REDUCE", f"{stage}，建议减仓：当前 {current:.0%} → 目标 {target:.0%}（{note_txt}）"
    return "HOLD", f"{stage}，维持当前仓位 {current:.0%}（已接近目标 {target:.0%}，{note_txt}）"


def compute_exposure(
    conn: duckdb.DuckDBPyConnection,
    current_exposure: float | None = None,
    benchmark: str = "000300",
    persist: bool = True,
) -> ExposureSignal | None:
    """读取最新 market_state，推导仓位信号并落 market_exposure 表。"""
    from src.market.state import load_latest_market_state

    state = load_latest_market_state(conn, benchmark=benchmark)
    if state is None:
        return None
    if isinstance(state.get("trade_date"), str):
        from datetime import datetime
        try:
            state["trade_date"] = datetime.fromisoformat(state["trade_date"]).date()
        except ValueError:
            state["trade_date"] = None
    signal = derive_exposure(state, current_exposure=current_exposure)
    if persist:
        _persist(conn, signal)
    return signal


def _persist(conn, sig: ExposureSignal) -> None:
    conn.execute("DELETE FROM market_exposure WHERE trade_date=? AND benchmark=?", [sig.trade_date, sig.benchmark])
    conn.execute(
        """
        INSERT INTO market_exposure (
            trade_date, benchmark, stage, base_exposure, valuation_adj, breadth_adj, heat_adj,
            target_exposure, current_exposure, action, advice
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [sig.trade_date, sig.benchmark, sig.stage, sig.base_exposure, sig.valuation_adj,
         sig.breadth_adj, sig.heat_adj, sig.target_exposure, sig.current_exposure, sig.action, sig.advice],
    )


def main(argv: list[str] | None = None) -> int:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        sig = compute_exposure(conn)
        if sig:
            logger.info(f"仓位信号 {sig.trade_date}: {sig.action} → {sig.advice}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
