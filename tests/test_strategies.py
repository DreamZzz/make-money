"""核心策略模块单元测试"""

import numpy as np
import pandas as pd
import pytest

from src.config import _deep_merge
from src.portfolio.risk_rules import RiskLimits, check_drawdown, check_position_limits
from src.research.strategies.mean_reversion import (
    _apply_min_days_between,
    compute_bollinger,
    compute_rsi,
    generate_signals,
)
from src.research.strategies.trend_following import compute_signals as trend_compute_signals
from src.signals.generator import apply_filters

# ─── 辅助数据 ───────────────────────────────────────────────────────────────

def make_price_df(n: int = 120, n_stocks: int = 5, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n)
    data = 100 * np.cumprod(1 + rng.normal(0, 0.01, (n, n_stocks)), axis=0)
    return pd.DataFrame(data, index=dates, columns=[f"S{i:03d}" for i in range(n_stocks)])


# ─── compute_rsi ────────────────────────────────────────────────────────────

def test_rsi_range():
    prices = make_price_df()
    rsi = compute_rsi(prices, period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all().all()
    assert (valid <= 100).all().all()


def test_rsi_trending_up_high():
    """以小幅随机噪声为主的强上涨序列，RSI 应持续高于中线 50"""
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2023-01-01", periods=60)
    trend = np.linspace(100, 160, 60) + rng.normal(0, 0.3, 60)
    s = pd.DataFrame({"X": trend}, index=dates)
    rsi = compute_rsi(s, period=14)
    valid = rsi["X"].dropna()
    assert len(valid) > 0
    assert valid.iloc[-1] > 50


# ─── compute_bollinger ──────────────────────────────────────────────────────

def test_bollinger_band_order():
    prices = make_price_df()
    upper, middle, lower = compute_bollinger(prices, period=20)
    # 去掉预热期 NaN 行后比较
    idx = upper.dropna().index
    assert (upper.loc[idx].values >= middle.loc[idx].values).all()
    assert (middle.loc[idx].values >= lower.loc[idx].values).all()


# ─── _apply_min_days_between ────────────────────────────────────────────────

def test_min_days_between_enforced():
    dates = pd.bdate_range("2023-01-01", periods=20)
    df = pd.DataFrame({
        "trade_date": dates,
        "symbol": "S001",
        "side": "BUY",
        "score": 0.5,
    })
    result = _apply_min_days_between(df, min_days=5)
    diffs = result["trade_date"].diff().dropna().dt.days
    assert (diffs >= 5).all()


def test_min_days_between_zero_keeps_all():
    df = pd.DataFrame({
        "trade_date": pd.bdate_range("2023-01-01", periods=10),
        "symbol": "S001",
        "side": "BUY",
        "score": 0.5,
    })
    result = _apply_min_days_between(df, min_days=0)
    assert len(result) == len(df)


# ─── generate_signals (mean_reversion) ─────────────────────────────────────

def test_mean_reversion_returns_dataframe():
    prices = make_price_df(n=200)
    result = generate_signals(prices)
    assert isinstance(result, pd.DataFrame)


def test_mean_reversion_columns():
    prices = make_price_df(n=200)
    result = generate_signals(prices)
    if not result.empty:
        for col in ["symbol", "side", "score", "confidence", "model_name"]:
            assert col in result.columns


def test_mean_reversion_side_values():
    prices = make_price_df(n=200)
    result = generate_signals(prices)
    if not result.empty:
        assert result["side"].isin(["BUY", "SELL"]).all()


# ─── compute_signals (trend_following) ──────────────────────────────────────

def test_trend_following_returns_dataframe():
    prices = make_price_df(n=200)
    result = trend_compute_signals(prices)
    assert isinstance(result, pd.DataFrame)


def test_trend_following_with_highs_lows():
    prices = make_price_df(n=200)
    highs = prices * 1.01
    lows  = prices * 0.99
    result = trend_compute_signals(prices, highs=highs, lows=lows)
    assert isinstance(result, pd.DataFrame)


def test_trend_following_short_data_returns_empty():
    prices = make_price_df(n=10)
    result = trend_compute_signals(prices)
    assert result.empty


# ─── check_drawdown ─────────────────────────────────────────────────────────

def test_check_drawdown_no_breach():
    nav = pd.Series([1.0, 1.05, 1.10, 1.08])
    triggered, dd = check_drawdown(nav, max_dd=0.20)
    assert not triggered
    assert dd < 0


def test_check_drawdown_breach():
    nav = pd.Series([1.0, 1.2, 1.2, 0.9])  # 回撤 25%
    triggered, dd = check_drawdown(nav, max_dd=0.20)
    assert triggered
    assert dd < -0.20


def test_check_drawdown_empty():
    triggered, dd = check_drawdown(pd.Series([], dtype=float))
    assert not triggered
    assert dd == 0.0


# ─── check_position_limits ──────────────────────────────────────────────────

def test_single_position_capped():
    positions = pd.DataFrame({
        "symbol":      ["A", "B"],
        "order_value": [500.0, 100.0],
    })
    limits = RiskLimits(max_single_position_pct=0.30)
    result = check_position_limits(positions, total_value=600.0, limits=limits)
    assert result.loc[result["symbol"] == "A", "order_value"].iloc[0] == pytest.approx(180.0)
    assert result.loc[result["symbol"] == "A", "limit_breach"].iloc[0]


def test_industry_limit_capped():
    positions = pd.DataFrame({
        "symbol":      ["A", "B", "C"],
        "order_value": [200.0, 200.0, 100.0],
        "industry":    ["银行", "银行", "科技"],
    })
    limits = RiskLimits(max_single_position_pct=1.0, max_industry_pct=0.30)
    result = check_position_limits(positions, total_value=500.0, limits=limits)
    bank = result[result["industry"] == "银行"]["order_value"].sum()
    assert bank <= 500.0 * 0.30 + 1e-6


def test_blacklist_filtered():
    positions = pd.DataFrame({
        "symbol":      ["A", "B", "C"],
        "order_value": [100.0, 100.0, 100.0],
    })
    limits = RiskLimits(blacklist=["B"])
    result = check_position_limits(positions, total_value=300.0, limits=limits)
    assert "B" not in result["symbol"].values


# ─── apply_filters ──────────────────────────────────────────────────────────

def test_apply_filters_confidence_threshold():
    df = pd.DataFrame({
        "symbol":     ["A", "B", "C"],
        "side":       ["BUY", "BUY", "SELL"],
        "confidence": [0.8, 0.5, 0.7],
        "score":      [0.9, 0.6, 0.8],
    })
    result = apply_filters(df, min_confidence=0.6)
    assert 0.5 not in result["confidence"].values
    assert len(result) == 2


def test_apply_filters_dedup_keeps_highest_score():
    df = pd.DataFrame({
        "symbol":     ["A", "A"],
        "side":       ["BUY", "BUY"],
        "confidence": [0.8, 0.7],
        "score":      [0.9, 0.6],
    })
    result = apply_filters(df, min_confidence=0.6)
    assert len(result) == 1
    assert result.iloc[0]["score"] == pytest.approx(0.9)


# ─── _deep_merge (config) ───────────────────────────────────────────────────

def test_deep_merge_partial_override():
    base     = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"x": 99}}
    result   = _deep_merge(base, override)
    assert result["a"]["x"] == 99
    assert result["a"]["y"] == 2   # 保留默认值
    assert result["b"] == 3


def test_deep_merge_new_key():
    base     = {"a": 1}
    override = {"b": 2}
    result   = _deep_merge(base, override)
    assert result == {"a": 1, "b": 2}
