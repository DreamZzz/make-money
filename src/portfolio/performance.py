"""Periodic account performance reviews."""
import uuid
from datetime import date, timedelta

import pandas as pd

from src.portfolio.cashbook import DEFAULT_ACCOUNT_ID, rebuild_account_daily


def _period_start(end_date: date, period_type: str) -> date:
    if period_type == "MONTHLY":
        return end_date - timedelta(days=30)
    if period_type == "WEEKLY":
        return end_date - timedelta(days=7)
    raise ValueError("period_type must be WEEKLY or MONTHLY")


def generate_review(
    period_type: str = "WEEKLY",
    benchmark_code: str = "000300",
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict:
    """Generate and persist a cash-flow-adjusted performance review."""
    from src.data_pipeline.loader import get_connection, init_db

    period_type = period_type.upper()
    rebuild_account_daily(account_id=account_id)

    conn = get_connection()
    try:
        init_db(conn)
        latest = conn.execute("""
            SELECT MAX(trade_date) FROM account_daily WHERE account_id = ?
        """, [account_id]).fetchone()[0]
        if latest is None:
            return {}
        start = _period_start(latest, period_type)
        account = conn.execute("""
            SELECT trade_date, nav, total_value, daily_external_flow, drawdown
            FROM account_daily
            WHERE account_id = ? AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """, [account_id, start, latest]).fetchdf()
        if len(account) < 2:
            return {}

        account["trade_date"] = pd.to_datetime(account["trade_date"])
        nav_return = float(account["nav"].iloc[-1] / account["nav"].iloc[0] - 1)
        total_value_change = float(account["total_value"].iloc[-1] - account["total_value"].iloc[0])
        net_flow = float(account["daily_external_flow"].sum())
        max_drawdown = float(account["drawdown"].min())

        bench = conn.execute("""
            SELECT trade_date, close FROM index_daily
            WHERE index_code = ? AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """, [benchmark_code, start, latest]).fetchdf()
        benchmark_return = None
        if len(bench) >= 2:
            benchmark_return = float(bench["close"].iloc[-1] / bench["close"].iloc[0] - 1)
        excess_return = nav_return - benchmark_return if benchmark_return is not None else None

        review = {
            "review_id": f"REV-{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id,
            "period_type": period_type,
            "start_date": account["trade_date"].iloc[0].date(),
            "end_date": account["trade_date"].iloc[-1].date(),
            "nav_return": nav_return,
            "total_value_change": total_value_change,
            "net_flow": net_flow,
            "benchmark_code": benchmark_code,
            "benchmark_return": benchmark_return,
            "excess_return": excess_return,
            "max_drawdown": max_drawdown,
        }
        df = pd.DataFrame([review])
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_review AS SELECT * FROM df")
        conn.execute("""
            INSERT INTO performance_reviews (
                review_id, account_id, period_type, start_date, end_date,
                nav_return, total_value_change, net_flow, benchmark_code,
                benchmark_return, excess_return, max_drawdown
            )
            SELECT review_id, account_id, period_type, start_date, end_date,
                   nav_return, total_value_change, net_flow, benchmark_code,
                   benchmark_return, excess_return, max_drawdown
            FROM _tmp_review
        """)
        return review
    finally:
        conn.close()


if __name__ == "__main__":
    for period in ("WEEKLY", "MONTHLY"):
        result = generate_review(period)
        if result:
            print(f"{period}: {result}")
