from __future__ import annotations

import math
from typing import Any

import pandas as pd

MODEL_NAME = "market_regime"

OUTPUT_COLUMNS = [
    "trade_date",
    "index_code",
    "close",
    "fast_ma",
    "slow_ma",
    "return_1d",
    "realized_vol",
    "drawdown",
    "trend_score",
    "risk_state",
    "satellite_scale",
    "reason",
]


def compute_market_regime(
    index_prices: pd.DataFrame,
    benchmark: str = "000300",
    fast_window: int = 20,
    slow_window: int = 120,
    vol_window: int = 20,
    drawdown_window: int = 120,
) -> pd.DataFrame:
    prices = _prepare_prices(index_prices, benchmark)
    if prices.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    prices["fast_ma"] = prices["close"].rolling(fast_window, min_periods=1).mean()
    prices["slow_ma"] = prices["close"].rolling(slow_window, min_periods=1).mean()
    prices["return_1d"] = prices["close"].pct_change()
    prices["realized_vol"] = prices["return_1d"].rolling(vol_window, min_periods=2).std() * math.sqrt(252)

    rolling_high = prices["close"].rolling(drawdown_window, min_periods=1).max()
    prices["drawdown"] = prices["close"] / rolling_high - 1.0
    prices["trend_score"] = _compute_trend_score(prices)

    states = prices.apply(_classify_row, axis=1, result_type="expand")
    prices["risk_state"] = states["risk_state"]
    prices["satellite_scale"] = states["satellite_scale"]
    prices["reason"] = states["reason"]
    return prices[OUTPUT_COLUMNS].reset_index(drop=True)


def latest_market_regime(index_prices: pd.DataFrame, benchmark: str = "000300", **kwargs: Any) -> dict[str, Any]:
    regime = compute_market_regime(index_prices, benchmark=benchmark, **kwargs)
    if regime.empty:
        return {
            "trade_date": pd.NaT,
            "index_code": benchmark,
            "close": math.nan,
            "fast_ma": math.nan,
            "slow_ma": math.nan,
            "return_1d": math.nan,
            "realized_vol": math.nan,
            "drawdown": math.nan,
            "trend_score": math.nan,
            "risk_state": "unknown",
            "satellite_scale": 1.0,
            "reason": "missing usable benchmark close data",
        }
    return regime.iloc[-1].to_dict()


def _prepare_prices(index_prices: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    if index_prices.empty or "close" not in index_prices.columns:
        return pd.DataFrame(columns=["trade_date", "index_code", "close"])

    prices = index_prices.copy()
    if "trade_date" not in prices.columns:
        return pd.DataFrame(columns=["trade_date", "index_code", "close"])
    if "index_code" not in prices.columns:
        prices["index_code"] = benchmark

    prices["index_code"] = prices["index_code"].astype(str)
    codes = prices["index_code"].dropna().unique()
    if benchmark in codes:
        prices = prices[prices["index_code"] == benchmark]
    else:
        return pd.DataFrame(columns=["trade_date", "index_code", "close"])

    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["trade_date", "close"])
    return prices.sort_values(["trade_date", "index_code"])[["trade_date", "index_code", "close"]]


def _compute_trend_score(prices: pd.DataFrame) -> pd.Series:
    score = pd.Series(0.0, index=prices.index)
    score += prices["close"].ge(prices["slow_ma"]).map({True: 0.5, False: -0.5})
    score += prices["fast_ma"].ge(prices["slow_ma"]).map({True: 0.25, False: -0.25})
    score += prices["drawdown"].ge(-0.10).map({True: 0.25, False: -0.25})
    return score


def _classify_row(row: pd.Series) -> pd.Series:
    trend_score = float(row["trend_score"])
    drawdown = float(row["drawdown"])
    return_1d = float(row["return_1d"]) if pd.notna(row["return_1d"]) else 0.0
    realized_vol = float(row["realized_vol"]) if pd.notna(row["realized_vol"]) else 0.0

    if return_1d <= -0.07 or (drawdown <= -0.25 and realized_vol >= 0.35):
        risk_state = "crisis"
        satellite_scale = 0.0
    elif trend_score >= 0.75 and drawdown >= -0.10:
        risk_state = "risk_on"
        satellite_scale = 1.10
    elif trend_score >= 0.0 and drawdown >= -0.18:
        risk_state = "neutral"
        satellite_scale = 1.00
    elif drawdown >= -0.25:
        risk_state = "defensive"
        satellite_scale = 0.70
    else:
        risk_state = "risk_off"
        satellite_scale = 0.40

    return pd.Series(
        {
            "risk_state": risk_state,
            "satellite_scale": satellite_scale,
            "reason": (
                f"{risk_state}: trend_score={trend_score:.2f}, "
                f"drawdown={drawdown:.2%}, return_1d={return_1d:.2%}, "
                f"realized_vol={realized_vol:.2%}"
            ),
        }
    )
