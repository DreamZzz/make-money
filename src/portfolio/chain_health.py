"""执行链每日健康检查（P1-E）：把历史暴露过的执行 bug 固化成可自动判定的不变量，
客观追踪"连续 N 个干净交易日"的稳定窗口，取代手工监控。

检查的不变量（来自 daily_close_monitoring 的 issue register）：
- 同日重复成交：同 模型/标的/方向 在同一执行日不应有多于一笔成交（600808 双买 bug）。
- 终态信号未对齐 executed：status 非 ACTIVE 的信号应 executed=TRUE，否则 outcome 跟踪会漏。
- order_ts 异常时间：成交时间不应落在 00:00（回填/回溯执行的痕迹）。
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import duckdb
from loguru import logger


@dataclass
class DayHealth:
    trade_date: date
    orders: int
    clean: bool
    issues: list[str] = field(default_factory=list)
    duplicate_orders: int = 0
    terminal_status_unexecuted: int = 0
    midnight_orders: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["trade_date"] = self.trade_date.isoformat()
        return out


def check_trading_day(conn: duckdb.DuckDBPyConnection, trade_date: date) -> DayHealth:
    """检查某交易日的执行链是否干净。"""
    orders = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE CAST(order_ts AS DATE) = ?", [trade_date]
    ).fetchone()[0]

    # 1. 同日 模型/标的/方向 重复成交
    dup = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT COALESCE(s.model_name, '?') m, po.symbol, po.side, COUNT(*) c
            FROM paper_orders po
            LEFT JOIN signals s ON po.signal_id = s.signal_id
            WHERE CAST(po.order_ts AS DATE) = ?
            GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
        )
        """,
        [trade_date],
    ).fetchone()[0]

    # 2. 终态信号未对齐 executed（execution_date 当天）
    # 终态=已成定局：FILLED/NO_ACTION/EXPIRED/SUPERSEDED 应 executed=TRUE。
    # DEFERRED_BUDGET 是"预算不足暂缓"的 pending 状态、会择日重试，executed=FALSE 属正常，不算违例。
    term = conn.execute(
        """
        SELECT COUNT(*) FROM signals
        WHERE execution_date = ?
          AND status IN ('FILLED', 'NO_ACTION', 'EXPIRED', 'SUPERSEDED')
          AND COALESCE(executed, FALSE) = FALSE
        """,
        [trade_date],
    ).fetchone()[0]

    # 3. order_ts 落在 00:00（回填/回溯痕迹）
    midnight = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE CAST(order_ts AS DATE) = ? AND CAST(order_ts AS TIME) = TIME '00:00:00'",
        [trade_date],
    ).fetchone()[0]

    issues = []
    if dup:
        issues.append(f"同日重复成交 {dup} 组（同模型/标的/方向多笔）")
    if term:
        issues.append(f"终态信号未对齐 executed {term} 条（outcome 跟踪会漏）")
    if midnight:
        issues.append(f"order_ts 落在 00:00 的成交 {midnight} 笔（回溯/回填痕迹）")

    return DayHealth(
        trade_date=trade_date, orders=int(orders), clean=not issues, issues=issues,
        duplicate_orders=int(dup), terminal_status_unexecuted=int(term), midnight_orders=int(midnight),
    )


def _recent_trading_days(conn: duckdb.DuckDBPyConnection, end_date: date | None, n: int) -> list[date]:
    params: list[Any] = []
    where = ""
    if end_date is not None:
        where = "WHERE trade_date <= ?"
        params.append(end_date)
    rows = conn.execute(
        f"""
        SELECT DISTINCT trade_date FROM daily_price
        {where}
        ORDER BY trade_date DESC LIMIT ?
        """,
        [*params, n],
    ).fetchall()
    return sorted(r[0] for r in rows)


def check_recent_days(
    conn: duckdb.DuckDBPyConnection,
    end_date: date | None = None,
    n: int = 5,
) -> dict[str, Any]:
    """检查最近 n 个交易日，返回每日判定 + 连续干净天数（从最近往回数）。"""
    days = _recent_trading_days(conn, end_date, n)
    results = [check_trading_day(conn, d) for d in days]
    streak = 0
    for r in reversed(results):
        if r.clean:
            streak += 1
        else:
            break
    return {
        "days": [r.to_dict() for r in results],
        "clean_streak": streak,
        "target": n,
        "gate_met": streak >= n,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行链每日健康检查 / 5 日干净窗口")
    parser.add_argument("--date", default=None, help="结束日期 YYYY-MM-DD，默认最新交易日")
    parser.add_argument("--days", type=int, default=5)
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection

    end = date.fromisoformat(args.date) if args.date else None
    conn = get_connection(read_only=True)
    try:
        report = check_recent_days(conn, end_date=end, n=args.days)
    finally:
        conn.close()
    for d in report["days"]:
        mark = "✅" if d["clean"] else "⚠️"
        detail = "干净" if d["clean"] else "; ".join(d["issues"])
        logger.info(f"{mark} {d['trade_date']} 成交{d['orders']} {detail}")
    logger.info(f"连续干净交易日: {report['clean_streak']}/{report['target']} → 门槛{'达成' if report['gate_met'] else '未达成'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
