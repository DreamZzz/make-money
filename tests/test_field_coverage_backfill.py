from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.field_coverage_backfill import (
    backfill_field_coverage,
    backfill_field_coverage_scopes,
    build_field_coverage_health_rows,
    load_field_coverage,
    resolve_scope_symbols,
)
from src.data_pipeline.loader import init_db


def _seed_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
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
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, pe_ttm, pb)
        VALUES (?, DATE '2026-05-15', 10, 11, 9, 10.5, ?, ?)
        """,
        [symbol, pe_ttm, pb],
    )


def test_resolve_scope_symbols_uses_latest_cn_daily_price_for_target_universe():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001")
    _seed_symbol(conn, "600519")
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('00700', 'HK', '腾讯控股')")
    conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('00700', DATE '2026-05-15', 400)")

    symbols = resolve_scope_symbols(conn, "target_universe", as_of=date(2026, 5, 15))

    assert symbols == ["000001", "600519"]
    conn.close()


def test_backfill_field_coverage_fills_target_universe_without_overwriting_existing_values():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001")
    _seed_symbol(conn, "600519", industry="白酒", market_cap=18000, pe_ttm=25, pb=8)

    spot = pd.DataFrame([
        {"symbol": "000001", "name": "平安银行", "market_cap": 2000.0, "pe_ttm": 5.5, "pb": 0.8},
        {"symbol": "600519", "name": "贵州茅台", "market_cap": 19000.0, "pe_ttm": 30.0, "pb": 9.0},
    ])
    individual_calls: list[str] = []

    def fake_individual(symbol: str) -> pd.DataFrame:
        individual_calls.append(symbol)
        return pd.DataFrame([{"symbol": symbol, "industry": "银行", "market_cap": 2100.0}])

    result = backfill_field_coverage(
        conn,
        scope="target_universe",
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: spot,
        fetch_cn_individual=fake_individual,
    )

    pingan = conn.execute("""
        SELECT name, industry, market_cap
        FROM stock_info
        WHERE symbol = '000001'
    """).fetchone()
    pingan_price = conn.execute("""
        SELECT pe_ttm, pb
        FROM daily_price
        WHERE symbol = '000001' AND trade_date = DATE '2026-05-15'
    """).fetchone()
    maotai = conn.execute("""
        SELECT industry, market_cap
        FROM stock_info
        WHERE symbol = '600519'
    """).fetchone()
    maotai_price = conn.execute("""
        SELECT pe_ttm, pb
        FROM daily_price
        WHERE symbol = '600519' AND trade_date = DATE '2026-05-15'
    """).fetchone()

    assert result["status"] == "OK"
    assert result["scope"] == "target_universe"
    assert result["symbols"] == 2
    assert result["updated_stock_info"] == 1
    assert result["updated_daily_price"] == 1
    assert result["missing_after"] == {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0}
    assert individual_calls == ["000001"]
    assert pingan == ("股票000001", "银行", 2000.0)
    assert pingan_price == (5.5, 0.8)
    assert maotai == ("白酒", 18000.0)
    assert maotai_price == (25.0, 8.0)
    conn.close()


def test_backfill_field_coverage_uses_tencent_quote_when_spot_is_empty():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001", industry="银行", market_cap=None, pe_ttm=None, pb=None)

    quote_calls: list[list[str]] = []

    def fake_quote(symbols: list[str]) -> pd.DataFrame:
        quote_calls.append(symbols)
        return pd.DataFrame([{
            "symbol": "000001",
            "name": "平安银行",
            "market_cap": 2107.48,
            "pe_ttm": 4.89,
            "pb": 0.45,
        }])

    result = backfill_field_coverage(
        conn,
        scope="target_universe",
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: pd.DataFrame(),
        fetch_tencent_quote=fake_quote,
        fetch_cn_individual=lambda symbol: pd.DataFrame(),
    )

    stock = conn.execute("SELECT market_cap FROM stock_info WHERE symbol='000001'").fetchone()
    price = conn.execute("SELECT pe_ttm, pb FROM daily_price WHERE symbol='000001'").fetchone()

    assert result["status"] == "OK"
    assert quote_calls == [["000001"]]
    assert stock == (2107.48,)
    assert price == (4.89, 0.45)
    conn.close()


def test_backfill_field_coverage_can_skip_per_symbol_industry_fetch():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001", industry=None, market_cap=None, pe_ttm=None, pb=None)
    individual_calls: list[str] = []

    result = backfill_field_coverage(
        conn,
        scope="target_universe",
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: pd.DataFrame(),
        fetch_tencent_quote=lambda symbols: pd.DataFrame([{
            "symbol": "000001",
            "name": "平安银行",
            "market_cap": 2107.48,
            "pe_ttm": 4.89,
            "pb": 0.45,
        }]),
        fetch_cn_individual=lambda symbol: individual_calls.append(symbol) or pd.DataFrame(),
        fetch_industry=False,
    )

    assert individual_calls == []
    assert result["status"] == "OK"
    assert result["failed_symbols"] == []
    assert result["missing_after"] == {"industry": 1, "market_cap": 0, "pe_ttm": 0, "pb": 0}
    conn.close()


def test_backfill_field_coverage_scopes_deduplicates_symbols_across_priority_scopes():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001")
    _seed_symbol(conn, "600519")
    conn.execute(
        """
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2026-05-15', '000001', 100, 10, 10, 1000)
        """
    )
    conn.execute(
        """
        INSERT INTO signals (signal_id, symbol, signal_ts, side, confidence, model_name, status)
        VALUES ('S1', '000001', TIMESTAMP '2026-05-15 15:30:00', 'BUY', 0.8, 'alpha158', 'ACTIVE')
        """
    )

    spot = pd.DataFrame([
        {"symbol": "000001", "name": "平安银行", "market_cap": 2000.0, "pe_ttm": 5.5, "pb": 0.8},
        {"symbol": "600519", "name": "贵州茅台", "market_cap": 19000.0, "pe_ttm": 30.0, "pb": 9.0},
    ])
    individual_calls: list[str] = []

    result = backfill_field_coverage_scopes(
        conn,
        scopes=["current_holdings", "signal_candidates", "target_universe"],
        as_of=date(2026, 5, 15),
        fetch_cn_spot=lambda: spot,
        fetch_cn_individual=lambda symbol: (
            individual_calls.append(symbol)
            or pd.DataFrame([{"symbol": symbol, "industry": "行业" + symbol, "market_cap": 1.0}])
        ),
    )

    assert [item["scope"] for item in result["scopes"]] == [
        "current_holdings",
        "signal_candidates",
        "target_universe",
    ]
    assert result["scopes"][0]["symbols"] == 1
    assert result["scopes"][1]["symbols"] == 0
    assert result["scopes"][2]["symbols"] == 1
    assert individual_calls == ["000001", "600519"]
    conn.close()


def test_backfill_field_coverage_current_holdings_ignores_stale_flat_positions():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001")
    conn.execute(
        """
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES ('alpha158', DATE '2026-05-15', '000001', 100, 10, 10, 1000)
        """
    )
    conn.execute("""
        INSERT INTO portfolio_nav (
            strategy_name, trade_date, nav, daily_return, cash, position_value,
            total_value, external_flow, net_contribution, investment_nav, drawdown, sharpe_rolling
        )
        VALUES
            ('alpha158', DATE '2026-05-15', 1, 0, 99000, 1000, 100000, 0, 100000, 1, 0, 0),
            ('alpha158', DATE '2026-05-16', 1, 0, 100000, 0, 100000, 0, 100000, 1, 0, 0)
    """)

    result = backfill_field_coverage_scopes(
        conn,
        scopes=["current_holdings"],
        as_of=date(2026, 5, 16),
        fetch_cn_spot=lambda: pd.DataFrame(),
        fetch_cn_individual=lambda _symbol: pd.DataFrame(),
    )

    assert result["scopes"][0]["symbols"] == 0
    assert result["scopes"][0]["status"] == "OK"
    conn.close()


def test_load_field_coverage_reports_missing_counts_for_symbols():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_symbol(conn, "000001", industry="银行", market_cap=2000, pe_ttm=5.5, pb=None)
    _seed_symbol(conn, "600519", industry=None, market_cap=None, pe_ttm=None, pb=None)

    coverage = load_field_coverage(conn, ["000001", "600519"], as_of=date(2026, 5, 15))

    assert coverage[["symbol", "missing_industry", "missing_market_cap", "missing_pe_ttm", "missing_pb"]].to_dict("records") == [
        {
            "symbol": "000001",
            "missing_industry": False,
            "missing_market_cap": False,
            "missing_pe_ttm": False,
            "missing_pb": True,
        },
        {
            "symbol": "600519",
            "missing_industry": True,
            "missing_market_cap": True,
            "missing_pe_ttm": True,
            "missing_pb": True,
        },
    ]
    conn.close()


def test_build_field_coverage_health_rows_summarizes_scope_results():
    result = {
        "scopes": [
            {
                "scope": "current_holdings",
                "status": "OK",
                "symbols": 2,
                "updated_stock_info": 1,
                "updated_daily_price": 2,
                "failed_symbols": [],
                "missing_after": {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0},
            },
            {
                "scope": "target_universe",
                "status": "WARN",
                "symbols": 3,
                "updated_stock_info": 1,
                "updated_daily_price": 1,
                "failed_symbols": ["600519"],
                "missing_after": {"industry": 1, "market_cap": 0, "pe_ttm": 0, "pb": 0},
            },
        ],
    }

    rows = build_field_coverage_health_rows(result, run_id="FC-1")

    assert rows == [
        {
            "run_id": "FC-1",
            "source": "free_sources",
            "market": "CN",
            "operation": "field_coverage_current_holdings",
            "status": "OK",
            "attempted": 2,
            "updated": 2,
            "no_data": 0,
            "source_error": 0,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": 0,
            "message": "field coverage backfill current_holdings: 2 symbols, 1 stock_info, 2 valuation updates, 0 unresolved",
            "stats_json": {
                "failed_symbols": [],
                "missing_after": {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0},
            },
        },
        {
            "run_id": "FC-1",
            "source": "free_sources",
            "market": "CN",
            "operation": "field_coverage_target_universe",
            "status": "DEGRADED",
            "attempted": 3,
            "updated": 2,
            "no_data": 1,
            "source_error": 0,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": 1,
            "message": "field coverage backfill target_universe: 3 symbols, 1 stock_info, 1 valuation updates, 1 unresolved",
            "stats_json": {
                "failed_symbols": ["600519"],
                "missing_after": {"industry": 1, "market_cap": 0, "pe_ttm": 0, "pb": 0},
            },
        },
    ]
