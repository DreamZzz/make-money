import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.data_pipeline.valuation_backfill import (
    backfill_market_valuation,
    build_valuation_frame,
)


def _fake_pe() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "middlePETTM": [30.0, 31.0],
        "quantileInRecent10YearsMiddlePeTtm": [0.5, 0.55],
        "quantileInAllHistoryMiddlePeTtm": [0.6, 0.62],
        "close": [4000.0, 4050.0],
    })


def _fake_pb() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "middlePB": [2.0, 2.1],
        "quantileInRecent10YearsMiddlePB": [0.4, 0.45],
        "quantileInAllHistoryMiddlePB": [0.5, 0.52],
    })


def test_build_valuation_frame_merges_pe_and_pb():
    frame = build_valuation_frame(_fake_pe(), _fake_pb())
    assert len(frame) == 2
    row = frame.iloc[1]
    assert row["pe_ttm_median"] == 31.0
    assert row["pe_ttm_pct_10y"] == 0.55
    assert row["pb_median"] == 2.1
    assert row["pb_pct_10y"] == 0.45
    assert row["source"] == "akshare_lg"


def test_backfill_market_valuation_persists():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    result = backfill_market_valuation(conn, fetchers=(_fake_pe, _fake_pb))
    assert result["status"] == "OK"
    assert result["rows"] == 2
    rows = conn.execute(
        "SELECT trade_date, pe_ttm_pct_10y, pb_pct_10y FROM market_valuation ORDER BY trade_date"
    ).fetchall()
    assert len(rows) == 2
    assert rows[-1][1] == 0.55
    conn.close()


def test_backfill_handles_fetch_failure_gracefully():
    conn = duckdb.connect(":memory:")
    init_db(conn)

    def _boom() -> pd.DataFrame:
        raise RuntimeError("network down")

    result = backfill_market_valuation(conn, fetchers=(_boom, _fake_pb))
    assert result["status"] == "FAILED"
    assert result["rows"] == 0
    conn.close()
