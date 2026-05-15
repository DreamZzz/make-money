"""Historical CN daily-price backfill helpers.

This module is intentionally separate from the normal daily update pipeline:
it fills missing old history without changing the incremental close workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from typing import Any, Callable, Iterable

import pandas as pd
from loguru import logger

from src.config import PROJECT_ROOT
from src.data_pipeline.fetchers import akshare_fetcher as ak
from src.data_pipeline.fetchers import yfinance_fetcher as yf
from src.data_pipeline.loader import get_connection, init_db, upsert_daily_price, upsert_index_daily


CN_INDEX_CODES = ["000300", "000905"]
DEFAULT_QLIB_INSTRUMENTS = PROJECT_ROOT / "qlib_data" / "cn_data" / "instruments" / "all.txt"


def _date(value: str | date | pd.Timestamp) -> date:
    return pd.to_datetime(value).date()


def _ak_date(value: str | date | pd.Timestamp) -> str:
    return pd.to_datetime(value).strftime("%Y%m%d")


def _read_qlib_symbols(path=DEFAULT_QLIB_INSTRUMENTS) -> list[str]:
    path = pd.io.common.stringify_path(path)
    if not os.path.exists(path):
        return []
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.strip().split()
            if parts:
                out.append(str(parts[0]).strip())
    return sorted(dict.fromkeys(symbol for symbol in out if symbol))


def _select_cn_symbols(
    conn: Any,
    symbols: Iterable[str] | None = None,
    limit: int | None = None,
    universe: str = "qlib",
    qlib_instruments_path=DEFAULT_QLIB_INSTRUMENTS,
) -> list[str]:
    if symbols is not None:
        selected = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
    elif universe == "qlib":
        selected = _read_qlib_symbols(qlib_instruments_path)
        if not selected:
            selected = _select_cn_symbols(conn, universe="daily_price")
    elif universe == "daily_price":
        selected = [
            row[0]
            for row in conn.execute("""
                SELECT DISTINCT dp.symbol
                FROM daily_price dp
                JOIN stock_info si ON si.symbol = dp.symbol
                WHERE si.country = 'CN'
                ORDER BY dp.symbol
            """).fetchall()
        ]
    elif universe == "stock_info":
        selected = [
            row[0]
            for row in conn.execute("""
                SELECT symbol
                FROM stock_info
                WHERE country = 'CN'
                ORDER BY symbol
            """).fetchall()
        ]
    else:
        raise ValueError("universe must be qlib, daily_price, or stock_info")
    if limit is not None:
        return selected[: max(int(limit), 0)]
    return selected


def _current_min_trade_date(conn: Any, symbol: str) -> date | None:
    row = conn.execute("SELECT MIN(trade_date) FROM daily_price WHERE symbol = ?", [symbol]).fetchone()
    return row[0] if row and row[0] is not None else None


def _filter_missing_old_history(df: pd.DataFrame, start: date, fetch_end: date) -> pd.DataFrame:
    if df.empty or "trade_date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    return out[(out["trade_date"] >= start) & (out["trade_date"] <= fetch_end)].copy()


def _clear_proxy_env() -> None:
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)


def backfill_cn_history(
    conn: Any,
    start_date: str = "2016-01-01",
    end_date: str | None = None,
    symbols: Iterable[str] | None = None,
    universe: str = "qlib",
    limit: int | None = None,
    sleep_seconds: float = 0.0,
    fetch_daily: Callable[..., pd.DataFrame] | None = None,
    fetch_index: Callable[..., pd.DataFrame] | None = None,
    index_codes: Iterable[str] = CN_INDEX_CODES,
) -> dict[str, Any]:
    """Backfill missing old CN stock rows and CN index rows.

    For each stock, only dates before its current local minimum trade date are
    inserted. This keeps recent rows from the existing daily update path intact.
    """
    init_db(conn)
    start = _date(start_date)
    end = _date(end_date or date.today())
    fetch_daily = fetch_daily or yf.fetch_cn_daily
    fetch_index = fetch_index or ak.fetch_cn_index_daily
    selected_symbols = _select_cn_symbols(conn, symbols=symbols, limit=limit, universe=universe)
    stats: dict[str, Any] = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "cn_symbols": len(selected_symbols),
        "universe": "explicit" if symbols is not None else universe,
        "cn_attempted": 0,
        "cn_updated_symbols": 0,
        "cn_inserted_rows": 0,
        "cn_skipped_covered": 0,
        "cn_empty": 0,
        "cn_failed": 0,
        "index_attempted": 0,
        "index_inserted_rows": 0,
        "index_failed": 0,
    }

    for i, symbol in enumerate(selected_symbols, start=1):
        current_min = _current_min_trade_date(conn, symbol)
        if current_min is not None and current_min <= start:
            stats["cn_skipped_covered"] += 1
            continue
        fetch_end = min(end, (current_min - timedelta(days=1)) if current_min else end)
        if fetch_end < start:
            stats["cn_skipped_covered"] += 1
            continue

        stats["cn_attempted"] += 1
        try:
            df = fetch_daily(symbol, start.isoformat(), fetch_end.isoformat(), log_empty=False)
            missing = _filter_missing_old_history(df, start, fetch_end)
            if missing.empty:
                stats["cn_empty"] += 1
            else:
                rows = upsert_daily_price(conn, missing)
                stats["cn_inserted_rows"] += rows
                stats["cn_updated_symbols"] += 1
            if stats["cn_attempted"] % 50 == 0 or i == len(selected_symbols):
                logger.info(
                    "CN backfill progress: "
                    f"{i}/{len(selected_symbols)} attempted={stats['cn_attempted']} "
                    f"updated={stats['cn_updated_symbols']} rows={stats['cn_inserted_rows']} "
                    f"empty={stats['cn_empty']} failed={stats['cn_failed']}"
                )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        except Exception as exc:
            stats["cn_failed"] += 1
            logger.warning(f"CN history backfill failed for {symbol}: {exc}")

    for code in index_codes:
        stats["index_attempted"] += 1
        try:
            df = fetch_index(str(code), _ak_date(start), _ak_date(end))
            rows = upsert_index_daily(conn, df) if not df.empty else 0
            stats["index_inserted_rows"] += rows
        except Exception as exc:
            stats["index_failed"] += 1
            logger.warning(f"CN index history backfill failed for {code}: {exc}")

    return stats


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill historical CN daily prices before current local coverage.")
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma separated stock symbols. Defaults to the selected universe.",
    )
    parser.add_argument(
        "--universe",
        choices=["qlib", "daily_price", "stock_info"],
        default="qlib",
        help="Default qlib uses qlib_data/cn_data/instruments/all.txt; fallback is daily_price if missing.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--skip-proxy-env-clear", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_proxy_env_clear:
        _clear_proxy_env()

    conn = get_connection()
    try:
        stats = backfill_cn_history(
            conn,
            start_date=args.start_date,
            end_date=args.end_date,
            symbols=_parse_symbols(args.symbols),
            universe=args.universe,
            limit=args.limit,
            sleep_seconds=args.sleep_seconds,
        )
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, default=str, indent=2))
    return 0 if stats.get("cn_failed", 0) == 0 and stats.get("index_failed", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
