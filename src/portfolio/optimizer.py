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
    current_holdings: pd.DataFrame,
    target_weights: dict[str, float],
    total_capital: float,
    prices: dict[str, float],
) -> pd.DataFrame:
    """
    根据目标权重计算需要调整的仓位。

    Args:
        current_holdings: 当前持仓, columns=[symbol, quantity, avg_cost]
        target_weights: {symbol: weight}
        total_capital: 总资金
        prices: {symbol: 当前价格}

    Returns:
        调仓方案 DataFrame: symbol, current_qty, target_qty, delta, side
    """
    rows = []
    for symbol, weight in target_weights.items():
        target_value = total_capital * weight
        price = prices.get(symbol, 0)
        if price <= 0:
            continue

        target_qty = int(target_value / price / 100) * 100  # A股手数取整

        current = current_holdings[current_holdings["symbol"] == symbol]
        current_qty = int(current["quantity"].sum()) if not current.empty else 0

        delta = target_qty - current_qty
        if delta == 0:
            continue

        rows.append({
            "symbol": symbol,
            "current_qty": current_qty,
            "target_qty": target_qty,
            "delta": delta,
            "price": price,
            "side": "BUY" if delta > 0 else "SELL",
            "order_value": abs(delta) * price,
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
