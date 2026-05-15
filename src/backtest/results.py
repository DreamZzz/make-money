"""Backtest metric helpers and DuckDB persistence."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def _to_date(value) -> date:
    return pd.to_datetime(value).date()


def compute_metrics(
    returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    risk_free_rate: float = 0.03,
    turnover: float | None = None,
) -> dict[str, Any]:
    """Compute the common metrics expected by backtest_results."""
    returns = pd.Series(returns).dropna()
    returns = returns[~returns.index.duplicated()].sort_index()
    if returns.empty:
        return {}

    cumulative = (1 + returns).cumprod()
    years = max(len(returns) / 252, 1 / 252)
    cumulative_return = float(cumulative.iloc[-1] - 1)
    annual_return = float(cumulative.iloc[-1] ** (1 / years) - 1)
    annual_vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else 0.0
    sharpe = float((annual_return - risk_free_rate) / annual_vol) if annual_vol > 0 else 0.0

    downside = returns[returns < 0]
    downside_vol = float(downside.std() * np.sqrt(252)) if len(downside) > 1 else 0.0
    sortino = float((annual_return - risk_free_rate) / downside_vol) if downside_vol > 0 else 0.0

    peak = cumulative.expanding().max()
    drawdown = (cumulative - peak) / peak
    max_drawdown = float(drawdown.min())
    max_drawdown_days = int(_max_drawdown_days(drawdown))

    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = float(len(wins) / len(returns)) if len(returns) else 0.0
    avg_win_loss = (
        float(wins.mean() / abs(losses.mean()))
        if not wins.empty and not losses.empty and losses.mean() != 0
        else 0.0
    )

    benchmark_return = None
    info_ratio = None
    excess_return = None
    if benchmark_returns is not None and not benchmark_returns.empty:
        bench = pd.Series(benchmark_returns).dropna().sort_index()
        common = returns.index.intersection(bench.index)
        if len(common) > 1:
            bench_common = bench.loc[common]
            bench_cum = (1 + bench_common).cumprod()
            bench_years = max(len(bench_common) / 252, 1 / 252)
            benchmark_return = float(bench_cum.iloc[-1] ** (1 / bench_years) - 1)
            active = returns.loc[common] - bench_common
            active_vol = float(active.std() * np.sqrt(252))
            info_ratio = float(active.mean() * 252 / active_vol) if active_vol > 0 else 0.0
            excess_return = annual_return - benchmark_return

    return {
        "start_date": _to_date(returns.index.min()),
        "end_date": _to_date(returns.index.max()),
        "annual_return": annual_return,
        "cumulative_return": cumulative_return,
        "annual_volatility": annual_vol,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "max_drawdown_days": max_drawdown_days,
        "win_rate": win_rate,
        "avg_win_loss": avg_win_loss,
        "turnover": float(turnover) if turnover is not None else None,
        "info_ratio": info_ratio,
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
    }


def save_backtest_result(
    strategy_name: str,
    market: str,
    metrics: dict[str, Any],
    config_snapshot: dict[str, Any] | None = None,
    run_id: str | None = None,
    engine: str = "unknown",
    decision_scope: str = "decision",
) -> str:
    """Persist one backtest result row and return its run_id."""
    if not metrics:
        raise ValueError("Cannot save empty backtest metrics")

    from src.data_pipeline.loader import get_connection

    run_id = run_id or f"BT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    snapshot = json.dumps(config_snapshot or {}, ensure_ascii=False, default=str)
    row = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "market": market,
        "engine": engine,
        "decision_scope": decision_scope,
        "config_snapshot": snapshot,
        **metrics,
    }
    cols = [
        "run_id", "strategy_name", "market", "engine", "decision_scope", "start_date", "end_date",
        "annual_return", "cumulative_return", "annual_volatility",
        "sharpe_ratio", "sortino_ratio", "max_drawdown",
        "max_drawdown_days", "win_rate", "avg_win_loss", "turnover",
        "info_ratio", "benchmark_return", "excess_return", "config_snapshot",
    ]
    conn = get_connection()
    try:
        conn.register("_tmp_backtest_result_df", pd.DataFrame([{col: row.get(col) for col in cols}]))
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_backtest_result AS SELECT * FROM _tmp_backtest_result_df")
        conn.execute(f"""
            INSERT OR REPLACE INTO backtest_results ({", ".join(cols)})
            SELECT {", ".join(cols)} FROM _tmp_backtest_result
        """)
    finally:
        conn.close()
    return run_id


def load_benchmark_returns(index_code: str = "000300") -> pd.Series:
    """Load benchmark daily returns from index_daily."""
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    try:
        df = conn.execute("""
            SELECT trade_date, close FROM index_daily
            WHERE index_code = ?
            ORDER BY trade_date
        """, [index_code]).fetchdf()
    finally:
        conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")["close"].pct_change().dropna()


def _max_drawdown_days(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in drawdown.fillna(0):
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
