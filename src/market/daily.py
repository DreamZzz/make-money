"""市场层每日运维入口：依次算 市场状态 → T+1 仓位信号 → 指数搭配。

供 daily_close 调用，也可手动跑：
  python -m src.market.daily               # 算今日市场状态/仓位/指数搭配
  python -m src.market.daily --backfill-valuation  # 先回灌历史估值再算
"""
from __future__ import annotations

import argparse
from typing import Any

from loguru import logger


def run_daily(conn, current_exposure: float | None = None, benchmark: str = "000300") -> dict[str, Any]:
    from src.market.exposure import compute_exposure
    from src.market.index_allocation import build_index_allocation
    from src.market.state import build_market_state

    state = build_market_state(conn, benchmark=benchmark)
    if state is None:
        logger.warning("市场状态无法计算（缺指数行情）")
        return {"status": "NO_DATA"}
    exposure = compute_exposure(conn, current_exposure=current_exposure, benchmark=benchmark)
    allocation = build_index_allocation(conn, benchmark=benchmark)
    logger.info(f"市场状态: {state.summary}")
    if exposure:
        logger.info(f"仓位建议: {exposure.advice}")
    return {
        "status": "OK",
        "stage": state.stage,
        "heat_score": state.heat_score,
        "target_exposure": exposure.target_exposure if exposure else None,
        "funds": [{"fund_code": a.fund_code, "weight": a.weight} for a in allocation],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="市场层每日运维")
    parser.add_argument("--backfill-valuation", action="store_true", help="先回灌历史估值")
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        if args.backfill_valuation:
            from src.data_pipeline.valuation_backfill import backfill_market_valuation

            backfill_market_valuation(conn)
        result = run_daily(conn)
        logger.info(f"市场层每日结果: {result}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
