from datetime import date

import duckdb
import pandas as pd

from scripts.convert_to_qlib import build_dynamic_instruments, write_instrument_files
from src.data_pipeline.index_membership import (
    active_members,
    merge_membership_ranges,
    normalize_current_snapshot,
    normalize_index_constituent_snapshot,
    reconcile_index_member_snapshot,
)
from src.data_pipeline.loader import init_db
from src.data_pipeline.main import _persist_index_member_snapshots, _sync_index_member_history


def test_normalize_current_snapshot_uses_price_start_as_snapshot_start():
    result = normalize_current_snapshot("000300", ["000001", "000002"], date(2021, 1, 4), source="akshare_snapshot")

    assert list(result["index_code"]) == ["000300", "000300"]
    assert list(result["symbol"]) == ["000001", "000002"]
    assert set(result["start_date"]) == {date(2021, 1, 4)}
    assert result["end_date"].isna().all()
    assert set(result["source"]) == {"akshare_snapshot"}


def test_merge_membership_ranges_merges_overlapping_ranges():
    df = pd.DataFrame({
        "index_code": ["000300", "000300", "000300"],
        "symbol": ["000001", "000001", "000002"],
        "start_date": [date(2021, 1, 1), date(2021, 6, 1), date(2022, 1, 1)],
        "end_date": [date(2021, 12, 31), None, None],
        "source": ["x", "x", "x"],
    })

    merged = merge_membership_ranges(df)

    row = merged[merged["symbol"] == "000001"].iloc[0]
    assert row["start_date"] == date(2021, 1, 1)
    assert pd.isna(row["end_date"])


def test_active_members_respects_date_ranges():
    df = pd.DataFrame({
        "index_code": ["000300", "000300"],
        "symbol": ["A", "B"],
        "start_date": [date(2021, 1, 1), date(2022, 1, 1)],
        "end_date": [date(2021, 12, 31), None],
        "source": ["x", "x"],
    })

    assert active_members(df, "000300", date(2021, 6, 1)) == {"A"}
    assert active_members(df, "000300", date(2022, 6, 1)) == {"B"}


def test_normalize_index_constituent_snapshot_parses_csindex_columns():
    raw = pd.DataFrame({
        "日期": ["20260514", "20260514"],
        "成分券代码": [1, "000002"],
        "成分券名称": ["平安银行", "万科A"],
    })

    result = normalize_index_constituent_snapshot("000300", raw, source="csindex_snapshot")

    assert result.to_records(index=False).tolist() == [
        ("000300", "000001", date(2026, 5, 14), None, "csindex_snapshot"),
        ("000300", "000002", date(2026, 5, 14), None, "csindex_snapshot"),
    ]


def test_reconcile_index_member_snapshot_closes_removed_and_opens_added_members():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO index_member_history (index_code, symbol, start_date, end_date, source)
        VALUES
            ('000300', '000001', DATE '2024-01-01', NULL, 'historical'),
            ('000300', '000002', DATE '2024-01-01', NULL, 'historical')
    """)

    changed = reconcile_index_member_snapshot(
        conn,
        "000300",
        ["000002", "000003"],
        date(2024, 6, 17),
        source="csindex_snapshot",
    )

    rows = conn.execute("""
        SELECT index_code, symbol, start_date, end_date, source
        FROM index_member_history
        ORDER BY symbol, start_date
    """).fetchall()
    assert changed == 2
    assert rows == [
        ("000300", "000001", date(2024, 1, 1), date(2024, 6, 16), "historical,csindex_snapshot"),
        ("000300", "000002", date(2024, 1, 1), None, "historical"),
        ("000300", "000003", date(2024, 6, 17), None, "csindex_snapshot"),
    ]


def test_persist_index_member_snapshots_uses_existing_cn_price_start():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', 'A'), ('000002', 'CN', 'B')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close)
        VALUES
            ('000001', DATE '2020-01-02', 1, 1, 1, 1),
            ('000002', DATE '2020-02-03', 1, 1, 1, 1)
    """)

    written = _persist_index_member_snapshots(
        conn,
        {"000300": ["000001"], "000905": ["000002"]},
        "20190101",
    )

    rows = conn.execute("""
        SELECT index_code, symbol, start_date, end_date, source
        FROM index_member_history
        ORDER BY index_code, symbol
    """).fetchall()
    assert written == 2
    assert rows == [
        ("000300", "000001", date(2020, 1, 2), None, "akshare_snapshot"),
        ("000905", "000002", date(2020, 1, 2), None, "akshare_snapshot"),
    ]


