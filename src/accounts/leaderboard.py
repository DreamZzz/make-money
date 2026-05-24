"""竞赛榜：从 account_nav / account_orders / 基准计算各账户横向可比指标。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.accounts.registry import AccountRecord, list_accounts

TRADING_DAYS = 252


@dataclass
class AccountMetrics:
    account_id: str
    as_of_date: date | None
    window_label: str
    sample_days: int
    annual_return: float
    cumulative_return: float
    annual_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    turnover: float
    hit_rate: float | None
    benchmark_return: float | None
    excess_return: float | None
    info_ratio: float | None
    ready_outcomes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_account_metrics(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    window_label: str = "replay",
) -> AccountMetrics | None:
    nav = conn.execute(
        """
        SELECT trade_date, nav, daily_return, total_value
        FROM account_nav WHERE account_id = ? ORDER BY trade_date
        """,
        [account.account_id],
    ).fetchdf()
    if nav.empty or len(nav) < 2:
        return None
    nav["trade_date"] = pd.to_datetime(nav["trade_date"])
    n = len(nav)
    years = n / TRADING_DAYS
    cumulative = float(nav["nav"].iloc[-1] / nav["nav"].iloc[0] - 1.0)
    annual_return = float((1.0 + cumulative) ** (1.0 / years) - 1.0) if years > 0 and cumulative > -1 else 0.0
    daily = nav["daily_return"].fillna(0.0).to_numpy()
    annual_vol = float(np.std(daily, ddof=1) * np.sqrt(TRADING_DAYS)) if n > 1 else 0.0
    sharpe = float(annual_return / annual_vol) if annual_vol > 1e-9 else 0.0
    max_dd = float(_max_drawdown(nav["nav"].to_numpy()))

    start, end = nav["trade_date"].iloc[0].date(), nav["trade_date"].iloc[-1].date()
    bench = _benchmark_series(conn, account.config.benchmark_index, start, end)
    benchmark_return = excess_return = info_ratio = None
    if bench is not None and not bench.empty:
        merged = nav[["trade_date", "daily_return"]].merge(bench, on="trade_date", how="inner")
        if len(merged) > 1:
            b_cum = float(merged["bench_close"].iloc[-1] / merged["bench_close"].iloc[0] - 1.0)
            benchmark_return = float((1.0 + b_cum) ** (1.0 / years) - 1.0) if years > 0 and b_cum > -1 else 0.0
            excess_return = annual_return - benchmark_return
            merged["bench_ret"] = merged["bench_close"].pct_change().fillna(0.0)
            active = merged["daily_return"].fillna(0.0) - merged["bench_ret"]
            te = float(np.std(active.to_numpy(), ddof=1) * np.sqrt(TRADING_DAYS))
            info_ratio = float((active.mean() * TRADING_DAYS) / te) if te > 1e-9 else None

    turnover = _annualized_turnover(conn, account.account_id, float(nav["total_value"].mean()), years)
    wins, closed = _round_trip_stats(conn, account.account_id)
    hit_rate = float(wins / closed) if closed > 0 else None

    return AccountMetrics(
        account_id=account.account_id, as_of_date=end, window_label=window_label,
        sample_days=n, annual_return=annual_return, cumulative_return=cumulative,
        annual_volatility=annual_vol, sharpe_ratio=sharpe, max_drawdown=max_dd,
        turnover=turnover, hit_rate=hit_rate, benchmark_return=benchmark_return,
        excess_return=excess_return, info_ratio=info_ratio, ready_outcomes=closed,
    )


def persist_metrics(conn: duckdb.DuckDBPyConnection, m: AccountMetrics) -> None:
    conn.execute(
        "DELETE FROM account_performance WHERE account_id = ? AND as_of_date = ? AND window_label = ?",
        [m.account_id, m.as_of_date, m.window_label],
    )
    conn.execute(
        """
        INSERT INTO account_performance (
            account_id, as_of_date, window_label, sample_days, annual_return, cumulative_return,
            annual_volatility, sharpe_ratio, max_drawdown, turnover, hit_rate,
            benchmark_return, excess_return, info_ratio, ready_outcomes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            m.account_id, m.as_of_date, m.window_label, m.sample_days, m.annual_return,
            m.cumulative_return, m.annual_volatility, m.sharpe_ratio, m.max_drawdown,
            m.turnover, m.hit_rate, m.benchmark_return, m.excess_return, m.info_ratio,
            m.ready_outcomes,
        ],
    )


