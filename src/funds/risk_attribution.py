"""G6: 组合风险归因。

输出"每基金市值权重 vs 风险贡献"对照,让你看到风险集中度(可能 35% 仓位贡献 70% 波动)。

公式:
- portfolio_vol = sqrt(w' Σ w),其中 w 是权重向量, Σ 是日收益率协方差矩阵(年化)
- marginal contribution to risk: MCR_i = (Σ w)_i / portfolio_vol
- absolute contribution: AC_i = w_i × MCR_i  (满足 sum AC_i = portfolio_vol)
- relative contribution: RC_i = AC_i / portfolio_vol  (满足 sum = 1)

设计:
- 只对有 nav 历史的持仓基金 + 现金;exited / no_snapshot 跳过
- 252 日(1 年)日收益估计;不足则用全部可用数据
- 年化波动率 = 日 σ × sqrt(252)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import duckdb
import numpy as np
import pandas as pd

WINDOW_DAYS = 252       # 1 年
MIN_DAYS = 60           # 最少需要 60 天


@dataclass
class SleeveRisk:
    fund_code: str
    fund_name: str | None
    market_weight: float          # 市值权重
    annual_volatility: float | None  # 自身年化波动
    risk_contribution_abs: float | None  # 对组合年化波动的绝对贡献(组合 vol 单位)
    risk_contribution_pct: float | None  # 占组合波动的比例 (0-1)
    risk_to_weight_ratio: float | None   # risk_pct / market_weight,>1 = 风险集中
    days_used: int


@dataclass
class PortfolioRisk:
    eval_date: str | None
    portfolio_annual_volatility: float | None
    sleeves: list[SleeveRisk]
    correlation_matrix: list[list[float]]  # 相关系数矩阵(行列对齐 sleeves)
    sleeve_codes: list[str]
    headline: str
    risk_tags: list[str] = field(default_factory=list)


def _load_nav_panel(conn: duckdb.DuckDBPyConnection, codes: list[str], days: int) -> pd.DataFrame:
    """读多支基金 nav,返回 wide DataFrame(date × fund_code)。"""
    if not codes:
        return pd.DataFrame()
    placeholders = ", ".join("?" for _ in codes)
    df = conn.execute(
        f"""
        SELECT fund_code, trade_date, nav FROM fund_nav
        WHERE fund_code IN ({placeholders}) AND nav IS NOT NULL
        ORDER BY trade_date
        """,
        codes,
    ).fetchdf()
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot(index="trade_date", columns="fund_code", values="nav")
    return wide.tail(days).dropna(how="all")


def attribute_portfolio_risk(conn: duckdb.DuckDBPyConnection) -> PortfolioRisk:
    """主入口。读持仓 + nav,算贡献。"""
    from src.funds.evaluation import evaluate_funds
    evals = evaluate_funds(conn)
    held = [e for e in evals if e.current_value and e.current_value > 0 and e.intent != "exited"]
    if not held:
        return PortfolioRisk(
            eval_date=None, portfolio_annual_volatility=None,
            sleeves=[], correlation_matrix=[], sleeve_codes=[],
            headline="无可计算的持仓",
        )

    total_held_value = sum(e.current_value for e in held)
    if total_held_value <= 0:
        return PortfolioRisk(
            eval_date=None, portfolio_annual_volatility=None,
            sleeves=[], correlation_matrix=[], sleeve_codes=[],
            headline="持仓总值为 0",
        )
    weights = np.array([e.current_value / total_held_value for e in held])
    codes = [e.fund_code for e in held]
    names = {e.fund_code: e.fund_name for e in held}

    nav_panel = _load_nav_panel(conn, codes, WINDOW_DAYS)
    if nav_panel.empty:
        return PortfolioRisk(
            eval_date=None, portfolio_annual_volatility=None,
            sleeves=[], correlation_matrix=[], sleeve_codes=codes,
            headline="持仓基金 nav 历史不足", risk_tags=["nav_missing"],
        )

    # 只保留有数据的基金
    available = [c for c in codes if c in nav_panel.columns]
    if len(available) < 1:
        return PortfolioRisk(
            eval_date=None, portfolio_annual_volatility=None,
            sleeves=[], correlation_matrix=[], sleeve_codes=codes,
            headline="持仓基金 nav 全部缺失", risk_tags=["nav_missing"],
        )
    # 重新对齐权重
    idx_in_available = [codes.index(c) for c in available]
    weights = weights[idx_in_available]
    nav_panel = nav_panel[available]

    rets = nav_panel.pct_change(fill_method=None).dropna(how="all")
    days_used = len(rets)
    if days_used < MIN_DAYS:
        return PortfolioRisk(
            eval_date=str(nav_panel.index[-1]) if not nav_panel.empty else None,
            portfolio_annual_volatility=None,
            sleeves=[], correlation_matrix=[], sleeve_codes=available,
            headline=f"日收益样本仅 {days_used} 天 (<{MIN_DAYS}),无法归因",
            risk_tags=["short_history"],
        )

    # 协方差 & 相关系数
    cov_daily = rets.cov().to_numpy()
    corr = rets.corr().to_numpy()
    cov_annual = cov_daily * 252
    # 个别基金年化波动
    own_vols = np.sqrt(np.diag(cov_annual))
    # 组合方差
    port_var = float(weights @ cov_annual @ weights.T)
    port_vol = float(np.sqrt(max(port_var, 0)))

    # MCR & contribution
    sleeves: list[SleeveRisk] = []
    if port_vol > 1e-9:
        sigma_w = cov_annual @ weights
        mcr = sigma_w / port_vol
        abs_contrib = weights * mcr
        rel_contrib = abs_contrib / port_vol
        for i, code in enumerate(available):
            sleeves.append(SleeveRisk(
                fund_code=code,
                fund_name=names.get(code),
                market_weight=float(weights[i]),
                annual_volatility=float(own_vols[i]),
                risk_contribution_abs=float(abs_contrib[i]),
                risk_contribution_pct=float(rel_contrib[i]),
                risk_to_weight_ratio=float(rel_contrib[i] / weights[i]) if weights[i] > 1e-9 else None,
                days_used=days_used,
            ))
    else:
        for i, code in enumerate(available):
            sleeves.append(SleeveRisk(
                fund_code=code, fund_name=names.get(code),
                market_weight=float(weights[i]),
                annual_volatility=float(own_vols[i]),
                risk_contribution_abs=None,
                risk_contribution_pct=None,
                risk_to_weight_ratio=None,
                days_used=days_used,
            ))

    # headline:风险最高 vs 市值最高的对比
    sleeves_sorted = sorted([s for s in sleeves if s.risk_contribution_pct is not None],
                            key=lambda s: -(s.risk_contribution_pct or 0))
    risk_tags: list[str] = []
    if not sleeves_sorted:
        headline = f"组合年化波动 {port_vol:.0%}"
    else:
        top = sleeves_sorted[0]
        if (top.risk_to_weight_ratio or 1) > 1.3:
            risk_tags.append("risk_concentration")
            headline = (
                f"组合年化波动 {port_vol:.0%};{top.fund_code} 市值 {top.market_weight:.0%} "
                f"但贡献 {(top.risk_contribution_pct or 0):.0%} 波动 "
                f"(risk/weight={(top.risk_to_weight_ratio or 0):.1f})"
            )
        else:
            headline = (
                f"组合年化波动 {port_vol:.0%};风险贡献最高 {top.fund_code} ({(top.risk_contribution_pct or 0):.0%})"
            )

    return PortfolioRisk(
        eval_date=str(rets.index[-1]),
        portfolio_annual_volatility=port_vol,
        sleeves=sleeves,
        correlation_matrix=[[float(corr[i, j]) for j in range(len(available))] for i in range(len(available))],
        sleeve_codes=available,
        headline=headline,
        risk_tags=risk_tags,
    )


def to_dict(p: PortfolioRisk) -> dict[str, Any]:
    return asdict(p)
