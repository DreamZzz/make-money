"""G7: 历史 block-bootstrap 蒙特卡洛。

用持仓 nav 近 5 年日收益做 block bootstrap(块长 5 天保留短期序列相关),
模拟未来 252 个交易日(1 年)组合累积收益,输出 5/50/95 分位 + 最大回撤估计。

为什么 block bootstrap 而非 i.i.d.:
- 日收益有自相关(momentum / mean-reversion);i.i.d. 抽样低估真实风险
- 块抽样保留短期序列结构;5 天块在 ETF 历史里效果稳

为什么不用 GBM / 参数化:
- 参数化需要假设(正态/对数正态),实证厚尾远超
- bootstrap 直接采样真实分布,不用假设

为什么不用蒙特卡洛 + Cholesky 关联抽样:
- 协方差矩阵在 60 天样本下不稳定
- 直接对组合日收益 series 抽样,跨基金关联自动保留
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import duckdb
import numpy as np
import pandas as pd

HISTORY_WINDOW_DAYS = 252 * 5    # 5 年(约 1260 个交易日)
SIMULATION_HORIZON_DAYS = 252    # 1 年
N_PATHS = 5000
BLOCK_SIZE = 5
PERCENTILES = [5, 25, 50, 75, 95]
MIN_HISTORY_DAYS = 250            # < 1 年历史拒绝


@dataclass
class MonteCarloResult:
    eval_date: str | None
    horizon_days: int
    n_paths: int
    history_days_used: int
    block_size: int
    # 终值收益率分位(t=horizon)
    return_percentiles: dict[str, float]   # p5/p25/p50/p75/p95
    # 路径中的最大回撤分位(每条路径回撤的最大值,再分位)
    drawdown_percentiles: dict[str, float]
    # 简易点估
    expected_return: float
    expected_volatility: float
    prob_loss: float                       # 终值 < 0 的概率
    prob_loss_10pct: float                 # 终值 < -10% 的概率
    headline: str
    risk_tags: list[str] = field(default_factory=list)


def _portfolio_daily_returns(
    conn: duckdb.DuckDBPyConnection,
    holdings: list[dict[str, Any]],
    history_days: int,
) -> tuple[pd.Series, int]:
    """根据持仓权重和历史 nav 算组合日收益 series。"""
    if not holdings:
        return pd.Series(dtype=float), 0
    codes = [h["fund_code"] for h in holdings]
    weights = np.array([h["weight"] for h in holdings])
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
        return pd.Series(dtype=float), 0
    wide = df.pivot(index="trade_date", columns="fund_code", values="nav").tail(history_days)
    available = [c for c in codes if c in wide.columns]
    if not available:
        return pd.Series(dtype=float), 0
    idx_in_available = [codes.index(c) for c in available]
    w = weights[idx_in_available]
    w = w / w.sum() if w.sum() > 0 else w
    rets = wide[available].pct_change(fill_method=None).dropna(how="all")
    # 缺失值补 0(避免某基金缺数据日组合收益变 NaN)
    rets = rets.fillna(0.0)
    port_rets = (rets.to_numpy() @ w)
    series = pd.Series(port_rets, index=rets.index)
    return series, len(series)


def _block_bootstrap_paths(
    daily_returns: np.ndarray,
    *,
    n_paths: int,
    horizon: int,
    block_size: int,
    seed: int = 42,
) -> np.ndarray:
    """block bootstrap → (n_paths, horizon) 矩阵的累积净值(从 1 开始)。"""
    rng = np.random.default_rng(seed)
    n = len(daily_returns)
    n_blocks = (horizon + block_size - 1) // block_size
    # 每条路径独立抽样 n_blocks 个起点,拼成 horizon 天
    nav_paths = np.ones((n_paths, horizon + 1))
    max_start = n - block_size
    if max_start <= 0:
        return nav_paths
    for p in range(n_paths):
        starts = rng.integers(0, max_start, size=n_blocks)
        # 拼接成 1 维收益序列
        rets_path = np.concatenate([daily_returns[s:s + block_size] for s in starts])[:horizon]
        nav_paths[p, 1:] = np.cumprod(1 + rets_path)
    return nav_paths


def _max_drawdown(path: np.ndarray) -> float:
    """单路径最大回撤(峰到谷的最大跌幅,负数)。"""
    running_max = np.maximum.accumulate(path)
    dd = path / running_max - 1.0
    return float(dd.min())


def simulate_portfolio(
    conn: duckdb.DuckDBPyConnection,
    *,
    n_paths: int = N_PATHS,
    horizon_days: int = SIMULATION_HORIZON_DAYS,
    block_size: int = BLOCK_SIZE,
    history_window_days: int = HISTORY_WINDOW_DAYS,
    seed: int = 42,
) -> MonteCarloResult:
    """主入口:基于当前持仓 + nav 历史做 block bootstrap 蒙特卡洛。"""
    from src.funds.evaluation import evaluate_funds
    evals = evaluate_funds(conn)
    held = [e for e in evals
            if e.current_value and e.current_value > 0 and e.intent != "exited"]
    if not held:
        return MonteCarloResult(
            eval_date=None, horizon_days=horizon_days, n_paths=0,
            history_days_used=0, block_size=block_size,
            return_percentiles={}, drawdown_percentiles={},
            expected_return=0.0, expected_volatility=0.0,
            prob_loss=0.0, prob_loss_10pct=0.0,
            headline="无可计算持仓", risk_tags=["no_holdings"],
        )
    total_val = sum(e.current_value for e in held)
    holdings = [{"fund_code": e.fund_code, "weight": e.current_value / total_val} for e in held]
    port_rets, days_used = _portfolio_daily_returns(conn, holdings, history_window_days)
    if days_used < MIN_HISTORY_DAYS:
        return MonteCarloResult(
            eval_date=str(port_rets.index[-1]) if len(port_rets) else None,
            horizon_days=horizon_days, n_paths=0,
            history_days_used=days_used, block_size=block_size,
            return_percentiles={}, drawdown_percentiles={},
            expected_return=0.0, expected_volatility=0.0,
            prob_loss=0.0, prob_loss_10pct=0.0,
            headline=f"历史样本 {days_used} 天 < {MIN_HISTORY_DAYS},无法模拟",
            risk_tags=["short_history"],
        )

    paths = _block_bootstrap_paths(
        port_rets.to_numpy(),
        n_paths=n_paths, horizon=horizon_days, block_size=block_size, seed=seed,
    )
    terminal_returns = paths[:, -1] - 1.0
    drawdowns = np.array([_max_drawdown(paths[i]) for i in range(n_paths)])
    return_pcts = {f"p{p}": float(np.percentile(terminal_returns, p)) for p in PERCENTILES}
    dd_pcts = {f"p{p}": float(np.percentile(drawdowns, p)) for p in PERCENTILES}
    expected_return = float(terminal_returns.mean())
    expected_volatility = float(np.log(1 + terminal_returns).std())
    prob_loss = float((terminal_returns < 0).mean())
    prob_loss_10pct = float((terminal_returns < -0.10).mean())

    p50_dd = dd_pcts["p50"]
    p5_dd = dd_pcts["p5"]
    p50_ret = return_pcts["p50"]
    p5_ret = return_pcts["p5"]
    p95_ret = return_pcts["p95"]
    headline = (
        f"1 年区间 5/50/95: {p5_ret:+.0%} / {p50_ret:+.0%} / {p95_ret:+.0%};"
        f"路径回撤 中位 {p50_dd:.0%} / 5 分位 {p5_dd:.0%};"
        f"亏损概率 {prob_loss:.0%} (亏 >10% 概率 {prob_loss_10pct:.0%})"
    )
    risk_tags: list[str] = []
    if prob_loss_10pct > 0.30:
        risk_tags.append("high_tail_risk")
    if p5_dd < -0.30:
        risk_tags.append("deep_drawdown_risk")

    return MonteCarloResult(
        eval_date=str(port_rets.index[-1]),
        horizon_days=horizon_days, n_paths=n_paths,
        history_days_used=days_used, block_size=block_size,
        return_percentiles=return_pcts, drawdown_percentiles=dd_pcts,
        expected_return=expected_return, expected_volatility=expected_volatility,
        prob_loss=prob_loss, prob_loss_10pct=prob_loss_10pct,
        headline=headline, risk_tags=risk_tags,
    )


def to_dict(r: MonteCarloResult) -> dict[str, Any]:
    return asdict(r)
