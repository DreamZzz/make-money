from datetime import date

import duckdb
import pandas as pd
import pytest

from src.config import DEFAULT_CONFIG
from src.data_pipeline.loader import init_db, upsert_fund_info, upsert_fund_nav
from src.index_funds.config import FundWatchItem, get_watchlist
from src.index_funds.performance import compute_max_drawdown, evaluate_holdings
from src.index_funds.signals import calculate_signal

RULES = {
    "valuation_window_days": 30,
    "low_valuation_percentile": 0.50,
    "high_valuation_percentile": 0.80,
    "trend_ma_fast": 5,
    "trend_ma_slow": 10,
    "rebalance_threshold_pct": 0.05,
    "min_confidence": 0.45,
}


def _item(fund_code: str = "510300", target_weight: float = 0.50) -> FundWatchItem:
    return FundWatchItem(
        fund_code=fund_code,
        name="测试沪深300ETF",
        fund_type="ETF",
        tracking_index="000300",
        tracking_index_name="沪深300",
        market="CN",
        currency="CNY",
        target_weight=target_weight,
    )


def _index_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": pd.bdate_range("2024-01-01", periods=len(closes)),
        "close": closes,
    })


def test_index_fund_signal_buy_when_low_percentile_and_trend_recovers():
    closes = [130, 125, 120, 115, 110, 105, 100, 95, 90, 85, 80, 78, 76, 74, 72, 70, 69, 68, 70, 72, 74, 76, 78, 80, 82]
    rules = {**RULES, "low_valuation_percentile": 0.65}
    signal = calculate_signal(_item(), _index_df(closes), rules, current_weight=0.0)
    assert signal.action == "BUY"
    assert signal.confidence >= rules["min_confidence"]
    assert "低分位" in signal.thesis


def test_index_fund_signal_reduce_when_high_percentile_and_overweight():
    closes = list(range(80, 110))
    signal = calculate_signal(_item(), _index_df(closes), RULES, current_weight=0.62)
    assert signal.action == "REDUCE"
    assert "overweight" in signal.risk_tags


def test_index_fund_signal_pause_when_trend_weak():
    closes = list(range(130, 100, -1))
    signal = calculate_signal(_item(), _index_df(closes), RULES, current_weight=0.45)
    assert signal.action == "PAUSE"
    assert "trend_weak" in signal.risk_tags


def test_evaluate_holdings_from_latest_snapshot():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    upsert_fund_info(conn, pd.DataFrame([{
        "fund_code": "510300",
        "name": "测试沪深300ETF",
        "fund_type": "ETF",
        "tracking_index": "000300",
        "market": "CN",
        "currency": "CNY",
        "enabled": True,
    }]))
    upsert_fund_nav(conn, pd.DataFrame({
        "fund_code": ["510300", "510300"],
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
        "nav": [1.0, 1.2],
        "close": [1.0, 1.2],
        "premium_discount": [None, None],
    }))
    conn.execute("""
        INSERT INTO index_daily (index_code, trade_date, close)
        VALUES ('000300', DATE '2024-01-02', 1000), ('000300', DATE '2024-01-03', 1100)
    """)
    conn.execute("""
        INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount, note)
        VALUES ('S1', DATE '2024-01-02', '510300', 1000, 1000, 'initial')
    """)
    result = evaluate_holdings(conn)
    assert result.iloc[0]["market_value"] == pytest.approx(1200)
    assert result.iloc[0]["holding_return"] == pytest.approx(0.2)
    assert result.iloc[0]["tracking_index_return"] == pytest.approx(0.1)
    assert result.iloc[0]["excess_return"] == pytest.approx(0.1)


def test_fund_nav_upsert_replaces_existing_row():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    upsert_fund_nav(conn, pd.DataFrame({
        "fund_code": ["510300"],
        "trade_date": [date(2024, 1, 2)],
        "nav": [1.0],
        "close": [1.0],
    }))
    upsert_fund_nav(conn, pd.DataFrame({
        "fund_code": ["510300"],
        "trade_date": [date(2024, 1, 2)],
        "nav": [1.1],
        "close": [1.1],
    }))
    close = conn.execute("SELECT close FROM fund_nav WHERE fund_code = '510300'").fetchone()[0]
    assert close == pytest.approx(1.1)


def test_default_watchlist_has_index_slots_without_hardcoded_fund_codes():
    watchlist = get_watchlist(DEFAULT_CONFIG["index_funds"])
    assert {item.tracking_index for item in watchlist} >= {"000300", "000905"}
    assert all(item.fund_code == "" for item in watchlist[:2])


def test_compute_max_drawdown():
    assert compute_max_drawdown(pd.Series([1.0, 1.2, 0.9, 1.1])) == pytest.approx(-0.25)
