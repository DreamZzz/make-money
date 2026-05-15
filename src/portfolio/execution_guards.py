"""Shared execution guardrails for paper trading and backtests."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class OpenTradeability:
    tradeable: bool
    reason_code: str | None = None
    reason: str | None = None


def check_open_tradeable(
    open_price: float | None,
    pre_close: float | None = None,
    *,
    market: str = "CN",
    is_st: bool | None = False,
    is_suspended: bool | None = False,
    limit_threshold: float = 0.095,
) -> OpenTradeability:
    """Return whether an opening-auction execution is realistically tradeable."""
    if is_suspended:
        return OpenTradeability(False, "SUSPENDED", "停牌，无法成交")
    if str(market).upper() == "CN" and is_st:
        return OpenTradeability(False, "ST", "ST 标的，跳过成交")

    if open_price is None:
        return OpenTradeability(False, "NO_OPEN_PRICE", "缺少开盘价")
    try:
        open_value = float(open_price)
    except (TypeError, ValueError):
        return OpenTradeability(False, "NO_OPEN_PRICE", "开盘价无效")
    if open_value <= 0 or not isfinite(open_value):
        return OpenTradeability(False, "NO_OPEN_PRICE", "开盘价无效")

    if str(market).upper() == "CN" and pre_close is not None:
        try:
            pre_close_value = float(pre_close)
        except (TypeError, ValueError):
            pre_close_value = 0.0
        if pre_close_value > 0 and isfinite(pre_close_value):
            open_gap = abs(open_value / pre_close_value - 1.0)
            if open_gap >= limit_threshold:
                return OpenTradeability(False, "CN_LIMIT_OPEN", "A 股开盘涨跌停，按开盘价成交不可信")

    return OpenTradeability(True)
