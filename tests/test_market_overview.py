from datetime import date

import duckdb
import pandas as pd
import pytest

from src.data_pipeline.loader import init_db


def _insert_stock(conn, symbol: str, country: str, name: str, industry: str | None = None):
    conn.execute(
        """
        INSERT INTO stock_info (symbol, country, name, industry, market_cap, currency)
        VALUES (?, ?, ?, ?, 1000, CASE WHEN ? = 'HK' THEN 'HKD' ELSE 'CNY' END)
        """,
        [symbol, country, name, industry, country],
    )


def _insert_daily(conn, symbol: str, trade_date: str, close: float, volume: float = 1000, amount: float | None = None):
    conn.execute(
        """
        INSERT INTO daily_price (
            symbol, trade_date, open, high, low, close, pre_close,
            volume, amount, turnover_rate, pe_ttm, pb
        )
        VALUES (?, ?::DATE, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL)
        """,
        [symbol, trade_date, close, close * 1.02, close * 0.98, close, volume, amount],
    )


def test_market_snapshot_schema_builder_and_upsert_replaces_rows():
    from src.data_pipeline.loader import build_market_snapshot_from_daily, upsert_market_snapshot

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _insert_stock(conn, "000001", "CN", "平安银行", "银行")
    _insert_daily(conn, "000001", "2024-01-02", 10, volume=1000, amount=100000)
    _insert_daily(conn, "000001", "2024-01-03", 11, volume=2000, amount=220000)

    rows = build_market_snapshot_from_daily(conn, markets=["CN"])
    assert rows == 1

    snap = conn.execute("""
        SELECT symbol, market, trade_date, last_price, prev_close, pct_chg,
               volume_ratio, amplitude, amount, source
        FROM market_snapshot
        WHERE symbol = '000001' AND trade_date = DATE '2024-01-03'
    """).fetchone()
    assert snap == ("000001", "CN", date(2024, 1, 3), 11.0, 10.0, pytest.approx(10.0), pytest.approx(2.0), pytest.approx(4.4), 220000.0, "daily_price")

    upsert_market_snapshot(conn, pd.DataFrame([{
        "symbol": "000001",
        "market": "CN",
        "trade_date": date(2024, 1, 3),
        "last_price": 12.0,
        "source": "manual",
    }]))
    updated = conn.execute("""
        SELECT last_price, source FROM market_snapshot
        WHERE symbol = '000001' AND trade_date = DATE '2024-01-03'
    """).fetchone()
    assert updated == (12.0, "manual")


def test_market_overview_uses_snapshot_distribution_and_field_coverage():
    from src.dashboard.market_service import load_field_coverage, load_market_overview
    from src.data_pipeline.loader import build_market_snapshot_from_daily

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _insert_stock(conn, "000001", "CN", "平安银行", "银行")
    _insert_stock(conn, "000002", "CN", "万科A", None)
    for symbol, first, second, amount in [
        ("000001", 10, 11, 220000),
        ("000002", 20, 19, None),
    ]:
        _insert_daily(conn, symbol, "2024-01-02", first, volume=1000, amount=amount)
        _insert_daily(conn, symbol, "2024-01-03", second, volume=1000, amount=amount)
    build_market_snapshot_from_daily(conn, markets=["CN"])

    overview = load_market_overview(conn)
    cn = overview["markets"]["CN"]
    assert cn["latest_date"] == date(2024, 1, 3)
    assert cn["total"] == 2
    assert cn["advancers"] == 1
    assert cn["decliners"] == 1
    assert cn["median_pct_chg"] == pytest.approx(2.5)

    distribution = overview["distribution"]
    assert set(distribution["bucket"]) >= {"上涨 0-3%", "下跌 0-3%"}

    coverage = load_field_coverage(conn)
    industry = coverage[(coverage["market"] == "CN") & (coverage["field"] == "industry")].iloc[0]
    amount = coverage[(coverage["market"] == "CN") & (coverage["field"] == "amount")].iloc[0]
    assert industry["coverage_pct"] == pytest.approx(0.5)
    assert amount["coverage_pct"] == pytest.approx(0.5)


