from datetime import date

import duckdb
import pandas as pd
import pytest

from src.data_pipeline.loader import init_db
from src.portfolio.exposure_monitor import (
    compute_exposure_snapshot,
    load_exposure_snapshot,
)


def test_compute_exposure_snapshot_groups_industry_size_and_valuation_against_benchmark():
    holdings = pd.DataFrame([
        {
            "symbol": "000001",
            "name": "银行A",
            "strategy_name": "alpha158",
            "market_value": 60_000,
            "industry": "银行",
            "market_cap": 3_000,
            "pe_ttm": 6,
            "pb": 0.7,
        },
        {
            "symbol": "000002",
            "name": "制造B",
            "strategy_name": "trend",
            "market_value": 40_000,
            "industry": "制造",
            "market_cap": 80,
            "pe_ttm": 20,
            "pb": 2.0,
        },
    ])
    benchmark = pd.DataFrame([
        {"symbol": "000001", "industry": "银行", "market_cap": 3000},
        {"symbol": "000003", "industry": "消费", "market_cap": 1000},
    ])

    snapshot = compute_exposure_snapshot(holdings, benchmark)
    industry = snapshot["industry"].set_index("industry")
    size = snapshot["size"].set_index("size_bucket")
    summary = snapshot["summary"].iloc[0]

    assert industry.loc["银行", "weight"] == pytest.approx(0.60)
    assert industry.loc["银行", "benchmark_weight"] == pytest.approx(0.75)
    assert industry.loc["银行", "relative_weight"] == pytest.approx(-0.15)
    assert industry.loc["制造", "weight"] == pytest.approx(0.40)
    assert industry.loc["制造", "benchmark_weight"] == pytest.approx(0.0)
    assert size.loc["超大盘", "weight"] == pytest.approx(0.60)
    assert size.loc["小盘", "weight"] == pytest.approx(0.40)
    assert summary["position_count"] == 2
    assert summary["top1_weight"] == pytest.approx(0.60)
    assert summary["weighted_pe_ttm"] == pytest.approx(11.6)
    assert summary["weighted_pb"] == pytest.approx(1.22)
    assert summary["pe_coverage"] == pytest.approx(1.0)


def test_compute_exposure_snapshot_handles_empty_holdings():
    snapshot = compute_exposure_snapshot(pd.DataFrame(), pd.DataFrame())

    assert snapshot["positions"].empty
    assert snapshot["industry"].empty
    assert snapshot["size"].empty
    assert snapshot["summary"].iloc[0]["position_count"] == 0


def test_load_exposure_snapshot_uses_latest_holdings_and_active_benchmark_members():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name, industry, market_cap)
        VALUES
            ('000001', 'CN', '银行A', '银行', 3000),
            ('000002', 'CN', '制造B', '制造', 80),
            ('000003', 'CN', '消费C', '消费', 1000)
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close, pe_ttm, pb)
        VALUES
            ('000001', DATE '2026-05-14', 10, 6, 0.7),
            ('000002', DATE '2026-05-14', 10, 20, 2.0)
    """)
    conn.execute("""
        INSERT INTO paper_positions (
            strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value
        )
        VALUES
            ('alpha158', DATE '2026-05-14', '000001', 6000, 10, 10, 60000),
            ('trend', DATE '2026-05-14', '000002', 4000, 10, 10, 40000),
            ('trend', DATE '2026-05-13', '000003', 1000, 10, 10, 10000)
    """)
    conn.execute("""
        INSERT INTO index_member_history (index_code, symbol, start_date, end_date, source)
        VALUES
            ('000300', '000001', DATE '2020-01-01', NULL, 'test'),
            ('000300', '000003', DATE '2020-01-01', NULL, 'test'),
            ('000300', '000002', DATE '2020-01-01', DATE '2026-05-13', 'test')
    """)

    snapshot = load_exposure_snapshot(conn, benchmark_index="000300", as_of=date(2026, 5, 14))
    positions = snapshot["positions"].set_index("symbol")
    industry = snapshot["industry"].set_index("industry")

    assert positions.index.tolist() == ["000001", "000002"]
    assert industry.loc["银行", "benchmark_weight"] == pytest.approx(0.75)
    assert "制造" in industry.index
    assert industry.loc["制造", "benchmark_weight"] == pytest.approx(0.0)
    conn.close()
