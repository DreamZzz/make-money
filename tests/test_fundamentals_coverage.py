from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.fetchers.akshare_fetcher import (
    normalize_cn_stock_individual_info,
    normalize_cn_stock_spot,
)
from src.data_pipeline.loader import init_db
from src.portfolio.fundamentals_coverage import (
    load_current_holding_coverage,
    refresh_current_holding_fundamentals,
)


def _seed_position(
    conn: duckdb.DuckDBPyConnection,
    symbol: str = "000001",
    industry: str | None = None,
    market_cap: float | None = None,
    pe_ttm: float | None = None,
    pb: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO stock_info (symbol, country, name, industry, market_cap)
        VALUES (?, 'CN', ?, ?, ?)
        """,
        [symbol, f"股票{symbol}", industry, market_cap],
    )
    conn.execute(
        """
        INSERT INTO daily_price (symbol, trade_date, close, pe_ttm, pb)
        VALUES (?, DATE '2026-05-15', 10, ?, ?)
        """,
        [symbol, pe_ttm, pb],
    )
    conn.execute(
        """
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2026-05-15', ?, 100, 10, 10, 1000)
        """,
        [symbol],
    )


def test_load_current_holding_coverage_flags_missing_fields():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_position(conn, industry=None, market_cap=None, pe_ttm=None, pb=None)

    coverage = load_current_holding_coverage(conn, as_of=date(2026, 5, 15))

    row = coverage.iloc[0]
    assert row["symbol"] == "000001"
    assert row["missing_industry"] is True
    assert row["missing_market_cap"] is True
    assert row["missing_pe_ttm"] is True
    assert row["missing_pb"] is True
    conn.close()


def test_refresh_current_holding_fundamentals_fills_missing_stock_info_and_valuation():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_position(conn, industry=None, market_cap=None, pe_ttm=None, pb=None)

    def fake_spot():
        return pd.DataFrame([{
            "symbol": "000001",
            "name": "平安银行",
            "market_cap": 2_000.0,
            "pe_ttm": 5.5,
            "pb": 0.8,
        }])

    def fake_individual(symbol: str):
        return pd.DataFrame([{"symbol": symbol, "industry": "银行", "market_cap": 2_100.0}])

    result = refresh_current_holding_fundamentals(
        conn,
        as_of=date(2026, 5, 15),
        fetch_cn_spot=fake_spot,
        fetch_cn_individual=fake_individual,
    )

    stock = conn.execute("""
        SELECT name, industry, market_cap
        FROM stock_info
        WHERE symbol = '000001'
    """).fetchone()
    price = conn.execute("""
        SELECT pe_ttm, pb
        FROM daily_price
        WHERE symbol = '000001' AND trade_date = DATE '2026-05-15'
    """).fetchone()

    assert result["status"] == "OK"
    assert result["holdings"] == 1
    assert result["updated_stock_info"] == 1
    assert result["updated_daily_price"] == 1
    assert result["missing_after"] == {
        "industry": 0,
        "market_cap": 0,
        "pe_ttm": 0,
        "pb": 0,
    }
    assert stock == ("股票000001", "银行", 2000.0)
    assert price == (5.5, 0.8)
    conn.close()


def test_refresh_current_holding_fundamentals_keeps_existing_values_without_force():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_position(conn, industry="银行", market_cap=1_000.0, pe_ttm=6.0, pb=0.9)

    result = refresh_current_holding_fundamentals(
        conn,
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: pd.DataFrame([{
            "symbol": "000001",
            "name": "新名字",
            "market_cap": 9_999.0,
            "pe_ttm": 99.0,
            "pb": 9.9,
        }]),
        fetch_cn_individual=lambda symbol: pd.DataFrame([{"symbol": symbol, "industry": "新行业"}]),
    )

    stock = conn.execute("SELECT name, industry, market_cap FROM stock_info WHERE symbol='000001'").fetchone()
    price = conn.execute("SELECT pe_ttm, pb FROM daily_price WHERE symbol='000001'").fetchone()

    assert result["status"] == "OK"
    assert result["updated_stock_info"] == 0
    assert result["updated_daily_price"] == 0
    assert stock == ("股票000001", "银行", 1000.0)
    assert price == (6.0, 0.9)
    conn.close()


def test_refresh_current_holding_fundamentals_reports_unresolved_symbols_without_raising():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_position(conn, industry=None, market_cap=None, pe_ttm=None, pb=None)

    def failing_individual(symbol: str):
        raise RuntimeError(f"boom {symbol}")

    result = refresh_current_holding_fundamentals(
        conn,
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: pd.DataFrame(),
        fetch_cn_individual=failing_individual,
    )

    assert result["status"] == "WARN"
    assert result["failed_symbols"] == ["000001"]
    assert result["missing_after"] == {
        "industry": 1,
        "market_cap": 1,
        "pe_ttm": 1,
        "pb": 1,
    }
    conn.close()


def test_normalize_cn_stock_spot_converts_market_cap_yuan_to_yi():
    raw = pd.DataFrame([{
        "代码": "1",
        "名称": "平安银行",
        "市盈率-动态": "5.5",
        "市净率": "0.8",
        "总市值": 200_000_000_000,
    }])

    result = normalize_cn_stock_spot(raw)

    row = result.iloc[0]
    assert row["symbol"] == "000001"
    assert row["name"] == "平安银行"
    assert row["pe_ttm"] == 5.5
    assert row["pb"] == 0.8
    assert row["market_cap"] == 2000.0


def test_normalize_cn_stock_individual_info_extracts_industry_and_market_cap_text():
    raw = pd.DataFrame({
        "item": ["股票简称", "行业", "总市值"],
        "value": ["平安银行", "银行", "2000亿"],
    })

    result = normalize_cn_stock_individual_info("1", raw)

    assert result.to_dict("records") == [{
        "symbol": "000001",
        "country": "CN",
        "name": "平安银行",
        "industry": "银行",
        "market_cap": 2000.0,
    }]
