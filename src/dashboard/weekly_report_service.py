"""Pure helpers for the weekly operation report."""
from __future__ import annotations

from typing import Any

import pandas as pd


def build_weekly_operation_summary(
    plan: pd.DataFrame,
    *,
    minutes_per_operation: int = 3,
) -> dict[str, Any]:
    """Aggregate executable rebalance plan rows into a retail operation summary."""
    if plan.empty:
        return {
            "operation_count": 0,
            "buy_count": 0,
            "reduce_count": 0,
            "required_cash": 0.0,
            "released_cash": 0.0,
            "candidate_count": 0,
            "one_lot_funding_gap": 0.0,
            "estimated_minutes": 0,
        }

    executable = plan[plan.get("executable", False).fillna(False).astype(bool)]
    buys = executable[executable.get("action", "") == "买入"]
    reduces = executable[executable.get("action", "").isin(["减仓", "清仓"])]
    candidates = plan[plan.get("action", "") == "候选"]

    required_cash = _sum(buys.get("order_value")) + _sum(buys.get("estimated_fee"))
    released_cash = _sum(-reduces.get("order_value")) - _sum(reduces.get("estimated_fee"))
    operation_count = int(len(buys) + len(reduces))
    funding_gap = _sum(candidates.get("funding_gap"))
    minutes = max(int(minutes_per_operation), 1)

    return {
        "operation_count": operation_count,
        "buy_count": int(len(buys)),
        "reduce_count": int(len(reduces)),
        "required_cash": float(required_cash),
        "released_cash": float(max(released_cash, 0.0)),
        "candidate_count": int(len(candidates)),
        "one_lot_funding_gap": float(max(funding_gap, 0.0)),
        "estimated_minutes": int(operation_count * minutes),
    }


def _sum(series: pd.Series | None) -> float:
    if series is None:
        return 0.0
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())
