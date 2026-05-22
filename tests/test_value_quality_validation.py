from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.research.strategies.value_quality_validation import (
    build_rebalance_dates,
    build_value_quality_score_panel,
    run_value_quality_validation,
)


def _seed_validation_db(conn: duckdb.DuckDBPyConnection) -> None:
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name, market_cap)
        VALUES
            ('000001', 'CN', 'A', 1000),
            ('000002', 'CN', 'B', 800),
            ('000003', 'CN', 'C', 600)
    """)
    for symbol, base in [("000001", 10.0), ("000002", 20.0), ("000003", 30.0)]:
        conn.execute(
            """
            INSERT INTO daily_price (symbol, trade_date, open, close, pre_close, pe_ttm, pb)
            VALUES
                (?, DATE '2022-01-28', ?, ?, ?, ?, ?),
                (?, DATE '2022-02-28', ?, ?, ?, ?, ?),
                (?, DATE '2022-03-31', ?, ?, ?, ?, ?),
                (?, DATE '2022-04-29', ?, ?, ?, ?, ?)
            """,
            [
                symbol, base, base, base * 0.99, 8.0 + base / 10, 1.0,
                symbol, base * 1.1, base * 1.1, base, 8.0 + base / 10, 1.0,
                symbol, base * 1.2, base * 1.2, base * 1.1, 8.0 + base / 10, 1.0,
                symbol, base * 1.3, base * 1.3, base * 1.2, 8.0 + base / 10, 1.0,
            ],
        )
    conn.execute("""
        INSERT INTO index_daily (index_code, trade_date, close)
        VALUES
            ('000300', DATE '2022-01-28', 100),
            ('000300', DATE '2022-02-28', 101),
            ('000300', DATE '2022-03-31', 102),
            ('000300', DATE '2022-04-29', 103)
    """)
    conn.execute("""
        INSERT INTO financials (
            symbol, report_date, revenue, net_profit, total_assets, total_equity,
            operating_cf, roe, roa, gross_margin, net_margin, debt_ratio, eps, bvps
        )
        VALUES
            ('000001', DATE '2021-09-30', 100, 20, 300, 200, 30, 18, 6, 40, 20, 30, 1, 5),
            ('000002', DATE '2022-03-31', 100, 5, 300, 100, 5, 3, 1, 20, 5, 80, 0.2, 2),
            ('000003', DATE '2021-09-30', 100, 10, 300, 150, 10, 10, 3, 30, 10, 50, 0.5, 3)
    """)


def test_build_rebalance_dates_uses_last_available_trade_date_per_month():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)

    dates = build_rebalance_dates(conn, start="2022-01-01", end="2022-03-31", rebalance_freq="monthly")

    assert dates == [date(2022, 1, 28), date(2022, 2, 28), date(2022, 3, 31)]
    conn.close()


def test_build_value_quality_score_panel_applies_financial_reporting_lag():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)

    panel = build_value_quality_score_panel(
        conn,
        start="2022-01-01",
        end="2022-03-31",
        rebalance_freq="monthly",
        financial_lag_days=60,
    )
    feb = panel[panel["trade_date"] == date(2022, 2, 28)].set_index("symbol")

    assert set(panel["trade_date"].unique()) == {date(2022, 1, 28), date(2022, 2, 28), date(2022, 3, 31)}
    assert feb.loc["000001", "quality_score"] > feb.loc["000002", "quality_score"]
    assert feb.loc["000002", "roe"] != 3
    conn.close()


def test_run_value_quality_validation_outputs_metrics_and_correlations():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)
    reference = pd.Series(
        [0.02, 0.01],
        index=pd.to_datetime(["2022-01-28", "2022-02-28"]),
        name="alpha158",
    )

    result = run_value_quality_validation(
        conn,
        start="2022-01-01",
        end="2022-03-31",
        top_n=2,
        holding_days=1,
        rebalance_freq="monthly",
        financial_lag_days=60,
        benchmark_returns=reference * 0.5,
        reference_returns=reference,
    )

    assert result["score_rows"] == 9
    assert result["return_periods"] > 0
    assert result["metrics"]["annual_return"] is not None
    assert "correlation_alpha158" in result
    assert "correlation_benchmark" in result
    conn.close()


def test_run_value_quality_validation_outputs_alpha_gate_result():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)

    result = run_value_quality_validation(
        conn,
        start="2022-01-01",
        end="2022-03-31",
        top_n=2,
        holding_days=1,
        rebalance_freq="monthly",
        financial_lag_days=60,
        benchmark_returns=pd.Series(
            [0.01, 0.01, 0.01, 0.01],
            index=pd.to_datetime(["2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29"]),
        ),
        reference_returns=pd.Series(
            [0.02, 0.01, 0.02, 0.01],
            index=pd.to_datetime(["2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29"]),
        ),
    )

    assert isinstance(result["alpha_gate_passed"], bool)
    assert isinstance(result["alpha_gate_failed_reasons"], list)
    assert "alpha_gate_metrics" in result
    conn.close()


def test_run_value_quality_validation_exposes_v02_options():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)

    result = run_value_quality_validation(
        conn,
        start="2022-01-01",
        end="2022-03-31",
        top_n=2,
        holding_days=1,
        rebalance_freq="monthly",
        financial_lag_days=60,
        industry_neutral=True,
        retention_quantile=0.30,
        benchmark_returns=pd.Series(
            [0.01, 0.01, 0.01, 0.01],
            index=pd.to_datetime(["2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29"]),
        ),
        reference_returns=pd.Series(
            [0.02, 0.01, 0.02, 0.01],
            index=pd.to_datetime(["2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29"]),
        ),
    )

    assert result["industry_neutral"] is True
    assert result["retention_quantile"] == 0.30
    conn.close()


def test_run_value_quality_validation_can_persist_research_only_backtest_result():
    conn = duckdb.connect(":memory:")
    _seed_validation_db(conn)

    result = run_value_quality_validation(
        conn,
        start="2022-01-01",
        end="2022-03-31",
        top_n=2,
        holding_days=1,
        rebalance_freq="monthly",
        financial_lag_days=60,
        benchmark_returns=pd.Series(
            [0.01, 0.01, 0.01, 0.01],
            index=pd.to_datetime(["2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29"]),
        ),
        save_result=True,
    )
    row = conn.execute("""
        SELECT strategy_name, engine, decision_scope
        FROM backtest_results
        WHERE run_id = ?
    """, [result["run_id"]]).fetchone()

    assert row == ("value_quality", "value_quality_validation", "research_only")
    conn.close()
