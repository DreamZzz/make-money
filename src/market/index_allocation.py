"""指数选择与搭配：在 M3 给的权益预算内，按相对强弱把权重倾斜到领先指数。

替代固定目标权重——动态超配动量领先的指数(沪深300/中证500/港股科技)，
但仍分散持有(rank 线性权重)，避免单押。现金 = 1 − 权益预算。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
from loguru import logger

from src.config import load_config


@dataclass(frozen=True)
class FundAllocation:
    fund_code: str
    index_code: str
    index_name: str
    rs_score: float | None
    rs_rank: int
    weight: float


def compute_index_weights(
    rs_scores: dict[str, float],
    equity_budget: float,
    rank_weights: list[float] | None = None,
) -> dict[str, float]:
    """按相对强弱排名把 equity_budget 分配到各指数（纯函数）。

    rank_weights 缺省用线性递减 [n, n-1, ..., 1] 归一化：领先者多配但仍分散。
    """
    if not rs_scores or equity_budget <= 0:
        return {}
    ranked = sorted(rs_scores.items(), key=lambda kv: (kv[1] if kv[1] is not None else -1e9), reverse=True)
    n = len(ranked)
    raw = list(rank_weights)[:n] if rank_weights else [n - i for i in range(n)]
    total = float(sum(raw)) or 1.0
    return {code: round(equity_budget * w / total, 4) for (code, _), w in zip(ranked, raw)}


def _watchlist() -> list[dict[str, Any]]:
    funds = load_config().get("index_funds", {}).get("watchlist", [])
    return [f for f in funds if f.get("enabled", True) and f.get("tracking_index")]


def build_index_allocation(
    conn: duckdb.DuckDBPyConnection,
    benchmark: str = "000300",
    persist: bool = True,
) -> list[FundAllocation]:
    """读取最新 market_state(相对强弱) + market_exposure(权益预算)，输出各基金目标权重。"""
    from src.market.state import load_latest_market_state

    state = load_latest_market_state(conn, benchmark=benchmark)
    if state is None:
        return []
    rs = json.loads(state.get("rs_json") or "{}")
    exp = conn.execute(
        "SELECT target_exposure, trade_date FROM market_exposure WHERE benchmark=? ORDER BY trade_date DESC LIMIT 1",
        [benchmark],
    ).fetchone()
    equity_budget = float(exp[0]) if exp and exp[0] is not None else 0.6
    as_of = exp[1] if exp else _state_date(state)

    funds = _watchlist()
    # 只对有 RS 分的指数分配
    fund_rs = {f["fund_code"]: rs.get(str(f["tracking_index"])) for f in funds}
    rs_for_alloc = {fc: v for fc, v in fund_rs.items() if v is not None}
    weights = compute_index_weights(rs_for_alloc, equity_budget)

    ranked_codes = sorted(rs_for_alloc, key=lambda c: rs_for_alloc[c], reverse=True)
    rank_map = {c: i + 1 for i, c in enumerate(ranked_codes)}

    allocations = []
    for f in funds:
        fc = f["fund_code"]
        allocations.append(FundAllocation(
            fund_code=fc, index_code=str(f["tracking_index"]),
            index_name=str(f.get("tracking_index_name") or f.get("name") or ""),
            rs_score=fund_rs.get(fc), rs_rank=rank_map.get(fc, 0),
            weight=round(weights.get(fc, 0.0), 4),
        ))
    if persist:
        _persist(conn, as_of, equity_budget, allocations)
    return allocations


def _state_date(state: dict) -> date | None:
    from datetime import datetime
    td = state.get("trade_date")
    if isinstance(td, date):
        return td
    try:
        return datetime.fromisoformat(str(td)).date()
    except (ValueError, TypeError):
        return None


def _persist(conn, as_of, equity_budget, allocations: list[FundAllocation]) -> None:
    conn.execute("DELETE FROM index_allocation WHERE trade_date=?", [as_of])
    for a in allocations:
        conn.execute(
            """
            INSERT INTO index_allocation (
                trade_date, fund_code, index_code, index_name, rs_score, rs_rank, weight, equity_budget
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [as_of, a.fund_code, a.index_code, a.index_name, a.rs_score, a.rs_rank, a.weight, equity_budget],
        )


def compare_index_strategies(conn, start: date, end: date, rebalance_days: int = 21) -> dict[str, float]:
    """历史对标：相对强弱轮动 vs 固定等权 vs 单一中证500(月度调仓)。返回累计收益。"""
    import pandas as pd

    codes = ["000300", "000905", "HSTECH"]
    panels = {}
    for c in codes:
        df = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [c, start, end],
        ).fetchdf()
        if not df.empty:
            panels[c] = df.set_index(pd.to_datetime(df["trade_date"]))["close"]
    if "000905" not in panels:
        return {}
    px = pd.DataFrame(panels).dropna()
    if len(px) < rebalance_days + 60:
        return {}
    rets = px.pct_change().fillna(0.0)
    dates = px.index
    rebal_idx = list(range(60, len(dates), rebalance_days))

    def _run(weight_fn) -> float:
        nav = 1.0
        w = {c: 1 / len(codes) for c in codes}
        for i in range(60, len(dates)):
            nav *= 1 + sum(w[c] * rets[c].iloc[i] for c in codes)
            if i in rebal_idx:
                w = weight_fn(i)
        return nav - 1.0

    def _equal(_i):
        return {c: 1 / len(codes) for c in codes}

    def _rotation(i):
        mom = {c: float(px[c].iloc[i] / px[c].iloc[i - 60] - 1) for c in codes}
        ranked = sorted(mom, key=lambda c: mom[c], reverse=True)
        raw = [len(codes) - j for j in range(len(codes))]
        tot = sum(raw)
        return {c: raw[ranked.index(c)] / tot for c in codes}

    csi500_only = float(px["000905"].iloc[-1] / px["000905"].iloc[60] - 1)
    return {
        "fixed_equal": round(_run(_equal), 4),
        "rs_rotation": round(_run(_rotation), 4),
        "csi500_only": round(csi500_only, 4),
    }


def main(argv: list[str] | None = None) -> int:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        allocs = build_index_allocation(conn)
        for a in allocs:
            logger.info(f"{a.index_name}({a.fund_code}) RS#{a.rs_rank} 权重 {a.weight:.1%}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
