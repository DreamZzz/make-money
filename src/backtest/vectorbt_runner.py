"""
Vectorbt 回测 Runner — 独立验证工具，避免单一框架偏差。
日频/周频向量化回测，速度快，适合快速迭代。
"""
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from src.backtest.results import compute_metrics, load_benchmark_returns, save_backtest_result


def _load_price_data(market: str = "cn") -> pd.DataFrame:
    """从 DuckDB 加载日线数据，整理为 vectorbt 需要的宽表格式"""
    from src.data_pipeline.loader import get_connection
    conn = get_connection(read_only=True)

    df = conn.execute(f"""
        SELECT symbol, trade_date, close, open, high, low, volume
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country = ?)
        ORDER BY symbol, trade_date
    """, [market.upper() if market == "cn" else market.upper()]).fetchdf()
    conn.close()

    if df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close = df.pivot(index="trade_date", columns="symbol", values="close")
    return close.sort_index()


def run_momentum_backtest(
    lookback: int = 20,
    top_n: int = 20,
    rebalance_freq: str = "W",
    save_result: bool = False,
) -> dict:
    """
    动量策略回测：买入过去 lookback 天涨幅最大的 top_n 只股票。
    等权重、每周调仓。
    """
    close = _load_price_data("cn")
    if close.empty:
        logger.warning("No price data for vectorbt backtest")
        return {}

    returns = close.pct_change().dropna(how="all")

    # 计算动量信号
    momentum = close.pct_change(lookback).dropna(how="all")

    # 每周生成持仓信号；resample 标签可能是周末，不要求它存在于原交易日索引。
    rebalance_scores = momentum.resample(rebalance_freq).last().dropna(how="all")
    rebalance_dates = rebalance_scores.index

    portfolio_returns = []
    for i, dt in enumerate(rebalance_dates):
        scores = rebalance_scores.loc[dt].dropna().nlargest(top_n)
        if scores.empty:
            continue

        # 下一周的等权重收益
        end = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else returns.index[-1]
        period_returns = returns.loc[dt:end, scores.index].dropna(how="all")
        if not period_returns.empty:
            portfolio_returns.append(period_returns.mean(axis=1))

    if not portfolio_returns:
        return {}

    all_returns = pd.concat(portfolio_returns).sort_index()
    all_returns = all_returns[~all_returns.index.duplicated()]

    metrics = compute_metrics(
        all_returns,
        benchmark_returns=load_benchmark_returns("000300"),
        turnover=52 / lookback * top_n,
    )
    if save_result and metrics:
        metrics["run_id"] = save_backtest_result(
            "momentum_topn",
            "CN",
            metrics,
            {"lookback": lookback, "top_n": top_n, "rebalance_freq": rebalance_freq},
        )
    return metrics


def run_benchmark(benchmark: str = "000300.SH") -> dict:
    """运行基准指数回测"""
    from src.data_pipeline.loader import get_connection
    conn = get_connection(read_only=True)

    df = conn.execute("""
        SELECT trade_date, close FROM index_daily
        WHERE index_code = ?
        ORDER BY trade_date
    """, [benchmark]).fetchdf()
    conn.close()

    if df.empty:
        return {}

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    returns = df["close"].pct_change().dropna()

    cumulative = (1 + returns).cumprod()
    annual_return = (cumulative.iloc[-1]) ** (252 / len(returns)) - 1
    annual_vol = returns.std() * np.sqrt(252)
    sharpe = (annual_return - 0.03) / annual_vol if annual_vol > 0 else 0

    peak = cumulative.expanding().max()
    drawdown = (cumulative - peak) / peak

    metrics = {
        "annual_return": annual_return,
        "cumulative_return": cumulative.iloc[-1] - 1,
        "annual_volatility": annual_vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": drawdown.min(),
    }
    return metrics


def compare_with_qlib(
    qlib_result: dict,
    vbt_result: dict,
    tolerance: float = 0.02
) -> dict:
    """
    对比 Qlib 和 vectorbt 的回测结果。
    偏差超过 tolerance 需要排查。
    """
    comparison = {}
    for key in ["annual_return", "sharpe_ratio", "max_drawdown"]:
        v1 = qlib_result.get(key)
        v2 = vbt_result.get(key)
        if v1 is None or v2 is None:
            comparison[key] = {"status": "missing", "qlib": v1, "vbt": v2}
        elif abs(v1 - v2) > tolerance:
            comparison[key] = {"status": "diverged", "qlib": v1, "vbt": v2, "diff": v1 - v2}
        else:
            comparison[key] = {"status": "ok", "qlib": v1, "vbt": v2, "diff": v1 - v2}
    return comparison
