#!/usr/bin/env python3
"""Probe free data sources and optionally record source-health diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.data_pipeline.free_source_probe import (
    build_probe_health_rows,
    default_fetchers,
    make_default_targets,
    probe_free_sources,
)
from src.data_pipeline.loader import get_connection, init_db, record_data_source_health
from src.data_pipeline.network_env import prepare_finance_data_environment


def _load_symbols(limit: int) -> list[str]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute("""
            SELECT DISTINCT dp.symbol
            FROM daily_price dp
            JOIN stock_info si ON si.symbol = dp.symbol
            WHERE si.country = 'CN'
              AND regexp_matches(dp.symbol, '^[0-9]{6}$')
            ORDER BY dp.symbol
            LIMIT ?
        """, [limit]).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def main() -> None:
    prepare_finance_data_environment()
    parser = argparse.ArgumentParser(description="Probe free CN data sources without changing trading decisions.")
    parser.add_argument("--symbols", nargs="*", help="A-share symbols to probe, e.g. 000001 600519")
    parser.add_argument("--sample-size", type=int, default=10, help="Number of local CN symbols to sample when --symbols is omitted.")
    parser.add_argument(
        "--sources",
        default="tencent,mootdx,eastmoney_report,ths_concept",
        help="Comma-separated sources: tencent,mootdx,eastmoney_report,ths_concept",
    )
    parser.add_argument("--start-date", default=(date.today() - timedelta(days=10)).strftime("%Y%m%d"))
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--record-health", action="store_true", help="Write probe result rows to data_source_health.")
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols] if args.symbols else _load_symbols(args.sample_size)
    sources = [item.strip() for item in args.sources.split(",") if item.strip()]
    targets = make_default_targets(symbols, sources)
    probe = probe_free_sources(
        targets,
        fetchers=default_fetchers(args.start_date, args.end_date),
    )

    if args.record_health:
        conn = get_connection(read_only=False)
        try:
            init_db(conn)
            inserted = record_data_source_health(conn, build_probe_health_rows(probe))
            probe["recorded_health_rows"] = inserted
        finally:
            conn.close()

    print(json.dumps(probe, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
