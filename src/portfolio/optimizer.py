"""
组合优化器 — 仓位计算、权重优化。
支持等权重、波动率倒数加权、均值方差优化。
"""
from math import floor

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
        from pypfopt.efficient_frontier import EfficientFrontier
        from pypfopt.expected_returns import mean_historical_return
        from pypfopt.objective import objective_functions
        from pypfopt.risk_models import sample_cov

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


def build_executable_rebalance_plan(
    signals: pd.DataFrame,
    current_weights: dict[str, float],
    latest_prices: dict[str, float],
    available_cash: float,
    total_value: float,
    current_quantities: dict[str, float] = None,
    max_single_position_pct: float = 0.10,
    overweight_single_position_pct: float = 0.15,
    overweight_min_confidence: float = 0.90,
    overweight_min_rank_score: float = 0.85,
    max_gross_exposure_pct: float = 0.95,
    cash_reserve_pct: float = 0.05,
    min_buy_confidence: float = 0.75,
    min_buy_rank_score: float = 0.50,
    lot_size: int = 100,
    estimated_fee_rate: float = 0.0015,
    max_buy_positions: int | None = None,
    existing_position_count: int | None = None,
) -> pd.DataFrame:
    """
    将研究信号转成账户规模约束下的可执行调仓草案。

    规则：
    - 现有持仓默认保留，只有 SELL/SHORT 信号才生成减仓/清仓提示。
    - 先执行减仓/清仓，再用释放出的现金参与本期买入预算。
    - BUY 信号必须先达到执行门槛，避免为填满现金买入弱信号。
    - 高置信、高排序分的 BUY 信号允许使用超配单票上限。
    - BUY 信号按置信度和分数排序，受可用现金、现金保留、总仓位上限、单票上限约束。
    - 买入数量按一手取整；不可执行的信号保留为候选并写明原因。
    """
    columns = [
        "symbol", "side", "current_weight", "target_weight", "delta_weight",
        "action", "executable", "reason", "price", "quantity", "order_value",
        "estimated_fee", "cash_after", "score", "confidence", "min_lot_value", "funding_gap",
    ]
    if signals.empty:
        return pd.DataFrame(columns=columns)

    account_value = float(total_value or 0)
    cash = max(float(available_cash or 0), 0.0)
    if account_value <= 0:
        account_value = cash
    if account_value <= 0:
        return pd.DataFrame(columns=columns)

    reserve_cash = max(account_value * cash_reserve_pct, 0.0)
    current_exposure = sum(max(float(w or 0), 0.0) for w in current_weights.values()) * account_value

    rows: list[dict] = []
    current_quantities = current_quantities or {}
    cash_after = cash
    exposure_after_reduces = current_exposure
    conflicted_symbols = _find_conflicted_symbols(signals)
    for symbol in sorted(conflicted_symbols):
        symbol_rows = signals[signals["symbol"].astype(str) == symbol].copy()
        symbol_rows["_rank_score"] = symbol_rows.apply(
            lambda r: _safe_float(r.get("confidence"), 0.0) * max(_safe_float(r.get("score"), 1.0), 0.0),
            axis=1,
        )
        best = symbol_rows.sort_values(
            ["_rank_score", "confidence", "score"],
            ascending=[False, False, False],
        ).iloc[0]
        cur_weight = float(current_weights.get(symbol, 0.0) or 0.0)
        price = _safe_price(latest_prices.get(symbol))
        rows.append({
            "symbol": symbol,
            "side": "BUY" if (symbol_rows["side"] == "BUY").any() else best.get("side"),
            "current_weight": cur_weight,
            "target_weight": cur_weight,
            "delta_weight": 0.0,
            "action": "候选",
            "executable": False,
            "reason": "多策略方向冲突，需人工确认",
            "price": price if price > 0 else np.nan,
            "quantity": 0,
            "order_value": 0.0,
            "estimated_fee": 0.0,
            "cash_after": cash_after,
            "score": _safe_float(best.get("score"), 0.0),
            "confidence": _safe_float(best.get("confidence"), 0.0),
        })

    signals_for_execution = signals[~signals["symbol"].astype(str).isin(conflicted_symbols)].copy()

    sell_signals = signals_for_execution[signals_for_execution["side"].isin(["SELL", "SHORT"])].copy()
    if not sell_signals.empty:
        sell_signals["_rank_score"] = sell_signals.apply(
            lambda r: _safe_float(r.get("confidence"), 0.0) * max(_safe_float(r.get("score"), 1.0), 0.0),
            axis=1,
        )
        sell_signals = sell_signals.sort_values(
            ["_rank_score", "confidence", "score"],
            ascending=[False, False, False],
        )

    for _, row in sell_signals.iterrows():
        symbol = str(row["symbol"])
        cur_weight = float(current_weights.get(symbol, 0.0) or 0.0)
        if cur_weight <= 0:
            continue
        price = _safe_price(latest_prices.get(symbol))
        current_value = cur_weight * account_value
        held_qty = _safe_float(current_quantities.get(symbol), 0.0)
        executable = price > 0
        if executable and held_qty > 0:
            quantity = held_qty
            sell_value = quantity * price
        elif executable:
            quantity = floor(current_value / price)
            sell_value = quantity * price
        else:
            quantity = 0
            sell_value = 0.0

        sell_value = min(max(sell_value, 0.0), current_value)
        estimated_fee = sell_value * estimated_fee_rate
        net_release = max(sell_value - estimated_fee, 0.0)
        if executable and sell_value > 0:
            cash_after += net_release
            exposure_after_reduces = max(exposure_after_reduces - sell_value, 0.0)
            reason = "释放资金可用于本期加仓"
        else:
            executable = False
            estimated_fee = 0.0
            reason = "缺少最新价格，暂不生成减仓订单"

        rows.append({
            "symbol": symbol,
            "side": row.get("side"),
            "current_weight": cur_weight,
            "target_weight": 0.0,
            "delta_weight": -cur_weight,
            "action": "清仓",
            "executable": executable,
            "reason": reason,
            "price": price if price > 0 else np.nan,
            "quantity": quantity,
            "order_value": -sell_value,
            "estimated_fee": estimated_fee,
            "cash_after": cash_after,
            "score": _safe_float(row.get("score"), 0.0),
            "confidence": _safe_float(row.get("confidence"), 0.0),
        })

    remaining_budget = min(
        max(cash_after - reserve_cash, 0.0),
        max(account_value * max_gross_exposure_pct - exposure_after_reduces, 0.0),
    )

    buy_signals = signals_for_execution[signals_for_execution["side"] == "BUY"].copy()
    if buy_signals.empty:
        return pd.DataFrame(rows, columns=columns)

    buy_signals["_rank_score"] = buy_signals.apply(
        lambda r: _safe_float(r.get("confidence"), 0.0) * max(_safe_float(r.get("score"), 1.0), 0.0),
        axis=1,
    )
    buy_signals = buy_signals.sort_values(
        ["_rank_score", "confidence", "score"],
        ascending=[False, False, False],
    )
    held_symbols = {symbol for symbol, weight in current_weights.items() if _safe_float(weight, 0.0) > 0}
    position_count = (
        max(int(existing_position_count), 0)
        if existing_position_count is not None
        else len(held_symbols)
    )
    max_positions = int(max_buy_positions) if max_buy_positions is not None else None

    for _, row in buy_signals.iterrows():
        symbol = str(row["symbol"])
        cur_weight = float(current_weights.get(symbol, 0.0) or 0.0)
        score = _safe_float(row.get("score"), 0.0)
        confidence = _safe_float(row.get("confidence"), 0.0)
        rank_score = confidence * max(score, 0.0)
        signal_cap = _safe_float(row.get("max_position_pct"), max_single_position_pct)
        single_cap = _effective_single_position_cap(
            confidence=confidence,
            rank_score=rank_score,
            normal_cap=max_single_position_pct,
            overweight_cap=overweight_single_position_pct,
            overweight_min_confidence=overweight_min_confidence,
            overweight_min_rank_score=overweight_min_rank_score,
        )
        target_cap = min(max(signal_cap, 0.0), single_cap)
        current_value = cur_weight * account_value
        target_value_cap = target_cap * account_value
        desired_add_value = max(target_value_cap - current_value, 0.0)
        price = _safe_price(latest_prices.get(symbol))
        max_order_value = min(desired_add_value, remaining_budget)

        reason = ""
        quantity = 0
        order_value = 0.0
        estimated_fee = 0.0
        executable = False
        min_lot_value = price * lot_size if price > 0 else 0.0
        funding_gap = 0.0

        if price <= 0:
            reason = "缺少最新价格"
        elif confidence < min_buy_confidence:
            reason = "低于执行置信度门槛"
        elif rank_score < min_buy_rank_score:
            reason = "低于执行排序分门槛"
        elif target_cap <= 0:
            reason = "信号未给出有效仓位"
        elif desired_add_value <= 0:
            reason = "已达到目标仓位"
        elif (
            max_positions is not None
            and symbol not in held_symbols
            and position_count >= max_positions
        ):
            reason = "达到本档位持仓数量上限"
        elif remaining_budget <= 0:
            reason = "可用预算不足"
        else:
            affordable_value = max_order_value / (1 + estimated_fee_rate)
            quantity = floor(affordable_value / (price * lot_size)) * lot_size
            order_value = quantity * price
            estimated_fee = order_value * estimated_fee_rate
            executable = quantity > 0 and order_value + estimated_fee <= remaining_budget + 1e-6
            if executable:
                reason = "可执行"
                remaining_budget -= order_value + estimated_fee
                cash_after -= order_value + estimated_fee
                if symbol not in held_symbols:
                    held_symbols.add(symbol)
                    position_count += 1
            else:
                min_lot_cash_required = min_lot_value * (1 + estimated_fee_rate)
                funding_gap = max(min_lot_cash_required - max_order_value, 0.0)
                reason = f"不足一手，需至少 {min_lot_cash_required:,.0f}，还差 {funding_gap:,.0f}"
                quantity = 0
                order_value = 0.0
                estimated_fee = 0.0

        planned_value = current_value + order_value
        target_weight = planned_value / account_value if account_value > 0 else 0.0
        rows.append({
            "symbol": symbol,
            "side": "BUY",
            "current_weight": cur_weight,
            "target_weight": target_weight,
            "delta_weight": order_value / account_value if account_value > 0 else 0.0,
            "action": "买入" if executable else "候选",
            "executable": executable,
            "reason": reason,
            "price": price if price > 0 else np.nan,
            "quantity": quantity,
            "order_value": order_value,
            "estimated_fee": estimated_fee,
            "cash_after": cash_after,
            "score": score,
            "confidence": confidence,
            "min_lot_value": min_lot_value,
            "funding_gap": funding_gap,
        })

    return pd.DataFrame(rows, columns=columns)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_conflicted_symbols(signals: pd.DataFrame) -> set[str]:
    """Symbols with both BUY and SELL/SHORT in the same decision batch."""
    if signals.empty or "symbol" not in signals.columns or "side" not in signals.columns:
        return set()
    sides = signals.assign(symbol=signals["symbol"].astype(str)).groupby("symbol")["side"].agg(set)
    return {
        symbol for symbol, side_set in sides.items()
        if "BUY" in side_set and bool(side_set & {"SELL", "SHORT"})
    }


def _safe_price(value) -> float:
    price = _safe_float(value, 0.0)
    if price <= 0 or not np.isfinite(price):
        return 0.0
    return price


def _effective_single_position_cap(
    confidence: float,
    rank_score: float,
    normal_cap: float,
    overweight_cap: float,
    overweight_min_confidence: float,
    overweight_min_rank_score: float,
) -> float:
    """Return normal or overweight single-name cap for high-conviction signals."""
    normal = max(_safe_float(normal_cap, 0.10), 0.0)
    overweight = max(_safe_float(overweight_cap, normal), normal)
    if confidence >= overweight_min_confidence and rank_score >= overweight_min_rank_score:
        return overweight
    return normal


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
