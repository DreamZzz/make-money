"""Global cash account ledger and cash-flow-adjusted account snapshots."""
import uuid
from collections import defaultdict
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


DEFAULT_ACCOUNT_ID = "default"
DEFAULT_CURRENCY = "CNY"


def _load_config() -> dict:
    from src.config import load_config

    return load_config()


def _initial_capital(config: dict) -> float:
    portfolio = config.get("portfolio", {})
    return float(portfolio.get("initial_capital_cn", 100000.0))


def signed_flow(flow_type: str, amount: float) -> float:
    """Return signed cash impact for a cashflow row."""
    amount = float(amount or 0)
    return amount if flow_type == "DEPOSIT" else -amount


def compute_cashflow_adjusted_return(
    previous_total_value: float,
    current_total_value: float,
    external_flow: float,
) -> float:
    """Cash-flow-adjusted return for one period."""
    capital_base = previous_total_value + external_flow
    if capital_base <= 0:
        return 0.0
    return current_total_value / capital_base - 1


def ensure_initial_cashflow(
    account_id: str = DEFAULT_ACCOUNT_ID,
    currency: str = DEFAULT_CURRENCY,
    initial_capital: Optional[float] = None,
    flow_date: Optional[date] = None,
) -> None:
    """Create one system initial deposit when an account has no cashflow rows."""
    from src.data_pipeline.loader import get_connection, init_db

    config = _load_config()
    amount = float(initial_capital if initial_capital is not None else _initial_capital(config))
    conn = get_connection()
    try:
        init_db(conn)
        existing = conn.execute(
            "SELECT COUNT(*) FROM account_cashflows WHERE account_id = ?",
            [account_id],
        ).fetchone()[0]
        if existing == 0 and amount > 0:
            conn.execute("""
                INSERT INTO account_cashflows (
                    flow_id, flow_date, account_id, currency, flow_type, amount, note
                )
                VALUES (?, ?, ?, ?, 'DEPOSIT', ?, 'SYSTEM_INITIAL_CAPITAL')
            """, [f"FLOW-{uuid.uuid4().hex[:10].upper()}", flow_date or date.today(), account_id, currency, amount])
            logger.info(f"Created initial cashflow: account={account_id}, amount={amount:,.2f}")
    finally:
        conn.close()


