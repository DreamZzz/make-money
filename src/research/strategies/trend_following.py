"""
趋势跟踪策略 — 基于双均线 + 通道突破信号。
A股/港股通用，日频/周频可配置。
"""
import math

import numpy as np
import pandas as pd

_SCORE_NORM = math.log1p(20)  # 20% MA 偏离度 = 满分 1.0


def _compute_atr(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, period: int) -> pd.DataFrame:
    """向量化 ATR：对所有股票同时计算"""
    prev_close = close.shift(1)
    tr = pd.DataFrame(
        np.maximum(
            np.maximum((high.values - low.values), np.abs(high.values - prev_close.values)),
            np.abs(low.values - prev_close.values),
        ),
        index=close.index,
        columns=close.columns,
    )
    return tr.rolling(period).mean()


def compute_signals(
    prices: pd.DataFrame,
    highs: pd.DataFrame = None,
    lows: pd.DataFrame = None,
    fast_ma: int = 20,
    slow_ma: int = 60,
    channel_period: int = 20,
    atr_period: int = 14,
    atr_stop: float = 2.0,
) -> pd.DataFrame:
    """
    生成趋势跟踪信号（向量化实现，不再逐日循环）。

    Args:
        prices: 收盘价宽表，index=trade_date, columns=symbol
        highs:  最高价宽表，结构同 prices；None 时退化为 close 近似
        lows:   最低价宽表，结构同 prices；None 时退化为 close 近似

    Returns:
        信号 DataFrame: symbol, trade_date, side, score, stop_loss_price, ...
    """
    if prices.empty:
        return pd.DataFrame()

    fast     = prices.rolling(fast_ma).mean()
    slow     = prices.rolling(slow_ma).mean()
    upper    = prices.rolling(channel_period).max().shift(1)
    lower    = prices.rolling(channel_period).min().shift(1)

    if highs is not None and lows is not None:
        h = highs.reindex(columns=prices.columns)
        low_prices = lows.reindex(columns=prices.columns)
        atr = _compute_atr(prices, h, low_prices, atr_period)
    else:
        atr = prices.diff().abs().rolling(atr_period).mean()

    score_raw   = (fast - slow).abs() / slow.replace(0, np.nan) * 100
    stop_loss   = prices - atr_stop * atr

    # 预热期后才有效信号
    valid_from = prices.index[slow_ma - 1] if len(prices) >= slow_ma else None
    if valid_from is None:
        return pd.DataFrame()

    buy_mask  = (fast > slow) & (prices > upper)
    sell_mask = (fast < slow) & (prices < lower)

    # 截断预热期
    buy_mask  = buy_mask.loc[valid_from:]
    sell_mask = sell_mask.loc[valid_from:]

    records = []
    for mask, side in [(buy_mask, "BUY"), (sell_mask, "SELL")]:
        stacked = mask.stack()
        stacked = stacked[stacked]
        if stacked.empty:
            continue
        idx = stacked.index  # MultiIndex (trade_date, symbol)
        df_part = idx.to_frame(index=False)
        df_part.columns = ["trade_date", "symbol"]
        df_part["side"] = side
        df_part["score"] = np.clip(
            np.log1p(score_raw.stack().reindex(idx).values) / _SCORE_NORM, 0.0, 1.0
        )
        if side == "BUY":
            sl_vals = stop_loss.stack().reindex(idx).values
            price_vals = prices.stack().reindex(idx).values
            df_part["stop_loss_price"] = np.where(sl_vals > 0, sl_vals, price_vals * 0.95)
        else:
            df_part["stop_loss_price"] = None
        records.append(df_part)

    if not records:
        return pd.DataFrame()

    df = pd.concat(records, ignore_index=True)
    df["model_name"]           = "trend_following"
    df["horizon"]              = "20d"
    df["confidence"]           = np.clip(df["score"], 0.3, 0.95)
    df["expected_holding_days"] = 20
    df["max_position_pct"]     = (0.04 + 0.08 * df["score"]).clip(0.04, 0.12).round(2)
    df["thesis"] = (
        f"趋势跟踪: 快线({fast_ma}d) vs 慢线({slow_ma}d), 突破{channel_period}d通道"
    )
    df["risk_tags"] = [["trend", "momentum"]] * len(df)
    return df
