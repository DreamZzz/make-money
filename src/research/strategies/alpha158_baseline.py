"""
Alpha158 多因子基线策略。
基于 Qlib Alpha158 因子集 + LightGBM 排序选股，向量化实现。
"""
import numpy as np
import pandas as pd
from loguru import logger


def _holding_symbols(current_holdings) -> set[str]:
    if current_holdings is None:
        return set()
    if isinstance(current_holdings, pd.DataFrame):
        if current_holdings.empty:
            return set()
        symbol_col = "symbol" if "symbol" in current_holdings.columns else "instrument"
        if symbol_col not in current_holdings.columns:
            return set()
        holdings = current_holdings.copy()
        if "quantity" in holdings.columns:
            holdings = holdings[pd.to_numeric(holdings["quantity"], errors="coerce").fillna(0) > 0]
        return set(holdings[symbol_col].dropna().astype(str))
    if isinstance(current_holdings, dict):
        symbols: set[str] = set()
        for symbol, quantity in current_holdings.items():
            try:
                if float(quantity or 0) > 0:
                    symbols.add(str(symbol))
            except Exception:
                continue
        return symbols
    if isinstance(current_holdings, str):
        return {current_holdings}
    try:
        return set(pd.Series(list(current_holdings)).dropna().astype(str))
    except Exception:
        return set()


def generate_signals(
    predictions: pd.DataFrame,
    top_n: int = 50,
    current_holdings=None,
    exit_rank_multiplier: float = 2.0,
    expected_holding_days: int = 5,
) -> pd.DataFrame:
    """
    将 Qlib 预测结果转换为系统标准信号格式。

    Args:
        predictions: Qlib 预测 DataFrame，columns=[datetime, instrument, score]
        top_n:       每个截面最多取多少只买入标的
        current_holdings:
            当前 Alpha158 持仓。可传 symbol 列表、{symbol: quantity} 或含
            symbol/quantity 的 DataFrame；跌出退出阈值的持仓生成 SELL。
        exit_rank_multiplier:
            退出阈值倍数。默认 Top-N 买入、跌出 Top-2N 才卖出，避免模型
            分数小幅波动导致过度换手；设为 1.0 时跌出 Top-N 即卖出。
        expected_holding_days: 信号预期持仓天数

    Returns:
        标准信号 DataFrame
    """
    if predictions.empty or "datetime" not in predictions.columns:
        return pd.DataFrame()

    predictions = predictions.copy()
    predictions["datetime"] = pd.to_datetime(predictions["datetime"])

    # 只取最新截面
    latest_dt = predictions["datetime"].max()
    latest = predictions[predictions["datetime"] == latest_dt].copy()

    if latest.empty:
        return pd.DataFrame()

    top_n = max(int(top_n or 0), 1)
    expected_holding_days = max(int(expected_holding_days or 1), 1)
    latest = latest.sort_values("score", ascending=False).reset_index(drop=True)
    latest["rank"] = np.arange(1, len(latest) + 1)

    # 归一化 score → confidence [0, 1]
    s_min, s_max = latest["score"].min(), latest["score"].max()
    if s_max > s_min:
        latest["confidence"] = (latest["score"] - s_min) / (s_max - s_min)
    else:
        latest["confidence"] = 0.5

    # 取 top_n
    buy = latest.head(top_n).copy()
    frames = []
    if not buy.empty:
        frames.append(pd.DataFrame({
        "model_name":            "alpha158",
        "model_version":         "1.0",
        "symbol":                buy["instrument"].values,
        "signal_ts":             latest_dt,
        "trade_date":            latest_dt,
        "horizon":               "5d",
        "score":                 np.clip(buy["confidence"].values, 0.0, 1.0),
        "side":                  "BUY",
        "confidence":            np.clip(buy["confidence"].values, 0.0, 1.0),
        "expected_holding_days": expected_holding_days,
        "max_position_pct":      0.05,
        "thesis":                "Alpha158 raw score: " + buy["score"].round(4).astype(str).values,
        "risk_tags":             [["multi_factor"]] * len(buy),
        }))

    held_symbols = _holding_symbols(current_holdings)
    if held_symbols:
        exit_rank = max(top_n, int(np.ceil(top_n * max(float(exit_rank_multiplier or 1.0), 1.0))))
        sell = latest[
            latest["instrument"].astype(str).isin(held_symbols)
            & (latest["rank"] > exit_rank)
        ].copy()
        if not sell.empty:
            sell_conf = np.clip(1.0 - sell["confidence"].values, 0.6, 1.0)
            frames.append(pd.DataFrame({
                "model_name":            "alpha158",
                "model_version":         "1.0",
                "symbol":                sell["instrument"].values,
                "signal_ts":             latest_dt,
                "trade_date":            latest_dt,
                "horizon":               "exit",
                "score":                 sell_conf,
                "side":                  "SELL",
                "confidence":            sell_conf,
                "expected_holding_days": 1,
                "max_position_pct":      0.0,
                "thesis": [
                    f"Alpha158 exit: rank {int(rank)} outside Top{exit_rank}; score {score:.4f}"
                    for rank, score in zip(sell["rank"], sell["score"])
                ],
                "risk_tags":             [["multi_factor", "rank_exit"]] * len(sell),
            }))

    signals = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if signals.empty:
        return signals

    logger.info(
        f"Alpha158 生成 {len(signals)} 条信号 "
        f"（BUY={(signals['side'] == 'BUY').sum()}, SELL={(signals['side'] == 'SELL').sum()}, "
        f"截面日期: {latest_dt.date()}）"
    )
    return signals


def evaluate_factors(factor_df: pd.DataFrame, returns_forward: pd.DataFrame) -> pd.DataFrame:
    """
    因子评估：计算各因子的 IC（信息系数）。

    Args:
        factor_df:        因子值 DataFrame，index=(trade_date, symbol)，columns=factors
        returns_forward:  前瞻收益 Series，index=(trade_date, symbol)

    Returns:
        按 |IC| 降序排列的因子评估 DataFrame
    """
    if factor_df.empty or returns_forward.empty:
        return pd.DataFrame()

    common_idx = factor_df.index.intersection(returns_forward.index)
    if common_idx.empty:
        return pd.DataFrame()

    fac = factor_df.loc[common_idx]
    ret = returns_forward.loc[common_idx]

    # 向量化 IC 计算
    valid_cols = [c for c in fac.columns if c not in ("trade_date", "symbol")]
    ic_vals = fac[valid_cols].corrwith(ret, method="pearson")

    return (
        ic_vals.rename("ic")
        .reset_index()
        .rename(columns={"index": "factor"})
        .assign(abs_ic=lambda d: d["ic"].abs())
        .sort_values("abs_ic", ascending=False)
        .drop(columns="abs_ic")
        .reset_index(drop=True)
    )
