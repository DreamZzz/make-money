"""Research-only low-volatility factor."""
from __future__ import annotations

import pandas as pd

MODEL_NAME = "low_vol"


def compute_low_vol_scores(prices: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(
            columns=["trade_date", "symbol", "realized_vol", "liquidity", "score"]
        )

    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["symbol", "trade_date"])
    min_periods = max(2, min(lookback, 20))

    df["return"] = df.groupby("symbol")["close"].pct_change()
    df["realized_vol"] = (
        df.groupby("symbol")["return"]
        .rolling(lookback, min_periods=min_periods)
        .std()
        .reset_index(level=0, drop=True)
    )
    df["liquidity"] = (
        df.groupby("symbol")["amount"]
        .rolling(lookback, min_periods=min_periods)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["vol_rank"] = df.groupby("trade_date")["realized_vol"].rank(
        pct=True,
        ascending=True,
    )
    df["liquidity_rank"] = df.groupby("trade_date")["liquidity"].rank(
        pct=True,
        ascending=True,
    )
    df["score"] = 0.85 * (1.0 - df["vol_rank"]) + 0.15 * df["liquidity_rank"]
    return df[["trade_date", "symbol", "realized_vol", "liquidity", "score"]].dropna(
        subset=["score"]
    )
