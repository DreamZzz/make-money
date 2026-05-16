#!/usr/bin/env python3
"""Backfill A-share quarterly financials from free AkShare endpoints."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.data_pipeline.financials_backfill import backfill_cn_financials
from src.data_pipeline.loader import get_connection, init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill CN financials from free AkShare data.")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated A-share symbols; default uses local CN symbols with daily_price rows",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of selected symbols to process")
    parser.add_argument("--sleep", type=float, default=0.8, help="Seconds to sleep between network calls")
    parser.add_argument("--force", action="store_true", help="Refetch symbols that already have financial rows")
    parser.add_argument(
        "--all-stock-info",
        action="store_true",
        help="Scan all CN stock_info symbols instead of only symbols with local daily_price rows",
    )
    args = parser.parse_args(argv)

    symbols = [part.strip() for part in args.symbols.split(",") if part.strip()] or None
    conn = get_connection()
    try:
        init_db(conn)
        result = backfill_cn_financials(
            conn,
            symbols=symbols,
            limit=args.limit,
            force=args.force,
            sleep_seconds=args.sleep,
            priced_only=not args.all_stock_info and symbols is None,
        )
    except Exception as exc:
        logger.exception(f"CN financials backfill failed: {exc}")
        result = {
            "status": "ERROR",
            "selected_symbols": 0,
            "attempted": 0,
            "skipped_existing": 0,
            "inserted_rows": 0,
            "empty": 0,
            "failed": 0,
            "failed_symbols": [],
            "error": str(exc),
        }
    finally:
        conn.close()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if result.get("status") in {"OK", "WARN"} else 1


def _json_default(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