def test_sync_index_member_history_prefers_csindex_snapshot_and_reconciles(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO index_member_history (index_code, symbol, start_date, end_date, source)
        VALUES
            ('000300', '000001', DATE '2024-01-01', NULL, 'historical'),
            ('000300', '000002', DATE '2024-01-01', NULL, 'historical')
    """)

    def fake_fetch(index_code):
        if index_code != "000300":
            return pd.DataFrame()
        return pd.DataFrame({
            "index_code": ["000300", "000300"],
            "symbol": ["000002", "000003"],
            "start_date": [date(2024, 6, 17), date(2024, 6, 17)],
            "end_date": [None, None],
            "source": ["csindex_snapshot", "csindex_snapshot"],
        })

    from src.data_pipeline import main

    monkeypatch.setattr(main.ak, "fetch_index_member_snapshot", fake_fetch)

    changed = _sync_index_member_history(conn, {"000300": ["000001", "000002"]}, "20200101")

    rows = conn.execute("""
        SELECT symbol, start_date, end_date, source
        FROM index_member_history
        WHERE index_code = '000300'
        ORDER BY symbol
    """).fetchall()
    assert changed == 2
    assert rows == [
        ("000001", date(2024, 1, 1), date(2024, 6, 16), "historical,csindex_snapshot"),
        ("000002", date(2024, 1, 1), None, "historical"),
        ("000003", date(2024, 6, 17), None, "csindex_snapshot"),
    ]


def test_build_dynamic_instruments_uses_membership_ranges():
    price_df = pd.DataFrame({
        "symbol": ["000001", "000001", "000002", "000002", "000003"],
        "date": [
            "2021-01-04",
            "2021-12-31",
            "2021-01-04",
            "2021-12-31",
            "2021-01-04",
        ],
    })
    membership_df = pd.DataFrame({
        "index_code": ["000300", "000905", "000905"],
        "symbol": ["000001", "000002", "000001"],
        "start_date": [date(2021, 2, 1), date(2021, 3, 1), date(2021, 10, 1)],
        "end_date": [date(2021, 8, 31), None, None],
        "source": ["x", "x", "x"],
    })

    instruments = build_dynamic_instruments(price_df, membership_df)

    assert set(instruments) == {"all", "csi300", "csi500", "csi800"}
    assert instruments["all"].to_records(index=False).tolist() == [
        ("000001", "2021-01-04", "2021-12-31"),
        ("000002", "2021-01-04", "2021-12-31"),
        ("000003", "2021-01-04", "2021-01-04"),
    ]
    assert instruments["csi300"].to_records(index=False).tolist() == [
        ("000001", "2021-02-01", "2021-08-31"),
    ]
    assert instruments["csi500"].to_records(index=False).tolist() == [
        ("000001", "2021-10-01", "2099-12-31"),
        ("000002", "2021-03-01", "2099-12-31"),
    ]
    assert instruments["csi800"].to_records(index=False).tolist() == [
        ("000001", "2021-02-01", "2021-08-31"),
        ("000001", "2021-10-01", "2099-12-31"),
        ("000002", "2021-03-01", "2099-12-31"),
    ]


def test_write_instrument_files_writes_csi800(tmp_path):
    instruments = {
        "all": pd.DataFrame([("000001", "2021-01-04", "2021-12-31")], columns=["symbol", "start", "end"]),
        "csi300": pd.DataFrame([("000001", "2021-01-04", "2021-12-31")], columns=["symbol", "start", "end"]),
        "csi500": pd.DataFrame(columns=["symbol", "start", "end"]),
        "csi800": pd.DataFrame([("000001", "2021-01-04", "2021-12-31")], columns=["symbol", "start", "end"]),
    }

    write_instrument_files(tmp_path, instruments)

    assert (tmp_path / "csi800.txt").read_text().strip() == "000001\t2021-01-04\t2021-12-31"
