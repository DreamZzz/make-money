"""Shared execution sizing preview for paper trading and Dashboard V2."""
from __future__ import annotations

from datetime import date
from typing import Any

import duckdb


def estimate_buy_execution(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    trade_date: date,
    current_total: float,
    max_position_pct: float,
    available_cash: float,
    satellite_budget: float | None,
    market: str = "CN",
    price: float | None = None,
    commission_rate: float = 0.00025,
    min_fee: float = 5.0,
) -> dict[str, Any]:
    """Estimate target-position BUY sizing and explain why it can/cannot trade."""
    name = _load_name(conn, symbol)
    execution_price = float(price or _load_open_or_close(conn, symbol, trade_date) or 0.0)
    lot_size = 100 if str(market).upper() == "CN" else 1
    one_lot_cash = execution_price * lot_size if execution_price > 0 else 0.0
    target_position_cash = max(float(current_total) * float(max_position_pct), 0.0)
    rounded_qty = int(target_position_cash / execution_price / lot_size) * lot_size if execution_price > 0 else 0
    execution_value = float(rounded_qty * execution_price)
    fee = max(execution_value * float(commission_rate), float(min_fee)) if execution_value > 0 else 0.0
    required_cash = execution_value + fee

    status = "EXECUTABLE"
    block_reason = "预算和整手约束均通过"
    budget_gap = 0.0
    if execution_price <= 0:
        status = "NO_PRICE"
        block_reason = "缺少开盘价/收盘价，无法计算执行金额"
    elif rounded_qty <= 0:
        status = "BLOCKED_LOT"
        block_reason = (
            f"{max_position_pct:.0%}目标仓位约 {target_position_cash:,.0f} 元，"
            f"不足一手所需 {one_lot_cash:,.0f} 元"
        )
    elif satellite_budget is not None and required_cash > float(satellite_budget) + 1e-9:
        status = "BLOCKED_BUDGET"
        budget_gap = required_cash - max(float(satellite_budget), 0.0)
        block_reason = f"Satellite预算不足：需要 {required_cash:,.0f} 元，剩余 {float(satellite_budget):,.0f} 元"
    elif required_cash > float(available_cash) + 1e-9:
        status = "BLOCKED_CASH"
        budget_gap = required_cash - max(float(available_cash), 0.0)
        block_reason = f"现金不足：需要 {required_cash:,.0f} 元，可用 {float(available_cash):,.0f} 元"

    return {
        "symbol": symbol,
        "name": name,
        "display_name": f"{name}（{symbol}）" if name and name != symbol else symbol,
        "trade_date": trade_date.isoformat(),
        "market": market,
        "execution_price": round(execution_price, 4),
        "one_lot_cash": round(one_lot_cash, 2),
        "target_position_cash": round(target_position_cash, 2),
        "rounded_qty": int(rounded_qty),
        "execution_value": round(execution_value, 2),
        "fee": round(fee, 2),
        "required_cash": round(required_cash, 2),
        "available_cash": round(float(available_cash), 2),
        "satellite_budget": None if satellite_budget is None else round(float(satellite_budget), 2),
        "budget_gap": round(max(budget_gap, 0.0), 2),
        "status": status,
        "block_reason": block_reason,
    }


def _load_name(conn: duckdb.DuckDBPyConnection, symbol: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(name, symbol) FROM stock_info WHERE symbol = ? LIMIT 1",
        [symbol],
    ).fetchone()
    return str(row[0]) if row and row[0] else symbol


def _load_open_or_close(conn: duckdb.DuckDBPyConnection, symbol: str, trade_date: date) -> float | None:
    row = conn.execute(
        """
        SELECT COALESCE(open, close)
        FROM daily_price
        WHERE symbol = ? AND trade_date = ?
        LIMIT 1
        """,
        [symbol, trade_date],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None
