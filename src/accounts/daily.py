"""虚拟账户竞赛的命令行入口（供 daily_close 与手动调用）。

  python -m src.accounts.daily seed                      # 创建/确保种子账户
  python -m src.accounts.daily forward                   # 每日前向隔离执行 + 刷新指标
  python -m src.accounts.daily replay --start 2024-01-01 --end 2026-05-22  # 历史回放预热
  python -m src.accounts.daily metrics                   # 仅刷新竞赛榜指标
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd
from loguru import logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="虚拟账户竞赛日常运维")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="创建/确保种子账户")
    sub.add_parser("forward", help="每日前向隔离执行 + 刷新指标")
    sub.add_parser("metrics", help="仅刷新竞赛榜指标")
    p_replay = sub.add_parser("replay", help="历史回放预热")
    p_replay.add_argument("--start", default="2024-01-01")
    p_replay.add_argument("--end", default=None)

    args = parser.parse_args(argv)

    from src.accounts.leaderboard import refresh_all_metrics
    from src.accounts.registry import seed_default_accounts
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        if args.command == "seed":
            records = seed_default_accounts(conn)
            logger.info(f"种子账户就绪: {[r.account_id for r in records]}")
        elif args.command == "forward":
            from src.accounts.engine import run_all_accounts_forward

            seed_default_accounts(conn)
            results = run_all_accounts_forward(conn)
            for r in results:
                logger.info(f"[{r.account_id}] forward filled={r.filled} skipped={r.skipped}")
            refresh_all_metrics(conn)
        elif args.command == "metrics":
            metrics = refresh_all_metrics(conn)
            logger.info(f"已刷新 {len(metrics)} 个账户指标")
        elif args.command == "replay":
            from src.accounts.replay import replay_all_accounts

            seed_default_accounts(conn)
            start = pd.to_datetime(args.start).date()
            end = pd.to_datetime(args.end).date() if args.end else date.today()
            results = replay_all_accounts(conn, start, end)
            for r in results:
                logger.info(f"[{r.account_id}] replay orders={r.orders} nav_days={r.nav_days}")
            refresh_all_metrics(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
