"""Research-only industry-neutral cross-sectional reversal factor."""
from __future__ import annotations

import numpy as np
import pandas as pd

MODEL_NAME = "cross_reversal"


def compute_cross_reversal_scores(
    prices: pd.DataFrame,
    lookback: int = 20,
    smooth_days: int = 1,
    size_neutral: bool = False,
    beta_neutral: bool = False,
    beta_lookback: int = 120,
) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "industry",
                "lookback_return",
                "smoothed_reversal",
                "residual_reversal",
                "score",
            ]
        )

    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["industry"] = df["industry"].fillna("unknown").astype(str)
    df = df.sort_values(["symbol", "trade_date"])
    df["lookback_return"] = df.groupby("symbol")["close"].pct_change(periods=lookback)
    if beta_neutral:
        df = _add_rolling_beta(df, beta_lookback=beta_lookback)
    df["raw_reversal"] = -df["lookback_return"]
    smooth_window = max(1, int(smooth_days))
    df["smoothed_reversal"] = (
        df.groupby("symbol")["raw_reversal"]
        .rolling(smooth_window, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["residual_reversal"] = (
        _neutral_residual(df, size_neutral=size_neutral, beta_neutral=beta_neutral)
        if (size_neutral or beta_neutral) else df["smoothed_reversal"]
    )
    if "market_cap" in df.columns and "log_market_cap" not in df.columns:
        df["log_market_cap"] = np.log(pd.to_numeric(df["market_cap"], errors="coerce"))
    df["score"] = df.groupby(["trade_date", "industry"])["residual_reversal"].rank(
        pct=True,
        ascending=True,
    )
    columns = [
        "trade_date",
        "symbol",
        "industry",
        "lookback_return",
        "raw_reversal",
        "smoothed_reversal",
        "residual_reversal",
        "score",
    ]
    if "market_cap" in df.columns:
        columns.insert(-1, "log_market_cap")
    if "rolling_beta" in df.columns:
        columns.insert(-1, "rolling_beta")
    return df[columns].dropna(subset=["score"])


def _add_rolling_beta(df: pd.DataFrame, beta_lookback: int) -> pd.DataFrame:
    working = df.copy()
    working["stock_return"] = working.groupby("symbol")["close"].pct_change()
    market_return = working.groupby("trade_date")["stock_return"].mean().rename("market_return")
    working = working.merge(market_return, on="trade_date", how="left")
    min_periods = max(2, min(int(beta_lookback), 20))
    beta_values = []
    for _, group in working.groupby("symbol", sort=False):
        cov = group["stock_return"].rolling(int(beta_lookback), min_periods=min_periods).cov(group["market_return"])
        var = group["market_return"].rolling(int(beta_lookback), min_periods=min_periods).var()
        beta_values.append(cov / var.replace(0, np.nan))
    working["rolling_beta"] = pd.concat(beta_values).sort_index()
    return working.drop(columns=["stock_return", "market_return"])


def _neutral_residual(
    df: pd.DataFrame,
    *,
    size_neutral: bool,
    beta_neutral: bool,
) -> pd.Series:
    working = df.copy()
    factor_cols = []
    if size_neutral and "market_cap" in working.columns:
        working["log_market_cap"] = np.log(pd.to_numeric(working["market_cap"], errors="coerce"))
        factor_cols.append("log_market_cap")
    if beta_neutral and "rolling_beta" in working.columns:
        factor_cols.append("rolling_beta")
    if not factor_cols:
        return working["smoothed_reversal"]
    residual = pd.Series(index=working.index, dtype=float)
    for _, group in working.groupby("trade_date", sort=False):
        valid = group[["smoothed_reversal", *factor_cols]].dropna()
        usable_factors = [col for col in factor_cols if valid[col].nunique() >= 2]
        if len(valid) <= len(usable_factors) + 1 or not usable_factors:
            residual.loc[group.index] = group["smoothed_reversal"]
            continue
        x = valid[usable_factors].to_numpy(dtype=float)
        y = valid["smoothed_reversal"].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(x)), x])
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        residual.loc[valid.index] = y - design @ coef
        missing = group.index.difference(valid.index)
        residual.loc[missing] = group.loc[missing, "smoothed_reversal"]
    for col in factor_cols:
        df[col] = working[col]
    return residual
