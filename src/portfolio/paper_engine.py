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
        WITH trading_days AS (
            SELECT dp.trade_date
            FROM daily_price dp
            JOIN stock_info si ON dp.symbol = si.symbol
            WHERE dp.trade_date > ? AND si.country = ?
            UNION
            SELECT ms.trade_date
            FROM market_snapshot ms
            WHERE ms.trade_date > ? AND ms.market = ?
        )
        SELECT MIN(trade_date) FROM trading_days
    """, [signal_date, market, signal_date, market]).fetchone()
    return result[0] if result and result[0] else None


def _get_open_price(conn: duckdb.DuckDBPyConnection, symbol: str, trade_date: date) -> Optional[float]:
    """获取某股票在某日的开盘价，盘中优先使用快照表，日线作为兜底。"""
    result = conn.execute("""
        SELECT open
        FROM (
            SELECT open, 1 AS priority
            FROM market_snapshot
            WHERE symbol = ? AND trade_date = ? AND open IS NOT NULL
            UNION ALL
            SELECT open, 2 AS priority
            FROM daily_price
            WHERE symbol = ? AND trade_date = ? AND open IS NOT NULL
        )
        ORDER BY priority
        LIMIT 1
    """, [symbol, trade_date, symbol, trade_date]).fetchone()
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
    sort_cols = ["_side_priority", "signal_date", "_confidence", "_score"]
    ascending = [True, True, False, False]
    if "model_name" in ordered.columns:
        sort_cols.append("model_name")
        ascending.append(True)
    sort_cols.append("symbol")
    ascending.append(True)
    ordered = ordered.sort_values(sort_cols, ascending=ascending, kind="mergesort")
    return ordered.drop(columns=["_side_priority", "_confidence", "_score"])


def _latest_position_qty(conn: duckdb.DuckDBPyConnection, strategy_name: str, symbol: str) -> float:
    pos = conn.execute("""
        SELECT quantity FROM paper_positions
        WHERE strategy_name = ? AND symbol = ?
        ORDER BY trade_date DESC
        LIMIT 1
    """, [strategy_name, symbol]).fetchone()
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


def _load_pending_signals(
    conn: duckdb.DuckDBPyConnection,
    strategy_name: Optional[str],
    default_market: str,
) -> pd.DataFrame:
    params: list[object] = [default_market]
    strategy_filter = ""
    if strategy_name:
        strategy_filter = "AND s.model_name = ?"
        params.append(strategy_name)
    signals = conn.execute(f"""
        SELECT s.signal_id, s.model_name, s.symbol, s.side, s.signal_ts, s.score, s.confidence,
               s.expected_holding_days, s.max_position_pct, s.thesis,
               COALESCE(si.country, ?) AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.executed = FALSE
          AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
          {strategy_filter}
        ORDER BY s.signal_ts ASC
    """, params).fetchdf()
    if signals.empty:
        return signals
    signals["signal_date"] = pd.to_datetime(signals["signal_ts"]).dt.date
    return _prioritize_signals(signals)


def _new_result(total: int = 0) -> dict:
    return {
        "executed": 0,
        "handled_without_order": 0,
        "pending": total,
        "total": total,
        "skipped_no_price": 0,
        "skipped_no_trading_day": 0,
        "skipped_cash": 0,
        "skipped_threshold": 0,
        "skipped_lot": 0,
    }


def _finalize_results(results: dict[str, dict]) -> dict[str, dict]:
    for result in results.values():
        result["pending"] = (
            result["total"]
            - result["executed"]
            - result["handled_without_order"]
        )
    return results


def _run_signal_batch(
    strategy_name: Optional[str],
    initial_capital: Optional[float] = None,
    market: str = "CN",
) -> dict[str, dict]:
    """Execute pending signals in one transaction and one serial cash session."""
    config = _load_config()
    initial_capital = initial_capital or _default_initial_capital(config, market)
    portfolio_cfg = config.get("portfolio", {})

    from src.data_pipeline.loader import get_connection, init_db
    from src.portfolio.cashbook import get_account_summary, get_available_cash
    from src.signals.lifecycle import expire_stale_signals

    account_summary = get_account_summary()
    session_cash = get_available_cash()
    current_total = float(account_summary.get("total_value") or initial_capital)

    conn = get_connection()
    in_transaction = False
    try:
        init_db(conn)
        conn.execute("BEGIN TRANSACTION")
        in_transaction = True
        expire_stale_signals(conn)
        signals = _load_pending_signals(conn, strategy_name, market)

        if signals.empty:
            logger.info(f"No pending signals for {strategy_name or 'all strategies'}")
            conn.execute("COMMIT")
            in_transaction = False
            return {}

        results: dict[str, dict] = {}
        position_cache: dict[tuple[str, str], float] = {}

        for _, sig in signals.iterrows():
            sig_date = sig["signal_date"]
            sym = str(sig["symbol"])
            strategy = str(sig["model_name"])
            signal_market = str(sig.get("market") or market or "CN")
            side = str(sig["side"] or "").upper()
            order_side = "SELL" if side in {"SELL", "SHORT"} else side
            stats = results.setdefault(strategy, _new_result())
            stats["total"] += 1

            next_day = _get_next_trading_day(conn, sig_date, signal_market)
            if next_day is None:
                stats["skipped_no_trading_day"] += 1
                logger.warning(f"No trading day after {sig_date} for {sym}")
                continue

            held_qty = 0.0
            if side in {"SELL", "SHORT"}:
                cache_key = (strategy, sym)
                if cache_key not in position_cache:
                    position_cache[cache_key] = _latest_position_qty(conn, strategy, sym)
                held_qty = position_cache[cache_key]
                if held_qty <= 0:
                    _mark_signal_handled(
                        conn,
                        sig["signal_id"],
                        next_day,
                        status="NO_ACTION",
                        status_reason="当前无持仓，无需卖出",
                    )
                    stats["handled_without_order"] += 1
                    logger.info(f"  跳过 {sym} {side}：当前无持仓，信号已按无需动作处理")
                    continue

            price = _get_open_price(conn, sym, next_day)
            if price is None or price <= 0:
                stats["skipped_no_price"] += 1
                logger.warning(f"No open price for {sym} on {next_day}")
                continue

            max_position = sig["max_position_pct"] if sig["max_position_pct"] else 0.05

            if side == "BUY":
                score = float(sig["score"] or 0)
                confidence = float(sig["confidence"] or 0)
                rank_score = confidence * max(score, 0)
                min_conf = float(portfolio_cfg.get("min_rebalance_buy_confidence", 0.75))
                min_rank = float(portfolio_cfg.get("min_rebalance_buy_rank_score", 0.50))
                if confidence < min_conf or rank_score < min_rank:
                    stats["skipped_threshold"] += 1
                    _mark_signal_handled(
                        conn,
                        sig["signal_id"],
                        next_day,
                        status="NO_ACTION",
                        status_reason=(
                            f"低于执行门槛: confidence={confidence:.2f}, "
                            f"rank_score={rank_score:.2f}"
                        ),
                    )
                    stats["handled_without_order"] += 1
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

                target_value = current_total * max_position
                lots = int(target_value / price / 100)
                if lots == 0:
                    stats["skipped_lot"] += 1
                    reason = (
                        f"不足一手: 仓位 {max_position*100:.0f}% × 总资产 {current_total:,.0f} = "
                        f"{target_value:,.0f}，不足一手 ({price:.0f}×100={price*100:,.0f})"
                    )
                    _mark_signal_handled(
                        conn,
                        sig["signal_id"],
                        next_day,
                        status="NO_ACTION",
                        status_reason=reason,
                    )
                    stats["handled_without_order"] += 1
                    logger.info(
                        f"  跳过 {sym}：仓位 {max_position*100:.0f}% × 总资产 {current_total:,.0f} = "
                        f"{target_value:,.0f}，不足一手 ({price:.0f}×100={price*100:,.0f})"
                    )
                    continue
                qty = lots * 100

            elif side in {"SELL", "SHORT"}:
                qty = held_qty
            else:
                continue

            market_key = "cn" if signal_market == "CN" else "hk"
            cost_cfg = config["markets"].get(market_key, config["markets"]["cn"])
            commission = float(cost_cfg["commission_rate"])
            stamp_duty = float(cost_cfg.get("stamp_duty_rate", 0))
            execution_value = float(qty * price)
            execution_cost = float(execution_value * (commission + (stamp_duty if order_side == "SELL" else 0)))
            min_fee = 5.0 if signal_market == "CN" else 10.0
            if execution_cost < min_fee:
                execution_cost = min_fee

            cash_before = session_cash
            if order_side == "BUY":
                required = execution_value + execution_cost
                if session_cash < required:
                    stats["skipped_cash"] += 1
                    logger.warning(f"  跳过 {sym} BUY：现金不足 (可用 {session_cash:,.0f} < 需要 {required:,.0f})")
                    continue
                session_cash -= required
            else:
                session_cash += execution_value - execution_cost
                position_cache[(strategy, sym)] = 0.0
            cash_after = session_cash

            import uuid
            order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
            order_ts = _execution_timestamp(conn, next_day, signal_market)
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

            _mark_signal_handled(conn, sig["signal_id"], next_day, price)
            stats["executed"] += 1

        conn.execute("COMMIT")
        in_transaction = False
        results = _finalize_results(results)
        for name, result in results.items():
            logger.info(
                f"Paper engine: {name} executed {result['executed']}/{result['total']} signals "
                f"(handled_without_order={result['handled_without_order']}, "
                f"no_price={result['skipped_no_price']}, cash={result['skipped_cash']})"
            )
        return results
    except Exception:
        if in_transaction:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        conn.close()


def run(strategy_name: str, initial_capital: Optional[float] = None, market: str = "CN") -> dict:
    """
    执行单个策略的纸交易：将未执行的信号转换为模拟成交。

    Returns:
        dict with summary stats
    """
    results = _run_signal_batch(strategy_name, initial_capital, market)
    return results.get(strategy_name, {"executed": 0})


def run_all_strategies(initial_capital: Optional[float] = None) -> dict:
    """对所有有信号的策略执行纸交易，共享一个卖出优先的串行现金会话。"""
    results = _run_signal_batch(None, initial_capital, "CN")
    if any(result.get("executed", 0) > 0 for result in results.values()):
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
