"""
组合优化器 — 仓位计算、权重优化。
支持等权重、波动率倒数加权、均值方差优化。
"""
import numpy as np
import pandas as pd
from loguru import logger


def equal_weight(signals_buy: pd.DataFrame) -> dict[str, float]:
    """等权重分配"""
    if signals_buy.empty:
        return {}
    n = len(signals_buy)
    weight = 1.0 / n
    return {row["symbol"]: weight for _, row in signals_buy.iterrows()}


def inverse_volatility_weight(returns: pd.DataFrame, signals_buy: pd.DataFrame,
                               lookback: int = 60) -> dict[str, float]:
    """
    波动率倒数加权：低波动的股票配更高权重。
    """
    if signals_buy.empty or returns.empty:
        return {}

    symbols = signals_buy["symbol"].tolist()
    available = [s for s in symbols if s in returns.columns]
    if not available:
        return {}

    vols = returns[available].iloc[-lookback:].std()
    inv_vols = 1.0 / vols.replace(0, np.nan)
    total = inv_vols.sum()
    if total == 0:
        return equal_weight(signals_buy)

    return {s: inv_vols[s] / total for s in available}


def mean_variance_optimize(returns: pd.DataFrame, signals_buy: pd.DataFrame,
                            risk_aversion: float = 1.0, max_weight: float = 0.10) -> dict[str, float]:
    """
    均值方差优化（PyPortfolioOpt）。
    """
    try:
        from pypfopt.expected_returns import mean_historical_return
        from pypfopt.risk_models import sample_cov
        from pypfopt.efficient_frontier import EfficientFrontier
        from pypfopt.objective import objective_functions

        symbols = signals_buy["symbol"].tolist()
        available = [s for s in symbols if s in returns.columns]
        if len(available) < 3:
            logger.warning("Too few stocks for MV optimization, using equal weight")
            return equal_weight(signals_buy)

        rets = returns[available].iloc[-252:]  # 一年日数据

        mu = mean_historical_return(rets, frequency=252)
        S = sample_cov(rets, frequency=252)

        ef = EfficientFrontier(mu, S)
        ef.add_constraint(lambda w: w <= max_weight)

        # L2 正则化防止极端权重
        ef.add_objective(objective_functions.L2_reg, gamma=0.1)

        weights = ef.max_sharpe(risk_free_rate=0.03)
        ef.clean_weights()

        return {s: weights[s] for s in available if weights[s] > 0.001}

    except ImportError:
        logger.warning("PyPortfolioOpt not installed, using equal weight")
        return equal_weight(signals_buy)
    except Exception as e:
        logger.error(f"MV optimization failed: {e}, using equal weight")
        return equal_weight(signals_buy)


def compute_target_positions(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
) -> pd.DataFrame:
    """
    计算从当前持仓权重调整到目标权重所需的操作建议。

    研究平台定位：输出权重百分比，由使用者根据实际资金换算手数。

    Args:
        current_weights: 当前各标的权重 {symbol: weight}，不持有则不传或传 0
        target_weights:  目标各标的权重 {symbol: weight}，总和应 ≤ 1

    Returns:
        调仓建议 DataFrame: symbol, current_weight, target_weight, delta_weight, action
    """
    all_symbols = set(current_weights) | set(target_weights)
    rows = []
    for symbol in sorted(all_symbols):
        cur = current_weights.get(symbol, 0.0)
        tgt = target_weights.get(symbol, 0.0)
        delta = tgt - cur
        if abs(delta) < 1e-4:
            continue
        rows.append({
            "symbol":         symbol,
            "current_weight": round(cur, 4),
            "target_weight":  round(tgt, 4),
            "delta_weight":   round(delta, 4),
            "action":         "买入" if delta > 0 else "减仓" if tgt > 0 else "清仓",
        })
    return pd.DataFrame(rows)


def apply_risk_rules(
    positions: pd.DataFrame,
    max_single_pct: float = 0.10,
    max_industry_pct: float = 0.30,
    industry_map: dict[str, str] = None,
) -> pd.DataFrame:
    """委托 risk_rules.check_position_limits 执行风控，避免重复逻辑"""
    from src.portfolio.risk_rules import RiskLimits, check_position_limits

    if positions.empty:
        return positions

    if industry_map and "industry" not in positions.columns:
        positions = positions.copy()
        positions["industry"] = positions["symbol"].map(industry_map)

    limits = RiskLimits(
        max_single_position_pct=max_single_pct,
        max_industry_pct=max_industry_pct,
    )
    total_value = positions["order_value"].sum()
    return check_position_limits(positions, total_value, limits)
