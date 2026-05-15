"""
风控规则 — 回撤止损、单标的上限、行业上限、最大杠杆控制。
"""
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger


@dataclass
class RiskLimits:
    """风控参数配置"""
    max_single_position_pct: float = 0.10
    max_industry_pct: float = 0.30
    max_drawdown_pct: float = 0.20
    max_leverage: float = 1.0
    max_daily_turnover_pct: float = 0.30
    min_cash_pct: float = 0.05
    blacklist: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)


def check_drawdown(nav_series: pd.Series, max_dd: float = 0.20) -> tuple[bool, float]:
    """检查当前回撤是否超过阈值"""
    if nav_series.empty:
        return False, 0.0
    peak = nav_series.expanding().max()
    drawdown = (nav_series.iloc[-1] - peak.iloc[-1]) / peak.iloc[-1]
    triggered = drawdown < -max_dd
    if triggered:
        logger.warning(f"Drawdown limit triggered: {drawdown:.2%}")
    return triggered, drawdown


def check_position_limits(
    positions: pd.DataFrame,
    total_value: float,
    limits: RiskLimits,
) -> pd.DataFrame:
    """
    执行单标的和行业仓位上限：超限项强制裁减 order_value，并标记 limit_breach。
    这是风控的唯一执行入口，optimizer.apply_risk_rules 委托给此函数。
    """
    if positions.empty or total_value <= 0:
        return positions

    positions = positions.copy()
    positions["limit_breach"] = False

    # ---- 单标的上限：超限按比例裁减 ----
    positions["weight"] = positions["order_value"] / total_value
    over_single = positions["weight"] > limits.max_single_position_pct
    if over_single.any():
        logger.warning(f"Single position limit capped: {positions.loc[over_single, 'symbol'].tolist()}")
        positions.loc[over_single, "order_value"] = total_value * limits.max_single_position_pct
        positions.loc[over_single, "limit_breach"] = True
        positions["weight"] = positions["order_value"] / total_value  # 重算 weight

    # ---- 行业上限：超限行业内各股票等比例缩减 ----
    if "industry" in positions.columns:
        industry_value = positions.groupby("industry")["order_value"].sum()
        over_industry = industry_value[industry_value / total_value > limits.max_industry_pct]
        if not over_industry.empty:
            logger.warning(f"Industry limit capped: {over_industry.index.tolist()}")
            for ind in over_industry.index:
                mask = positions["industry"] == ind
                ind_total = positions.loc[mask, "order_value"].sum()
                if ind_total > 0:
                    scale = (total_value * limits.max_industry_pct) / ind_total
                    positions.loc[mask, "order_value"] *= scale
                    positions.loc[mask, "limit_breach"] = True
            positions["weight"] = positions["order_value"] / total_value

    # ---- 黑名单过滤 ----
    if limits.blacklist and "symbol" in positions.columns:
        blocked = positions["symbol"].isin(limits.blacklist)
        if blocked.any():
            logger.warning(f"Blacklist filtered: {positions.loc[blocked, 'symbol'].tolist()}")
            positions = positions[~blocked].reset_index(drop=True)

    return positions


def check_blacklist(symbol: str, blacklist: list[str]) -> bool:
    """检查股票是否在黑名单中"""
    return symbol in blacklist


def compute_kill_switch(positions: pd.DataFrame, nav: float, peak_nav: float) -> bool:
    """
    Kill switch 判定：
    - 回撤超限 → True
    - 组合净值为0 → True
    """
    if nav <= 0:
        logger.error("NAV <= 0 — kill switch")
        return True

    if peak_nav > 0:
        dd = (nav - peak_nav) / peak_nav
        if dd < -0.25:  # 硬止损 25%
            logger.error(f"Critical drawdown {dd:.2%} — kill switch")
            return True

    return False