def add_cashflow(
    flow_date: date,
    flow_type: str,
    amount: float,
    note: str = "",
    account_id: str = DEFAULT_ACCOUNT_ID,
    currency: str = DEFAULT_CURRENCY,
) -> str:
    """Add a DEPOSIT/WITHDRAW cashflow and return flow_id."""
    from src.data_pipeline.loader import get_connection, init_db

    if flow_type not in {"DEPOSIT", "WITHDRAW"}:
        raise ValueError("flow_type must be DEPOSIT or WITHDRAW")
    if amount <= 0:
        raise ValueError("amount must be positive")

    flow_id = f"FLOW-{uuid.uuid4().hex[:10].upper()}"
    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("""
            INSERT INTO account_cashflows (
                flow_id, flow_date, account_id, currency, flow_type, amount, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [flow_id, flow_date, account_id, currency, flow_type, float(amount), note])
    finally:
        conn.close()
    return flow_id


def load_cashflows(account_id: str = DEFAULT_ACCOUNT_ID) -> pd.DataFrame:
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    try:
        df = conn.execute("""
            SELECT flow_id, flow_date, account_id, currency, flow_type, amount, note, created_at
            FROM account_cashflows
            WHERE account_id = ?
            ORDER BY flow_date, created_at, flow_id
        """, [account_id]).fetchdf()
    finally:
        conn.close()
    if not df.empty:
        df["flow_date"] = pd.to_datetime(df["flow_date"]).dt.date
    return df


def get_account_summary(account_id: str = DEFAULT_ACCOUNT_ID) -> dict:
    """Return latest account summary, rebuilding from the ledger if needed."""
    from src.data_pipeline.loader import get_connection, init_db

    ensure_initial_cashflow(account_id=account_id)
    conn = get_connection()
    try:
        init_db(conn)
        latest = conn.execute("""
            SELECT trade_date, cash, position_value, total_value, net_contribution, nav, daily_return, drawdown
            FROM account_daily
            WHERE account_id = ?
            ORDER BY trade_date DESC
            LIMIT 1
        """, [account_id]).fetchone()
    finally:
        conn.close()

    if latest is None:
        rebuild_account_daily(account_id=account_id)
        return get_account_summary(account_id=account_id)

    keys = [
        "trade_date", "cash", "position_value", "total_value",
        "net_contribution", "nav", "daily_return", "drawdown",
    ]
    return dict(zip(keys, latest))


def get_available_cash(account_id: str = DEFAULT_ACCOUNT_ID) -> float:
    return float(get_account_summary(account_id=account_id).get("cash") or 0.0)


def rebuild_account_daily(account_id: str = DEFAULT_ACCOUNT_ID) -> pd.DataFrame:
    """Rebuild global account daily cash, positions, and cash-flow-adjusted NAV."""
    from src.config import load_config
    from src.data_pipeline.loader import get_connection, init_db

    config = load_config()
    conn = get_connection(read_only=True)
    try:
        first_order_date = conn.execute("""
            SELECT MIN(CAST(order_ts AS DATE)) FROM paper_orders WHERE status = 'FILLED'
        """).fetchone()[0]
    finally:
        conn.close()

    ensure_initial_cashflow(account_id=account_id, flow_date=first_order_date or date.today())

    conn = get_connection(read_only=True)
    try:
        cashflows = conn.execute("""
            SELECT flow_date, flow_type, amount
            FROM account_cashflows
            WHERE account_id = ?
            ORDER BY flow_date
        """, [account_id]).fetchdf()
        orders = conn.execute("""
            SELECT po.symbol, po.side, po.order_qty, po.order_price,
                   CAST(po.order_ts AS DATE) AS trade_date,
                   COALESCE(si.country, 'CN') AS market
            FROM paper_orders po
            LEFT JOIN stock_info si ON po.symbol = si.symbol
            WHERE po.status = 'FILLED'
            ORDER BY trade_date, po.created_at, po.order_id
        """).fetchdf()
        latest_price_date = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    finally:
        conn.close()

    if cashflows.empty and orders.empty:
        return pd.DataFrame()

    if not cashflows.empty:
        cashflows["flow_date"] = pd.to_datetime(cashflows["flow_date"]).dt.date
    if not orders.empty:
        orders["trade_date"] = pd.to_datetime(orders["trade_date"]).dt.date

    start_candidates = []
    if not cashflows.empty:
        start_candidates.append(cashflows["flow_date"].min())
    if not orders.empty:
        start_candidates.append(orders["trade_date"].min())
    start_date = min(start_candidates)
    end_date = max([d for d in [latest_price_date, *start_candidates] if d is not None])

    conn = get_connection(read_only=True)
    try:
        trading_days_df = conn.execute("""
            SELECT DISTINCT trade_date FROM daily_price
            WHERE trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """, [start_date, end_date]).fetchdf()
        symbols = orders["symbol"].dropna().unique().tolist() if not orders.empty else []
        if symbols:
            placeholders = ",".join(["?"] * len(symbols))
            prices_df = conn.execute(f"""
                SELECT symbol, trade_date, close FROM daily_price
                WHERE symbol IN ({placeholders}) AND trade_date >= ? AND trade_date <= ?
                ORDER BY symbol, trade_date
            """, [*symbols, start_date, end_date]).fetchdf()
        else:
            prices_df = pd.DataFrame(columns=["symbol", "trade_date", "close"])
    finally:
        conn.close()

    calendar = set(pd.to_datetime(trading_days_df.get("trade_date", pd.Series(dtype=object))).dt.date.tolist())
    calendar.update(start_candidates)
    calendar.update(cashflows["flow_date"].tolist() if not cashflows.empty else [])
    calendar.update(orders["trade_date"].tolist() if not orders.empty else [])
    trading_days = sorted(d for d in calendar if d is not None and start_date <= d <= end_date)

    if not prices_df.empty:
        prices_df["trade_date"] = pd.to_datetime(prices_df["trade_date"]).dt.date
    price_map = (
        prices_df.groupby("symbol")
        .apply(lambda g: dict(zip(g["trade_date"], g["close"])), include_groups=False)
        .to_dict()
        if not prices_df.empty
        else {}
    )

    flow_map = defaultdict(float)
    for _, row in cashflows.iterrows():
        flow_map[row["flow_date"]] += signed_flow(row["flow_type"], row["amount"])

    order_map = defaultdict(list)
    for _, order in orders.iterrows():
        order_map[order["trade_date"]].append(order)

    cash = 0.0
    net_contribution = 0.0
    positions: dict[str, dict] = {}
    prev_total_value = 0.0
    investment_nav = 1.0
    peak_nav = 1.0
    rows = []

    for dt in trading_days:
        external_flow = flow_map.get(dt, 0.0)
        cash += external_flow
        net_contribution += external_flow

        for order in order_map.get(dt, []):
            market_key = "hk" if order["market"] == "HK" else "cn"
            cost_cfg = config["markets"][market_key]
            commission = cost_cfg["commission_rate"]
            stamp = cost_cfg.get("stamp_duty_rate", 0)
            min_fee = 10.0 if order["market"] == "HK" else 5.0
            sym = order["symbol"]
            qty = float(order["order_qty"])
            price = float(order["order_price"])
            value = qty * price
            if order["side"] == "BUY":
                fee = max(commission * value, min_fee)
                cash -= value + fee
                old = positions.get(sym)
                if old:
                    new_qty = old["qty"] + qty
                    new_cost = (old["qty"] * old["avg_cost"] + value + fee) / new_qty
                    positions[sym] = {"qty": new_qty, "avg_cost": new_cost}
                else:
                    positions[sym] = {"qty": qty, "avg_cost": (value + fee) / qty}
            elif order["side"] == "SELL":
                fee = max((commission + stamp) * value, min_fee)
                cash += value - fee
                old = positions.get(sym)
                if old:
                    remaining = old["qty"] - qty
                    if remaining <= 0:
                        positions.pop(sym, None)
                    else:
                        positions[sym] = {"qty": remaining, "avg_cost": old["avg_cost"]}

        position_value = 0.0
        for sym, pos in positions.items():
            close_price = price_map.get(sym, {}).get(dt)
            if close_price is None:
                previous_dates = [d for d in price_map.get(sym, {}) if d < dt]
                close_price = price_map[sym][max(previous_dates)] if previous_dates else pos["avg_cost"]
            position_value += pos["qty"] * close_price

        total_value = cash + position_value
        daily_return = compute_cashflow_adjusted_return(prev_total_value, total_value, external_flow)
        investment_nav *= 1 + daily_return
        peak_nav = max(peak_nav, investment_nav)
        drawdown = (investment_nav - peak_nav) / peak_nav if peak_nav > 0 else 0.0
        rows.append({
            "account_id": account_id,
            "trade_date": dt,
            "cash": round(cash, 2),
            "position_value": round(position_value, 2),
            "total_value": round(total_value, 2),
            "net_contribution": round(net_contribution, 2),
            "daily_external_flow": round(external_flow, 2),
            "nav": round(investment_nav, 6),
            "daily_return": round(daily_return, 6),
            "drawdown": round(drawdown, 6),
        })
        prev_total_value = total_value

    df = pd.DataFrame(rows)
    conn_w = get_connection()
    try:
        init_db(conn_w)
        conn_w.execute("DELETE FROM account_daily WHERE account_id = ?", [account_id])
        if not df.empty:
            conn_w.execute("CREATE OR REPLACE TEMP TABLE _tmp_account_daily AS SELECT * FROM df")
            conn_w.execute("""
                INSERT OR REPLACE INTO account_daily (
                    account_id, trade_date, cash, position_value, total_value,
                    net_contribution, daily_external_flow, nav, daily_return, drawdown
                )
                SELECT account_id, trade_date, cash, position_value, total_value,
                       net_contribution, daily_external_flow, nav, daily_return, drawdown
                FROM _tmp_account_daily
            """)
    finally:
        conn_w.close()

    return df