def refresh_all_metrics(
    conn: duckdb.DuckDBPyConnection,
    window_label: str = "replay",
    status: str | None = None,
) -> list[AccountMetrics]:
    out = []
    for account in list_accounts(conn, status=status):
        m = compute_account_metrics(conn, account, window_label=window_label)
        if m is not None:
            persist_metrics(conn, m)
            out.append(m)
    return out


def build_leaderboard(
    conn: duckdb.DuckDBPyConnection,
    window_label: str = "replay",
    rank_by: str = "excess_return",
) -> list[dict[str, Any]]:
    """返回按指定指标降序排名的竞赛榜（每账户取最新快照）。"""
    rows = conn.execute(
        """
        SELECT p.* FROM account_performance p
        JOIN (
            SELECT account_id, MAX(as_of_date) AS mx
            FROM account_performance WHERE window_label = ? GROUP BY account_id
        ) latest ON p.account_id = latest.account_id AND p.as_of_date = latest.mx
        WHERE p.window_label = ?
        """,
        [window_label, window_label],
    ).fetchdf()
    if rows.empty:
        return []
    names = {a.account_id: a.name for a in list_accounts(conn)}
    rows["name"] = rows["account_id"].map(names)
    sort_col = rank_by if rank_by in rows.columns else "excess_return"
    rows = rows.sort_values(sort_col, ascending=False, na_position="last")
    rows.insert(0, "rank", range(1, len(rows) + 1))
    return rows.to_dict(orient="records")


def _max_drawdown(nav: np.ndarray) -> float:
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    return float(dd.min()) if len(dd) else 0.0


def _benchmark_series(conn, index_code: str, start: date, end: date) -> pd.DataFrame | None:
    df = conn.execute(
        "SELECT trade_date, close AS bench_close FROM index_daily WHERE index_code = ? "
        "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        [index_code, start, end],
    ).fetchdf()
    if df.empty:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def _annualized_turnover(conn, account_id: str, avg_total: float, years: float) -> float:
    if avg_total <= 0 or years <= 0:
        return 0.0
    buy_value = conn.execute(
        "SELECT COALESCE(SUM(order_value), 0) FROM account_orders WHERE account_id = ? AND side = 'BUY'",
        [account_id],
    ).fetchone()[0]
    return float(buy_value / avg_total / years)


def _round_trip_stats(conn, account_id: str) -> tuple[int, int]:
    """按运行均价匹配卖出，统计已平仓交易的盈利笔数与总笔数。"""
    orders = conn.execute(
        """
        SELECT symbol, side, order_qty, order_price, order_value, fee, order_ts
        FROM account_orders WHERE account_id = ? AND status = 'FILLED'
        ORDER BY order_ts, order_id
        """,
        [account_id],
    ).fetchdf()
    if orders.empty:
        return 0, 0
    cost: dict[str, dict[str, float]] = {}
    wins = closed = 0
    for _, o in orders.iterrows():
        sym = str(o["symbol"])
        qty = float(o["order_qty"] or 0)
        price = float(o["order_price"] or 0)
        fee = float(o["fee"] or 0)
        c = cost.setdefault(sym, {"qty": 0.0, "cost": 0.0})
        if str(o["side"]).upper() == "BUY":
            c["qty"] += qty
            c["cost"] += float(o["order_value"] or 0) + fee
        else:  # SELL
            if c["qty"] <= 0:
                continue
            avg = c["cost"] / c["qty"]
            pnl = (price - avg) * qty - fee
            closed += 1
            wins += 1 if pnl > 0 else 0
            c["qty"] = max(c["qty"] - qty, 0.0)
            c["cost"] = avg * c["qty"]
    return wins, closed
