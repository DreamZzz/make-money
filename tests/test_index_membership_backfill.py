from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.index_membership_backfill import (
    build_membership_coverage_report,
    import_membership_archive,
    normalize_membership_archive,
)
from src.data_pipeline.loader import init_db


def test_normalize_membership_archive_accepts_cn_interval_columns():
    raw = pd.DataFrame({
        "指数代码": ["000300", "000300"],
        "成分券代码": [1, "000002"],
        "纳入日期": ["2020-01-01", "2020-06-01"],
        "剔除日期": ["2020-05-31", ""],
        "来源": ["csindex_archive", "csindex_archive"],
    })

    result = normalize_membership_archive(raw, source="manual_archive")

    assert result.to_records(index=False).tolist() == [
        ("000300", "000001", date(2020, 1, 1), date(2020, 5, 31), "csindex_archive"),
        ("000300", "000002", date(2020, 6, 1), None, "csindex_archive"),
    ]


def test_import_membership_archive_upserts_csv_and_builds_coverage(tmp_path):
    archive = tmp_path / "membership.csv"
    archive.write_text(
        "index_code,symbol,start_date,end_date,source\n"
        "000300,000001,2020-01-01,2020-12-31,test_archive\n"
        "000300,000002,2020-06-01,,test_archive\n"
        "000905,000003,2020-01-01,,test_archive\n",
        encoding="utf-8",
    )
    conn = duckdb.connect(":memory:")
    init_db(conn)

    stats = import_membership_archive(conn, [archive], source="manual_archive")
    report = build_membership_coverage_report(conn, as_of=date(2021, 1, 4))

    rows = conn.execute("""
        SELECT index_code, symbol, start_date, end_date, source
        FROM index_member_history
        ORDER BY index_code, symbol
    """).fetchall()
    assert stats == {"files": 1, "input_rows": 3, "written_rows": 3}
    assert rows == [
        ("000300", "000001", date(2020, 1, 1), date(2020, 12, 31), "test_archive"),
        ("000300", "000002", date(2020, 6, 1), None, "test_archive"),
        ("000905", "000003", date(2020, 1, 1), None, "test_archive"),
    ]
    assert report.loc[report["index_code"] == "000300", "total_rows"].iloc[0] == 2
    assert report.loc[report["index_code"] == "000300", "active_members"].iloc[0] == 1
    assert report.loc[report["index_code"] == "000905", "active_members"].iloc[0] == 1
