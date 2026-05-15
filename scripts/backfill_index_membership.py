#!/usr/bin/env python3
"""Import historical index membership interval archives into DuckDB."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.data_pipeline.index_membership_backfill import build_membership_coverage_report, import_membership_archive
from src.data_pipeline.loader import get_connection, init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import historical index membership archive files.")
    parser.add_argument("paths", nargs="+", help="CSV/XLS/XLSX files with index_code,symbol,start_date,end_date columns")
    parser.add_argument("--source", default="manual_archive", help="Source label used when a file has no source column")
    parser.add_argument("--report", default="docs/index_membership_coverage.md", help="Markdown coverage report path")
    args = parser.parse_args(argv)

    conn = get_connection()
    try:
        init_db(conn)
        stats = import_membership_archive(conn, args.paths, source=args.source)
        report = build_membership_coverage_report(conn)
    finally:
        conn.close()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Index Membership Coverage",
        "",
        f"- Imported files: {stats['files']}",
        f"- Input rows: {stats['input_rows']}",
        f"- Written rows: {stats['written_rows']}",
        "",
    ]
    if report.empty:
        lines.append("No membership rows found.")
    else:
        lines.append(report.to_markdown(index=False))
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"imported={stats['written_rows']} report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
