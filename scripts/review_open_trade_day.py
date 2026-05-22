#!/usr/bin/env python3
"""Summarize one open-session paper-trading day."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.data_pipeline.loader import get_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one open paper-trading day.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Trade date, YYYY-MM-DD")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date)

    conn = get_connection(read_only=True)
    try:
        orders = conn.execute("""
            SELECT po.symbol,
                   COALESCE(si.name, po.symbol) AS name,
                   po.side,
                   po.order_qty,
                   po.order_price,
                   po.order_value,
                   po.fee,
                   s.model_name,
                   s.status_reason
            FROM paper_orders po
            LEFT JOIN signals s ON po.signal_id = s.signal_id
            LEFT JOIN stock_info si ON po.symbol = si.symbol
            WHERE CAST(po.order_ts AS DATE) = ?
            ORDER BY po.order_ts, po.symbol
        """, [trade_date]).fetchdf()
        blocked = conn.execute("""
            SELECT s.symbol,
                   COALESCE(si.name, s.symbol) AS name,
                   s.side,
                   s.model_name,
                   s.status,
                   s.status_reason
            FROM signals s
            LEFT JOIN stock_info si ON s.symbol = si.symbol
            WHERE s.execution_date = ?
              AND s.status IN ('NO_ACTION', 'DEFERRED_BUDGET')
            ORDER BY s.status, s.model_name, s.symbol
        """, [trade_date]).fetchdf()
    finally:
        conn.close()

    print(f"# 开盘纸交易复盘 {trade_date}")
    print(f"成交订单: {len(orders)}")
    print(orders.to_string(index=False) if not orders.empty else "无成交订单")
    print()
    print(f"拦截/暂缓信号: {len(blocked)}")
    print(blocked.to_string(index=False) if not blocked.empty else "无拦截/暂缓信号")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
