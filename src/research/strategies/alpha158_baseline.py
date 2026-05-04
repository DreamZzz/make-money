"""
Alpha158 多因子基线策略。
基于 Qlib Alpha158 因子集 + LightGBM 排序选股。
"""
import pandas as pd
import numpy as np
from loguru import logger


def generate_signals(predictions: pd.DataFrame, top_n: int = 50, min_confidence: float = 0.6) -> pd.DataFrame:
    """
    根据 Qlib 模型预测生成调仓信号。

    Args:
        predictions: Qlib 预测结果 DataFrame, columns=[datetime, instrument, score]
        top_n: 买入数量
        min_confidence: 最低置信度（基于 score 标准化后的分位数）

    Returns:
        信号 DataFrame
    """
    if predictions.empty:
        return pd.DataFrame()

    df = predictions.copy()
    if "datetime" not in df.columns:
        logger.warning("predictions missing 'datetime' column")
        return pd.DataFrame()

    # 每个时间截面按 score 排序
    latest = df["datetime"].max()
    latest_df = df[df["datetime"] == latest].copy()

    # score 标准化为 [0, 1] 置信度
    if latest_df["score"].std() > 0:
        latest_df["confidence"] = (latest_df["score"] - latest_df["score"].min()) / (
            latest_df["score"].max() - latest_df["score"].min()
        )
    else:
        latest_df["confidence"] = 0.5

    # 选 top_n
    buy_candidates = latest_df.nlargest(top_n, "score")
    buy_candidates = buy_candidates[buy_candidates["confidence"] >= min_confidence]

    signals = []
    for _, row in buy_candidates.iterrows():
        signals.append({
            "model_name": "alpha158",
            "model_version": "1.0",
            "symbol": row.get("instrument", ""),
            "signal_ts": pd.Timestamp.now(),
            "horizon": "5d",
            "score": row["score"],
            "side": "BUY",
            "confidence": row["confidence"],
            "expected_holding_days": 5,
            "max_position_pct": 0.05,
            "thesis": f"Alpha158 factor score: {row['score']:.3f}",
            "risk_tags": ["multi_factor"],
        })

    return pd.DataFrame(signals)


def evaluate_factors(factor_df: pd.DataFrame, returns_forward: pd.DataFrame) -> pd.DataFrame:
    """
    因子评估：计算 IC Rank、IC Mean、IR。
    """
    if factor_df.empty or returns_forward.empty:
        return pd.DataFrame()

    common_idx = factor_df.index.intersection(returns_forward.index)
    factor_df = factor_df.loc[common_idx]
    returns_forward = returns_forward.loc[common_idx]

    ic_results = []
    for col in factor_df.columns:
        if col in ["trade_date", "symbol"]:
            continue
        valid = factor_df[col].notna() & returns_forward.notna()
        if valid.sum() < 10:
            continue
        ic = factor_df.loc[valid, col].corr(returns_forward.loc[valid])
        ic_results.append({"factor": col, "ic": ic})

    return pd.DataFrame(ic_results).sort_values("ic", key=abs, ascending=False)
