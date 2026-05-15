"""Snapshot-based index fund holding performance."""
from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import pandas as pd

PERIODS = {
    "return_1m": 22,
    "return_3m": 66,
    "return_6m": 132,
    "return_12m": 252,
}


def add_snapshot(
    fund_code: str,
    snapshot_date: date,
    shares: float,
    cost_amount: float,
    note: str = "",
) -> str:
    from src.data_pipeline.loader import get_connection, init_db

    if not fund_code:
        raise ValueError("fund_code is required")
    if shares < 0 or cost_amount < 0:
        raise ValueError("shares and cost_amount must be non-negative")

    snapshot_id = f"IFSNAP-{uuid.uuid4().hex[:10].upper()}"
    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("""
            INSERT INTO index_fund_snapshots (
                snapshot_id, snapshot_date, fund_code, shares, cost_amount, note
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, [snapshot_id, snapshot_date, fund_code, float(shares), float(cost_amount), note])
    finally:
        conn.close()
    return snapshot_id


def load_latest_snapshots(conn) -> pd.DataFrame:
    return conn.execute("""
        SELECT snapshot_id, snapshot_date, fund_code, shares, cost_amount, note, created_at
        FROM index_fund_snapshots
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY fund_code ORDER BY snapshot_date DESC, created_at DESC, snapshot_id DESC
        ) = 1
        ORDER BY fund_code
    """).fetchdf()


def load_current_weights(conn) -> dict[str, float]:
    holdings = evaluate_holdings(conn)
    if holdings.empty:
        return {}
    total = float(holdings["market_value"].fillna(0).sum())
    if total <= 0:
        return {}
    return {
        str(row["fund_code"]): float(row["market_value"] or 0) / total
        for _, row in holdings.iterrows()
    }


def evaluate_holdings(conn=None) -> pd.DataFrame:
    """Evaluate latest snapshot positions with latest fund NAV and tracking-index returns."""
    close_conn = False
    if conn is None:
        from src.data_pipeline.loader import get_connection, init_db

        conn = get_connection()
        init_db(conn)
        close_conn = True
    try:
        snapshots = load_latest_snapshots(conn)
        if snapshots.empty:
            return pd.DataFrame(columns=holding_columns())

        fund_info = conn.execute("""
            SELECT fund_code, name, fund_type, tracking_index, market, currency
            FROM fund_info
        """).fetchdf()
        latest_nav = conn.execute("""
            SELECT fund_code, trade_date AS nav_date, COALESCE(close, nav) AS latest_nav
            FROM fund_nav
            QUALIFY ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY trade_date DESC) = 1
        """).fetchdf()
        merged = snapshots.merge(fund_info, on="fund_code", how="left").merge(latest_nav, on="fund_code", how="left")
        rows = []
        for _, row in merged.iterrows():
            fund_code = str(row["fund_code"])
            nav = _safe_float(row.get("latest_nav"))
            shares = _safe_float(row.get("shares"))
            cost = _safe_float(row.get("cost_amount"))
            market_value = shares * nav if nav is not None else np.nan
            holding_return = market_value / cost - 1 if cost > 0 and not pd.isna(market_value) else np.nan
            index_return = _tracking_index_return(conn, row.get("tracking_index"), row.get("snapshot_date"), row.get("nav_date"))
            history = _load_fund_history(conn, fund_code)
            period_returns = compute_period_returns(history)
            rows.append({
                "fund_code": fund_code,
                "name": row.get("name") or fund_code,
                "fund_type": row.get("fund_type"),
                "tracking_index": row.get("tracking_index"),
                "snapshot_date": row.get("snapshot_date"),
                "shares": shares,
                "cost_amount": cost,
                "nav_date": row.get("nav_date"),
                "latest_nav": nav,
                "market_value": market_value,
                "holding_return": holding_return,
                "tracking_index_return": index_return,
                "excess_return": holding_return - index_return if not pd.isna(holding_return) and index_return is not None else np.nan,
                "max_drawdown": compute_max_drawdown(history["close"]) if not history.empty else np.nan,
                **period_returns,
                "note": row.get("note"),
            })
        result = pd.DataFrame(rows)
        total = float(result["market_value"].fillna(0).sum())
        result["current_weight"] = result["market_value"].fillna(0) / total if total > 0 else 0.0
        return result[holding_columns()]
    finally:
        if close_conn:
            conn.close()


def holding_columns() -> list[str]:
    return [
        "fund_code",
        "name",
        "fund_type",
        "tracking_index",
        "snapshot_date",
        "shares",
        "cost_amount",
        "nav_date",
        "latest_nav",
        "market_value",
        "current_weight",
        "holding_return",
        "tracking_index_return",
        "excess_return",
        "max_drawdown",
        "return_1m",
        "return_3m",
        "return_6m",
        "return_12m",
        "note",
    ]


def compute_period_returns(history: pd.DataFrame) -> dict[str, float]:
    if history.empty or len(history) < 2:
        return {key: np.nan for key in PERIODS}
    close = history["close"].dropna().reset_index(drop=True)
    result = {}
    for key, days in PERIODS.items():
        if len(close) <= days:
            result[key] = np.nan
        else:
            result[key] = float(close.iloc[-1] / close.iloc[-days - 1] - 1)
    return result


def compute_max_drawdown(close: pd.Series) -> float:
    values = pd.to_numeric(close, errors="coerce").dropna()
    if values.empty:
        return np.nan
    nav = values / values.iloc[0]
    return float((nav / nav.cummax() - 1).min())


def _load_fund_history(conn, fund_code: str) -> pd.DataFrame:
    return conn.execute("""
        SELECT trade_date, COALESCE(close, nav) AS close
        FROM fund_nav
        WHERE fund_code = ?
        ORDER BY trade_date
    """, [fund_code]).fetchdf()


def _tracking_index_return(conn, index_code, start_date, end_date) -> float | None:
    if not index_code or pd.isna(index_code) or pd.isna(start_date) or pd.isna(end_date):
        return None
    df = conn.execute("""
        SELECT trade_date, close
        FROM index_daily
        WHERE index_code = ? AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """, [str(index_code), start_date, end_date]).fetchdf()
    if len(df) < 2:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)


def _safe_float(value) -> float:
    if value is None or pd.isna(value):
        return np.nan
    return float(value)

