from datetime import date

import pandas as pd
import pytest

from src.data_pipeline.loader import init_db
from src.research.strategies.value_quality import (
    compute_value_quality_scores,
    generate_signals,
    load_fundamentals_snapshot,
    measure_return_correlation,
    simulate_topn_equal_weight_returns,
)


def test_compute_value_quality_scores_prefers_low_valuation_high_quality():
    fundamentals = pd.DataFrame([
        {
            "symbol": "000001",
            "trade_date": date(2026, 5, 15),
            "pe_ttm": 6.0,
            "pb": 0.8,
            "roe": 18.0,
            "net_margin": 24.0,
            "debt_ratio": 35.0,
            "market_cap": 3000.0,
        },
        {
            "symbol": "000002",
            "trade_date": date(2026, 5, 15),
            "pe_ttm": 24.0,
            "pb": 3.0,
            "roe": 8.0,
            "net_margin": 8.0,
            "debt_ratio": 70.0,
            "market_cap": 200.0,
        },
        {
            "symbol": "000003",
            "trade_date": date(2026, 5, 15),
            "pe_ttm": 10.0,
            "pb": 1.2,
            "roe": 12.0,
            "net_margin": 12.0,
            "debt_ratio": 50.0,
            "market_cap": 800.0,
        },
    ])

    scored = compute_value_quality_scores(fundamentals)

    assert scored.iloc[0]["symbol"] == "000001"
    assert scored["score"].between(0, 1).all()
    assert {"value_score", "quality_score", "liquidity_score", "coverage"}.issubset(scored.columns)
    assert scored.set_index("symbol").loc["000001", "score"] > scored.set_index("symbol").loc["000002", "score"]


def test_generate_signals_emits_standard_value_quality_buy_rows():
    scored = pd.DataFrame([
        {
            "symbol": "000001",
            "trade_date": date(2026, 5, 15),
            "score": 0.91,
            "value_score": 0.95,
            "quality_score": 0.88,
            "coverage": 1.0,
        },
        {
            "symbol": "000002",
            "trade_date": date(2026, 5, 15),
            "score": 0.55,
            "value_score": 0.50,
            "quality_score": 0.60,
            "coverage": 1.0,
        },
    ])

    signals = generate_signals(scored, top_n=1, min_score=0.6, expected_holding_days=20)
    row = signals.iloc[0]

    assert len(signals) == 1
    assert row["model_name"] == "value_quality"
    assert row["symbol"] == "000001"
    assert row["side"] == "BUY"
    assert row["horizon"] == "20d"
    assert row["confidence"] == pytest.approx(0.91)
    assert "value_quality" in row["risk_tags"]


def test_simulate_topn_returns_and_correlation_against_reference():
    scores = pd.DataFrame([
        {"trade_date": date(2026, 1, 1), "symbol": "A", "score": 0.9},
        {"trade_date": date(2026, 1, 1), "symbol": "B", "score": 0.1},
        {"trade_date": date(2026, 1, 3), "symbol": "A", "score": 0.2},
        {"trade_date": date(2026, 1, 3), "symbol": "B", "score": 0.8},
    ])
    prices = pd.DataFrame(
        {
            "A": [10.0, 11.0, 12.0, 12.0],
            "B": [10.0, 10.0, 10.0, 11.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
    )

    returns = simulate_topn_equal_weight_returns(scores, prices, top_n=1)
    corr = measure_return_correlation(returns, pd.Series([0.10, 0.09, 0.10], index=returns.index))

    assert returns.index.tolist() == pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]).tolist()
    assert returns.tolist() == pytest.approx([0.10, 1 / 11, 0.10])
    assert corr > 0.9


def test_load_fundamentals_snapshot_uses_latest_rows_as_of_date():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name, market_cap)
        VALUES ('000001', 'CN', '测试A', 1000), ('000002', 'CN', '测试B', 800)
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close, pe_ttm, pb)
        VALUES
            ('000001', DATE '2026-05-14', 10, 6, 0.8),
            ('000001', DATE '2026-05-16', 11, 30, 5),
            ('000002', DATE '2026-05-14', 10, 12, 1.5)
    """)
    conn.execute("""
        INSERT INTO financials (symbol, report_date, roe, net_margin, debt_ratio)
        VALUES
            ('000001', DATE '2026-03-31', 18, 20, 35),
            ('000001', DATE '2026-06-30', 1, 1, 90),
            ('000002', DATE '2026-03-31', 10, 12, 50)
    """)

    snapshot = load_fundamentals_snapshot(conn, as_of=date(2026, 5, 15)).set_index("symbol")

    assert snapshot.loc["000001", "trade_date"] == date(2026, 5, 14)
    assert snapshot.loc["000001", "pe_ttm"] == pytest.approx(6)
    assert snapshot.loc["000001", "roe"] == pytest.approx(18)
    assert snapshot.loc["000002", "pb"] == pytest.approx(1.5)
    conn.close()
