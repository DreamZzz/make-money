"""
Paper Trading 引擎 — 信号→订单→持仓。
模拟 T日信号 → T+1日开盘价成交，扣除佣金/印花税。
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

import duckdb
import pandas as pd
from loguru import logger


def _load_config():
    from src.config import load_config
    return load_config()


def _get_next_trading_day(conn: duckdb.DuckDBPyConnection, signal_date: date, market: str) -> Optional[date]:
    """获取信号日之后的下一个交易日"""
    result = conn.execute("""
        SELECT MIN(trade_date) FROM daily_price
        WHERE trade_date > ?
        AND symbol IN (SELECT symbol FROM stock_info WHERE country=?)
    """, [signal_date, market]).fetchone()
    return result[0] if result and result[0] else None


def _get_open_price(conn: duckdb.DuckDBPyConnection, symbol: str, trade_date: date) -> Optional[float]:
    """获取某股票在某日的开盘价"""
    result = conn.execute("""
        SELECT open FROM daily_price
        WHERE symbol = ? AND trade_date = ?
    """, [symbol, trade_date]).fetchone()
    return result[0] if result else None


def _execution_timestamp(conn: duckdb.DuckDBPyConnection, trade_date: date, market: str) -> datetime:
    """Return a realistic serial execution timestamp inside the market session."""
    existing = conn.execute("""
        SELECT COUNT(*) FROM paper_orders WHERE CAST(order_ts AS DATE) = ?
    """, [trade_date]).fetchone()[0]
    session_start = time(9, 30) if market in {"CN", "HK"} else time(9, 30)
    return datetime.combine(trade_date, session_start) + timedelta(seconds=int(existing) * 10)


def _default_initial_capital(config: dict, market: str) -> float:
    key = "initial_capital_cn" if market == "CN" else "initial_capital_hk"
    return float(config.get("portfolio", {}).get(key, 100000.0))


def _side_priority(side: str) -> int:
    normalized = str(side or "").upper()
    if normalized in {"SELL", "SHORT"}:
        return 0
    if normalized == "BUY":
        return 1
    return 9


def _prioritize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """Execution order: release risk/cash first, then deploy new cash."""
    if signals.empty:
        return signals

    ordered = signals.copy()
    ordered["_side_priority"] = ordered["side"].map(_side_priority)
    ordered["_confidence"] = pd.to_numeric(ordered.get("confidence", 0), errors="coerce").fillna(0.0)
    ordered["_score"] = pd.to_numeric(ordered.get("score", 0), errors="coerce").fillna(0.0)
    ordered = ordered.sort_values(
        ["signal_date", "_side_priority", "_confidence", "_score", "symbol"],
        ascending=[True, True, False, False, True],
        kind="mergesort",
    )
    return ordered.drop(columns=["_side_priority", "_confidence", "_score"])


def _latest_position_qty(conn: duckdb.DuckDBPyConnection, strategy_name: str, symbol: str) -> float:
    pos = conn.execute("""
        SELECT quantity FROM paper_positions
        WHERE strategy_name = ? AND symbol = ?
        AND trade_date = (
            SELECT MAX(trade_date) FROM paper_positions
            WHERE strategy_name = ? AND symbol = ?
        )
    """, [strategy_name, symbol, strategy_name, symbol]).fetchone()
    return float(pos[0]) if pos and pos[0] else 0.0


def _mark_signal_handled(
    conn: duckdb.DuckDBPyConnection,
    signal_id: str,
    execution_date: date,
    execution_price: Optional[float] = None,
    status: str = "FILLED",
    status_reason: str = "成交",
) -> None:
    conn.execute("""
        UPDATE signals
        SET executed = TRUE,
            execution_price = ?,
            execution_date = ?,
            status = ?,
            status_reason = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE signal_id = ?
    """, [execution_price, execution_date, status, status_reason, signal_id])


def run(strategy_name: str, initial_capital: Optional[float] = None, market: str = "CN") -> dict:
    """
    执行纸交易：将未执行的信号转换为模拟成交。

    Returns:
        dict with summary stats
    """
    config = _load_config()
    initial_capital = initial_capital or _default_initial_capital(config, market)
    market_key = "cn" if market == "CN" else "hk"
    cost_cfg = config["markets"][market_key]
    portfolio_cfg = config.get("portfolio", {})
    commission = cost_cfg["commission_rate"]
    stamp_duty = cost_cfg.get("stamp_duty_rate", 0)

    from src.data_pipeline.loader import get_connection, init_db
    from src.portfolio.cashbook import get_account_summary, get_available_cash
    from src.signals.lifecycle import expire_stale_signals
    conn = get_connection()
    init_db(conn)
    expire_stale_signals(conn)

    # 1. 获取未执行的信号（按时间排序）
    signals = conn.execute("""
        SELECT signal_id, model_name, symbol, side, signal_ts, score, confidence,
               expected_holding_days, max_position_pct, thesis
        FROM signals
        WHERE model_name = ?
          AND executed = FALSE
          AND COALESCE(status, 'ACTIVE') = 'ACTIVE'
        ORDER BY signal_ts ASC
    """, [strategy_name]).fetchdf()

    if signals.empty:
        logger.info(f"No pending signals for {strategy_name}")
        conn.close()
        return {"executed": 0}

    signals["signal_date"] = pd.to_datetime(signals["signal_ts"]).dt.date
    signals = _prioritize_signals(signals)
    executed = 0
    handled_without_order = 0
    conn.close()

    # 初始化本次执行的全局现金账本（session 内跨信号保持连续）
    account_summary = get_account_summary()
    session_cash = get_available_cash()
    current_total = float(account_summary.get("total_value") or initial_capital)
    conn = get_connection()

    # 2. 逐条处理信号
    for _, sig in signals.iterrows():
        sig_date = sig["signal_date"]
        sym = sig["symbol"]
        side = str(sig["side"] or "").upper()
        order_side = "SELL" if side in {"SELL", "SHORT"} else side

        # 找下一个交易日
        next_day = _get_next_trading_day(conn, sig_date, market)
        if next_day is None:
            logger.warning(f"No trading day after {sig_date} for {sym}")
            continue

        held_qty = 0.0
        if side in {"SELL", "SHORT"}:
            held_qty = _latest_position_qty(conn, strategy_name, sym)
            if held_qty <= 0:
                _mark_signal_handled(
                    conn,
                    sig["signal_id"],
                    next_day,
                    status="NO_ACTION",
                    status_reason="当前无持仓，无需卖出",
                )
                handled_without_order += 1
                logger.info(f"  跳过 {sym} {side}：当前无持仓，信号已按无需动作处理")
                continue

        # 获取成交价格（开盘价）
        price = _get_open_price(conn, sym, next_day)
        if price is None or price <= 0:
            logger.warning(f"No open price for {sym} on {next_day}")
            continue

        # 3. 计算交易量
        max_position = sig["max_position_pct"] if sig["max_position_pct"] else 0.05

        if side == "BUY":
            score = float(sig["score"] or 0)
            confidence = float(sig["confidence"] or 0)
            rank_score = confidence * max(score, 0)
            min_conf = float(portfolio_cfg.get("min_rebalance_buy_confidence", 0.75))
            min_rank = float(portfolio_cfg.get("min_rebalance_buy_rank_score", 0.50))
            if confidence < min_conf or rank_score < min_rank:
                logger.info(
                    f"  跳过 {sym} BUY：低于执行门槛 "
                    f"(confidence={confidence:.2f}, rank_score={rank_score:.2f})"
                )
                continue

            normal_cap = float(portfolio_cfg.get("max_single_position_pct", 0.10))
            overweight_cap = float(portfolio_cfg.get("overweight_single_position_pct", 0.15))
            overweight_min_conf = float(portfolio_cfg.get("overweight_min_confidence", 0.90))
            overweight_min_rank = float(portfolio_cfg.get("overweight_min_rank_score", 0.85))
            allowed_cap = overweight_cap if (
                confidence >= overweight_min_conf and rank_score >= overweight_min_rank
            ) else normal_cap
            max_position = min(float(max_position), allowed_cap)

            # 买入：按 max_position_pct 计算买入数量（A股每手100股，向下取整）
            target_value = current_total * max_position
            lots = int(target_value / price / 100)  # 可买手数
            if lots == 0:
                logger.info(f"  跳过 {sym}：仓位 {max_position*100:.0f}% × 总资产 {current_total:,.0f} = {target_value:,.0f}，不足一手 ({price:.0f}×100={price*100:,.0f})")
                continue
            qty = lots * 100

        elif side in {"SELL", "SHORT"}:
            # 卖出：清仓该标的
            qty = held_qty
        else:
            continue

        execution_value = float(qty * price)
        execution_cost = float(execution_value * (commission + (stamp_duty if order_side == "SELL" else 0)))
        min_fee = 5.0 if market == "CN" else 10.0
        if execution_cost < min_fee:
            execution_cost = min_fee

        # 现金余额校验：BUY 需有足够现金
        cash_before = session_cash
        if order_side == "BUY":
            required = execution_value + execution_cost
            if session_cash < required:
                logger.warning(f"  跳过 {sym} BUY：现金不足 (可用 {session_cash:,.0f} < 需要 {required:,.0f})")
                continue
            session_cash -= required
        else:
            session_cash += execution_value - execution_cost
        cash_after = session_cash

        # 4. 记录成交（更新 paper_orders）
        import uuid
        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        order_ts = _execution_timestamp(conn, next_day, market)
        conn.execute("""
            INSERT INTO paper_orders (
                order_id, signal_id, symbol, side, order_qty, order_price,
                order_value, fee, cash_before, cash_after, order_ts, status, status_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', '成交')
        """, [
            order_id, sig["signal_id"], sym, order_side, qty, price,
            execution_value, execution_cost, cash_before, cash_after, pd.Timestamp(order_ts),
        ])

        # 5. 标记信号已执行
        _mark_signal_handled(conn, sig["signal_id"], next_day, price)

        executed += 1

    conn.close()
    logger.info(
        f"Paper engine: {strategy_name} executed {executed}/{len(signals)} signals "
        f"(handled_without_order={handled_without_order})"
    )
    return {
        "executed": executed,
        "handled_without_order": handled_without_order,
        "pending": len(signals) - executed - handled_without_order,
        "total": len(signals),
    }


def run_all_strategies(initial_capital: Optional[float] = None) -> dict:
    """对所有有信号的策略执行纸交易"""
    from src.data_pipeline.loader import get_connection, init_db
    conn = get_connection()
    init_db(conn)
    rows = conn.execute("""
        SELECT s.model_name,
               COALESCE(si.country, 'CN') AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.executed = FALSE
          AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
        GROUP BY s.model_name, si.country
    """).fetchall()
    conn.close()

    results = {}
    for name, market in rows:
        results[name] = run(name, initial_capital, market)
        if results[name].get("executed", 0) > 0:
            from src.portfolio.cashbook import rebuild_account_daily

            rebuild_account_daily()
    if results:
        from src.portfolio.nav_calculator import calculate_all_strategies

        calculate_all_strategies()
    return results


def backfill_order_audit_fields() -> int:
    """Backfill historical order value/fee/cash fields and realistic timestamps."""
    from src.data_pipeline.loader import get_connection, init_db
    from src.portfolio.cashbook import DEFAULT_ACCOUNT_ID, ensure_initial_cashflow, load_cashflows, signed_flow

    config = _load_config()
    ensure_initial_cashflow(account_id=DEFAULT_ACCOUNT_ID)
    cashflows = load_cashflows(account_id=DEFAULT_ACCOUNT_ID)
    flow_by_date = {}
    if not cashflows.empty:
        for _, row in cashflows.iterrows():
            flow_by_date.setdefault(row["flow_date"], 0.0)
            flow_by_date[row["flow_date"]] += signed_flow(row["flow_type"], row["amount"])

    conn = get_connection()
    try:
        init_db(conn)
        orders = conn.execute("""
            SELECT po.order_id, po.symbol, po.side, po.order_qty, po.order_price,
                   po.order_ts, po.created_at, COALESCE(si.country, 'CN') AS market
            FROM paper_orders po
            LEFT JOIN stock_info si ON po.symbol = si.symbol
            WHERE po.status = 'FILLED'
            ORDER BY CAST(po.order_ts AS DATE), po.order_ts, po.created_at, po.order_id
        """).fetchdf()
        if orders.empty:
            return 0

        orders["trade_date"] = pd.to_datetime(orders["order_ts"]).dt.date
        cash = 0.0
        flow_dates = sorted(flow_by_date)
        applied_flow_dates: set[date] = set()
        per_day_sequence: dict[date, int] = {}
        updated = 0

        for _, order in orders.iterrows():
            trade_date = order["trade_date"]
            for flow_date in flow_dates:
                if flow_date <= trade_date and flow_date not in applied_flow_dates:
                    cash += float(flow_by_date.get(flow_date, 0.0))
                    applied_flow_dates.add(flow_date)

            market = str(order["market"] or "CN")
            side = str(order["side"])
            qty = float(order["order_qty"] or 0)
            price = float(order["order_price"] or 0)
            value = qty * price
            market_key = "cn" if market == "CN" else "hk"
            cost_cfg = config["markets"].get(market_key, config["markets"]["cn"])
            fee_rate = float(cost_cfg.get("commission_rate", 0))
            if side == "SELL":
                fee_rate += float(cost_cfg.get("stamp_duty_rate", 0))
            fee = max(value * fee_rate, 5.0 if market == "CN" else 10.0)

            cash_before = cash
            if side == "BUY":
                cash -= value + fee
            elif side == "SELL":
                cash += value - fee
            cash_after = cash

            order_ts = pd.Timestamp(order["order_ts"]).to_pydatetime()
            if order_ts.time() == time(0, 0):
                seq = per_day_sequence.get(trade_date, 0)
                order_ts = datetime.combine(trade_date, time(9, 30)) + timedelta(seconds=seq * 10)
                per_day_sequence[trade_date] = seq + 1

            reason = "成交"
            if cash_after < 0:
                reason = "成交后现金为负（历史回放）"

            conn.execute("""
                UPDATE paper_orders
                SET order_value = ?, fee = ?, cash_before = ?, cash_after = ?,
                    order_ts = ?, status_reason = COALESCE(status_reason, ?)
                WHERE order_id = ?
            """, [
                value, fee, cash_before, cash_after, pd.Timestamp(order_ts), reason, order["order_id"],
            ])
            updated += 1
        return updated
    finally:
        conn.close()


if __name__ == "__main__":
    backfilled = backfill_order_audit_fields()
    if backfilled:
        print(f"backfilled_order_audit_fields: {backfilled}")
    results = run_all_strategies()
    for name, r in results.items():
        print(f"{name}: {r}")