def test_market_breadth_computes_ma_and_high_low_counts():
    from src.dashboard.market_service import load_market_breadth

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _insert_stock(conn, "000001", "CN", "趋势向上", "测试")
    _insert_stock(conn, "000002", "CN", "趋势向下", "测试")

    dates = pd.bdate_range("2024-01-01", periods=130)
    for i, dt in enumerate(dates, start=1):
        _insert_daily(conn, "000001", dt.strftime("%Y-%m-%d"), float(i), volume=1000 + i)
        _insert_daily(conn, "000002", dt.strftime("%Y-%m-%d"), float(131 - i), volume=2000 - i)

    breadth = load_market_breadth(conn)
    cn = breadth[breadth["market"] == "CN"].iloc[0]
    assert cn["total"] == 2
    assert cn["above_ma20_pct"] == pytest.approx(0.5)
    assert cn["above_ma60_pct"] == pytest.approx(0.5)
    assert cn["above_ma120_pct"] == pytest.approx(0.5)
    assert cn["new_high_20"] == 1
    assert cn["new_low_20"] == 1


def test_index_benchmarks_return_normalized_series_and_summary():
    from src.dashboard.market_service import load_index_benchmarks

    conn = duckdb.connect(":memory:")
    init_db(conn)
    for code, closes in {"000300": [100, 110, 105], "000905": [200, 190, 210]}.items():
        for idx, close in enumerate(closes):
            conn.execute(
                """
                INSERT INTO index_daily (index_code, trade_date, open, high, low, close, volume)
                VALUES (?, ?::DATE, ?, ?, ?, ?, 1000)
                """,
                [code, f"2024-01-0{idx + 2}", close, close, close, close],
            )

    result = load_index_benchmarks(conn, days=9999)
    series = result["series"]
    summary = result["summary"]
    first_300 = series[series["index_code"] == "000300"].iloc[0]
    row_300 = summary[summary["index_code"] == "000300"].iloc[0]
    assert first_300["normalized"] == pytest.approx(1.0)
    assert row_300["period_return"] == pytest.approx(0.05)
    assert row_300["max_drawdown"] == pytest.approx(-0.0454545, rel=1e-5)


def test_market_movers_falls_back_to_daily_and_adds_links_and_names():
    from src.dashboard.market_service import load_market_movers

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _insert_stock(conn, "000001", "CN", "平安银行", "银行")
    _insert_stock(conn, "00700", "HK", "腾讯控股", "互联网")
    _insert_daily(conn, "000001", "2024-01-02", 10, volume=1000, amount=100000)
    _insert_daily(conn, "000001", "2024-01-03", 12, volume=2000, amount=240000)
    _insert_daily(conn, "00700", "2024-01-02", 300, volume=1000, amount=300000)
    _insert_daily(conn, "00700", "2024-01-03", 270, volume=2000, amount=540000)

    movers = load_market_movers(conn, limit=5)
    gainers = movers["gainers"]
    losers = movers["losers"]
    assert gainers.iloc[0]["symbol"] == "000001"
    assert gainers.iloc[0]["name"] == "平安银行"
    assert "东方财富" in gainers.iloc[0]["links"]
    assert losers.iloc[0]["symbol"] == "00700"
    assert losers.iloc[0]["pct_chg"] == pytest.approx(-10.0)


def test_latest_quotes_ignore_stale_snapshot_for_updated_market():
    from src.dashboard.market_service import load_latest_quotes
    from src.data_pipeline.loader import build_market_snapshot_from_daily

    conn = duckdb.connect(":memory:")
    init_db(conn)
    _insert_stock(conn, "000001", "CN", "平安银行", "银行")
    _insert_daily(conn, "000001", "2024-01-02", 10, volume=1000, amount=100000)
    _insert_daily(conn, "000001", "2024-01-03", 11, volume=2000, amount=220000)
    build_market_snapshot_from_daily(conn, markets=["CN"])

    _insert_daily(conn, "000001", "2024-01-04", 12, volume=3000, amount=360000)
    quotes = load_latest_quotes(conn)
    row = quotes[quotes["symbol"] == "000001"].iloc[0]
    assert row["trade_date"] == date(2024, 1, 4)
    assert row["last_price"] == pytest.approx(12.0)
    assert row["source"] == "daily_price"
