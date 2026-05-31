"""G1: 基金层每日运维入口 — scanner → monitor → recommendations 一气呵成。

供 daily_close 调用,也可手动:
  python -m src.funds.daily              # 当日全量算 + 落库
  python -m src.funds.daily --rebuild    # 删除当日记录后重算
"""
from __future__ import annotations

import argparse
from typing import Any

from loguru import logger


def run_daily(conn, *, rebuild: bool = False) -> dict[str, Any]:

    from src.funds.evaluation import evaluate_funds
    from src.funds.monitoring import monitor_holdings
    from src.funds.recommendations import build_recommendations
    from src.funds.scanner import scan_funds

    result: dict[str, Any] = {"status": "OK"}

    # 1. scanner 全量(已有 nav 的基金)
    screening = scan_funds(conn, persist=True)
    result["scanner_count"] = len(screening)
    from collections import Counter
    result["scanner_dist"] = dict(Counter(r.signal_tag for r in screening))
    logger.info(f"scanner: {len(screening)} 支基金, signal_tag={result['scanner_dist']}")

    # 2. 持仓评估(D 阶段)
    evals = evaluate_funds(conn, persist=True)
    result["evaluation_count"] = len(evals)
    logger.info(f"evaluator: {len(evals)} 支持仓基金")

    # 3. 持仓告警(F3)
    alerts = monitor_holdings(conn, persist=True)
    result["alert_count"] = len(alerts)
    result["alert_dist"] = dict(Counter(a.alert_level for a in alerts))
    logger.info(f"monitor: {len(alerts)} 条告警, level={result['alert_dist']}")

    # 4. 推荐(F4-v2) — 不落库(衍生数据,服务调用时算就够)
    rec = build_recommendations(conn)
    result["recommendations"] = {
        "in_window": len(rec.in_window),
        "oversold": len(rec.oversold_candidates),
        "watch": len(rec.watch_high_value),
        "headline": rec.overall_advice,
    }
    logger.info(f"recommendations: {result['recommendations']}")

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="(预留)重建当日表数据,目前 persist 已是 upsert")
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db
    conn = get_connection()
    try:
        init_db(conn)
        out = run_daily(conn, rebuild=args.rebuild)
        print(out)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
