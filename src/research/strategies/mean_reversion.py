"""
均值回归策略 — 基于布林带 + RSI 超买超卖信号。
短期反转：买入超卖、卖出超买。
"""
import numpy as np
import pandas as pd


def compute_rsi(close: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """向量化 RSI，支持宽表 DataFrame；loss=0 时 RSI=100（无下跌期）"""
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    # loss=0 且不是预热期 NaN → RSI 应为 100
    all_gains = (loss == 0) & loss.notna()
    return rsi.where(~all_gains, 100.0)


def compute_bollinger(close: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
    """向量化布林带，返回 (upper, middle, lower) 三个 DataFrame"""
    middle = close.rolling(period).mean()
    std    = close.rolling(period).std()
    return middle + std_dev * std, middle, middle - std_dev * std


def _apply_min_days_between(df: pd.DataFrame, min_days: int) -> pd.DataFrame:
    """对每只股票每个方向，去掉距前一条信号不足 min_days 天的信号"""
    if df.empty or min_days <= 0:
        return df

    result = []
    for _, group in df.groupby(["symbol", "side"]):
        group = group.sort_values("trade_date").reset_index(drop=True)
        keep      = [True]
        last_date = group["trade_date"].iloc[0]
        for i in range(1, len(group)):
            curr = group["trade_date"].iloc[i]
            if (curr - last_date).days >= min_days:
                keep.append(True)
                last_date = curr
            else:
                keep.append(False)
        result.append(group[keep])

    return pd.concat(result, ignore_index=True) if result else pd.DataFrame()


def generate_signals(
    prices: pd.DataFrame,
    rsi_period: int = 14,
    rsi_oversold: float = 30,
    rsi_overbought: float = 70,
    bb_period: int = 20,
    bb_std: float = 2.0,
    min_days_between: int = 5,
) -> pd.DataFrame:
    """
    生成均值回归信号（向量化实现）。

    BUY:  RSI < oversold AND close < lower_band  → 超卖反弹
    SELL: RSI > overbought AND close > upper_band → 超买回调
    """
    if prices.empty:
        return pd.DataFrame()

    warmup = max(rsi_period, bb_period) + 5
    if len(prices) < warmup:
        return pd.DataFrame()

    rsi              = compute_rsi(prices, rsi_period)
    upper, middle, lower = compute_bollinger(prices, bb_period, bb_std)

    buy_mask  = (rsi < rsi_oversold)  & (prices < lower)
    sell_mask = (rsi > rsi_overbought) & (prices > upper)

    valid_from = prices.index[warmup - 1]
    buy_mask   = buy_mask.loc[valid_from:]
    sell_mask  = sell_mask.loc[valid_from:]

    records = []
    for mask, side in [(buy_mask, "BUY"), (sell_mask, "SELL")]:
        stacked = mask.stack()
        stacked = stacked[stacked]
        if stacked.empty:
            continue
        idx = stacked.index
        df_part = idx.to_frame(index=False)
        df_part.columns = ["trade_date", "symbol"]
        df_part["side"] = side

        rsi_vals = rsi.stack().reindex(idx).values
        bb_upper = upper.stack().reindex(idx).values
        bb_lower = lower.stack().reindex(idx).values
        price_vals = prices.stack().reindex(idx).values

        if side == "BUY":
            df_part["score"] = np.clip((rsi_oversold - rsi_vals) / rsi_oversold, 0, 1)
        else:
            df_part["score"] = np.clip((rsi_vals - rsi_overbought) / (100 - rsi_overbought), 0, 1)

        df_part["rsi"]         = rsi_vals
        df_part["bb_position"] = np.where(
            bb_upper != bb_lower,
            (price_vals - bb_lower) / (bb_upper - bb_lower),
            0.5,
        )
        records.append(df_part)

    if not records:
        return pd.DataFrame()

    df = pd.concat(records, ignore_index=True)
    df = _apply_min_days_between(df, min_days_between)

    if df.empty:
        return pd.DataFrame()

    df["model_name"]            = "mean_reversion"
    df["horizon"]               = "5d"
    df["confidence"]            = np.clip(df["score"] * 1.5, 0.25, 0.85)
    df["expected_holding_days"] = 5
    df["max_position_pct"]      = 0.05
    df["thesis"] = df.apply(
        lambda r: f"均值回归: RSI={r['rsi']:.1f}, BB位置={r['bb_position']:.2f}", axis=1
    )
    df["risk_tags"] = [["mean_rev", "short_term"]] * len(df)
    return df
