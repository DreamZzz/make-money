from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db, upsert_daily_price


def _conn():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '平安银行')")
    return conn


def test_backfill_cn_history_only_inserts_dates_before_existing_min():
    from src.data_pipeline.history_backfill import backfill_cn_history

    conn = _conn()
    upsert_daily_price(
        conn,
        pd.DataFrame({
            "symbol": ["000001"],
            "trade_date": [date(2021, 4, 30)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000],
        }),
    )

    calls = []

    def fake_fetch(symbol, start_date, end_date, log_empty=False):
        calls.append((symbol, start_date, end_date))
        return pd.DataFrame({
            "symbol": [symbol, symbol],
            "trade_date": [pd.Timestamp("2016-01-04"), pd.Timestamp("2021-05-06")],
            "open": [1.0, 20.0],
            "high": [1.1, 21.0],
            "low": [0.9, 19.0],
            "close": [1.05, 20.5],
            "volume": [100, 2000],
        })

    result = backfill_cn_history(
        conn,
        start_date="2016-01-01",
        end_date="2026-05-13",
        symbols=["000001"],
        fetch_daily=fake_fetch,
        fetch_index=lambda code, start, end: pd.DataFrame(),
    )

    rows = conn.execute("""
        SELECT trade_date, close
        FROM daily_price
        WHERE symbol = '000001'
        ORDER BY trade_date
    """).fetchall()
    assert calls == [("000001", "2016-01-01", "2021-04-29")]
    assert rows == [(date(2016, 1, 4), 1.05), (date(2021, 4, 30), 10.5)]
    assert result["cn_inserted_rows"] == 1
    assert result["cn_updated_symbols"] == 1
    conn.close()


def test_backfill_cn_history_skips_symbol_already_covered():
    from src.data_pipeline.history_backfill import backfill_cn_history

    conn = _conn()
    upsert_daily_price(
        conn,
        pd.DataFrame({
            "symbol": ["000001"],
            "trade_date": [date(2016, 1, 4)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000],
        }),
    )

    def fail_fetch(symbol, start_date, end_date, log_empty=False):
        raise AssertionError("covered symbol should not be fetched")

    result = backfill_cn_history(
        conn,
        start_date="2016-01-04",
        end_date="2026-05-13",
        symbols=["000001"],
        fetch_daily=fail_fetch,
        fetch_index=lambda code, start, end: pd.DataFrame(),
    )

    assert result["cn_skipped_covered"] == 1
    assert result["cn_attempted"] == 0
    conn.close()


def test_select_cn_symbols_prefers_qlib_instrument_universe(tmp_path):
    from src.data_pipeline.history_backfill import _select_cn_symbols

    conn = _conn()
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000002', 'CN', '万科A')")
    instrument_path = tmp_path / "all.txt"
    instrument_path.write_text("000002\t2021-04-30\t2026-05-13\n000001\t2021-04-30\t2026-05-13\n")

    selected = _select_cn_symbols(conn, universe="qlib", qlib_instruments_path=instrument_path)

    assert selected == ["000001", "000002"]
    conn.close()


def test_backfill_cn_history_upserts_cn_indices():
    from src.data_pipeline.history_backfill import backfill_cn_history

    conn = _conn()
    calls = []

    def fake_index(code, start_date, end_date):
        calls.append((code, start_date, end_date))
        return pd.DataFrame({
            "index_code": [code],
            "trade_date": [pd.Timestamp("2016-01-04")],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [1000],
        })

    result = backfill_cn_history(
        conn,
        start_date="2016-01-01",
        end_date="2026-05-13",
        symbols=[],
        fetch_daily=lambda *args, **kwargs: pd.DataFrame(),
        fetch_index=fake_index,
        index_codes=["000300"],
    )

    rows = conn.execute("SELECT index_code, trade_date, close FROM index_daily").fetchall()
    assert calls == [("000300", "20160101", "20260513")]
    assert rows == [("000300", date(2016, 1, 4), 1.05)]
    assert result["index_inserted_rows"] == 1
    conn.close()
