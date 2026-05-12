#!/usr/bin/env python3
"""Open-session paper trading wrapper.

Runs after market open:
1. stop Dashboard to release DuckDB locks
2. update only current holdings and pending-signal symbols
3. execute pending stock paper trades
4. recalculate strategy NAV
5. restart Dashboard
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("PYTHON") or sys.executable
DASHBOARD_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.quant.dashboard.plist"


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _launchctl(action: str) -> None:
    if not DASHBOARD_PLIST.exists():
        return
    subprocess.run(["launchctl", action, str(DASHBOARD_PLIST)], capture_output=True, text=True)


def _print_result(title: str, result: subprocess.CompletedProcess) -> None:
    print(f"\n--- {title} exit={result.returncode} ---")
    output = (result.stdout + result.stderr).strip()
    if not output:
        print("(无输出)")
        return
    lines = [
        line for line in output.splitlines()
        if "possibly delisted" not in line and "yfinance CN daily empty" not in line
    ]
    print("\n".join(lines[-80:]))


def _fetch_symbol(symbol: str, market: str, start_date: date, end_date: date):
    from src.data_pipeline.fetchers import akshare_fetcher as ak
    from src.data_pipeline.fetchers import yfinance_fetcher as yf

    start_ak = start_date.strftime("%Y%m%d")
    end_ak = end_date.strftime("%Y%m%d")
    start_yf = start_date.strftime("%Y-%m-%d")
    end_yf = end_date.strftime("%Y-%m-%d")

    if market == "CN":
        df = ak.fetch_cn_stock_daily(symbol, start_date=start_ak, end_date=end_ak, log_empty=False)
        source = f"akshare:{ak.source_status(df)}"
        if df.empty:
            df_yf = yf.fetch_cn_daily(symbol, start_yf, end_yf, log_empty=False)
            source += f", yfinance:{yf.source_status(df_yf)}"
            if not df_yf.empty:
                df = df_yf
        return df, source

    df = yf.fetch_hk_daily(symbol, start_yf, end_yf, log_empty=False)
    source = f"yfinance:{yf.source_status(df)}"
    if df.empty:
        df_ak = ak.fetch_hk_stock_daily(symbol, start_date=start_ak, end_date=end_ak, log_empty=False)
        source += f", akshare:{ak.source_status(df_ak)}"
        if not df_ak.empty:
            df = df_ak
    return df, source


def _load_target_symbols(conn) -> list[tuple[str, str]]:
    rows = conn.execute("""
        WITH latest_positions AS (
            SELECT symbol, quantity
            FROM paper_positions
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY strategy_name, symbol ORDER BY trade_date DESC
            ) = 1
        ),
        targets AS (
            SELECT symbol FROM latest_positions WHERE quantity > 0
            UNION
            SELECT symbol FROM signals
            WHERE executed = FALSE
              AND COALESCE(status, 'ACTIVE') = 'ACTIVE'
        )
        SELECT DISTINCT t.symbol, COALESCE(si.country, 'CN') AS market
        FROM targets t
        LEFT JOIN stock_info si ON t.symbol = si.symbol
        ORDER BY market, t.symbol
    """).fetchall()
    return [(str(symbol), str(market or "CN")) for symbol, market in rows]


def _update_target_symbols() -> int:
    from src.data_pipeline.loader import get_connection, get_last_trade_date, init_db, upsert_daily_price

    conn = get_connection()
    try:
        init_db(conn)
        targets = _load_target_symbols(conn)
        if not targets:
            print("目标行情更新: 当前无持仓且无待执行信号。")
            return 0

        today = date.today()
        updated = 0
        no_data = 0
        skipped = 0
        print(f"目标行情更新: {len(targets)} 只")
        for symbol, market in targets:
            last = get_last_trade_date(conn, symbol)
            if last and last >= today:
                skipped += 1
                continue
            start = (last + timedelta(days=1)) if last else (today - timedelta(days=7))
            df, source = _fetch_symbol(symbol, market, start, today)
            if df.empty:
                no_data += 1
                print(f"  no_data {symbol} {market}: {source}")
                continue
            upsert_daily_price(conn, df)
            updated += 1
        print(f"目标行情更新汇总: targets={len(targets)} updated={updated} no_data={no_data} skipped={skipped}")
        return 0
    finally:
        conn.close()


def _prepare_env() -> None:
    os.chdir(PROJECT)
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)
    os.environ["no_proxy"] = "*"
    os.environ["PYTHONPATH"] = str(PROJECT)
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run opening-session paper trading.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would run without executing commands.")
    args = parser.parse_args()

    commands = [
        ("执行股票纸交易", [PYTHON, "-m", "src.portfolio.paper_engine"], 180),
        ("计算组合净值", [PYTHON, "-m", "src.portfolio.nav_calculator"], 180),
    ]

    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 开盘纸交易任务开始 ===")
    print(f"项目目录: {PROJECT}")
    print(f"Python: {PYTHON}")

    if args.dry_run:
        print("[dry-run] 目标行情更新: 当前持仓 + 待执行信号")
        for title, cmd, _timeout in commands:
            print(f"[dry-run] {title}: {' '.join(cmd)}")
        return 0

    _prepare_env()

    print("暂停 Dashboard...")
    _launchctl("unload")
    time.sleep(3)

    exit_code = 0
    try:
        _update_target_symbols()
        for title, cmd, timeout in commands:
            result = _run(cmd, timeout=timeout)
            _print_result(title, result)
            if title != "更新行情数据" and result.returncode != 0:
                exit_code = result.returncode
    finally:
        print("\n重启 Dashboard...")
        _launchctl("load")
        print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 开盘纸交易任务结束 ===")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
