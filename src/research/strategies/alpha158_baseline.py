"""
Alpha158 多因子基线策略。
基于 Qlib Alpha158 因子集 + LightGBM 排序选股，向量化实现。
"""
import numpy as np
import pandas as pd
from loguru import logger


def generate_signals(predictions: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """
    将 Qlib 预测结果转换为系统标准信号格式。

    Args:
        predictions: Qlib 预测 DataFrame，columns=[datetime, instrument, score]
        top_n:       每个截面最多取多少只买入标的

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

    # 归一化 score → confidence [0, 1]
    s_min, s_max = latest["score"].min(), latest["score"].max()
    if s_max > s_min:
        latest["confidence"] = (latest["score"] - s_min) / (s_max - s_min)
    else:
        latest["confidence"] = 0.5

    # 取 top_n
    buy = latest.nlargest(top_n, "score").copy()

    if buy.empty:
        return pd.DataFrame()

    signals = pd.DataFrame({
        "model_name":            "alpha158",
        "model_version":         "1.0",
        "symbol":                buy["instrument"].values,
        "signal_ts":             pd.Timestamp.now(),
        "trade_date":            latest_dt,
        "horizon":               "5d",
        "score":                 buy["score"].values,
        "side":                  "BUY",
        "confidence":            np.clip(buy["confidence"].values, 0.0, 1.0),
        "expected_holding_days": 5,
        "max_position_pct":      0.05,
        "thesis":                "Alpha158 factor score: " + buy["score"].round(4).astype(str).values,
        "risk_tags":             [["multi_factor"]] * len(buy),
    })

    logger.info(f"Alpha158 生成 {len(signals)} 条信号（截面日期: {latest_dt.date()}）")
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
