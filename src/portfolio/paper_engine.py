"""
Paper Trading 引擎 — 信号→订单→持仓。
模拟 T日信号 → T+1日开盘价成交，扣除佣金/印花税。
"""
from datetime import date, timedelta
from typing import Optional

import duckdb
import pandas as pd
import yaml
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


def _get_close_price(conn: duckdb.DuckDBPyConnection, symbol: str, trade_date: date) -> Optional[float]:
    result = conn.execute("""
        SELECT close FROM daily_price WHERE symbol = ? AND trade_date = ?
    """, [symbol, trade_date]).fetchone()
    return result[0] if result else None


def run(strategy_name: str, initial_capital: float = 100000.0, market: str = "CN") -> dict:
    """
    执行纸交易：将未执行的信号转换为模拟成交。

    Returns:
        dict with summary stats
    """
    config = _load_config()
    market_key = "cn" if market == "CN" else "hk"
    cost_cfg = config["markets"][market_key]
    commission = cost_cfg["commission_rate"]
    stamp_duty = cost_cfg.get("stamp_duty_rate", 0)

    from src.data_pipeline.loader import get_connection
    conn = get_connection()

    # 1. 获取未执行的信号（按时间排序）
    signals = conn.execute("""
        SELECT signal_id, model_name, symbol, side, signal_ts, score, confidence,
               expected_holding_days, max_position_pct, thesis
        FROM signals
        WHERE model_name = ? AND executed = FALSE
        ORDER BY signal_ts ASC
    """, [strategy_name]).fetchdf()

    if signals.empty:
        logger.info(f"No pending signals for {strategy_name}")
        conn.close()
        return {"executed": 0}

    signals["signal_date"] = pd.to_datetime(signals["signal_ts"]).dt.date
    executed = 0

    # 初始化本次执行的现金账本（session 内跨信号保持连续）
    nav_row = conn.execute("""
        SELECT cash FROM portfolio_nav
        WHERE strategy_name = ? ORDER BY trade_date DESC LIMIT 1
    """, [strategy_name]).fetchone()
    session_cash = nav_row[0] if nav_row else initial_capital

    # 2. 逐条处理信号
    for _, sig in signals.iterrows():
        sig_date = sig["signal_date"]
        sym = sig["symbol"]
        side = sig["side"]

        # 找下一个交易日
        next_day = _get_next_trading_day(conn, sig_date, market)
        if next_day is None:
            logger.warning(f"No trading day after {sig_date} for {sym}")
            continue

        # 获取成交价格（开盘价）
        price = _get_open_price(conn, sym, next_day)
        if price is None or price <= 0:
            logger.warning(f"No open price for {sym} on {next_day}")
            continue

        # 3. 计算交易量
        max_position = sig["max_position_pct"] if sig["max_position_pct"] else 0.05

        # 获取当前总资产
        nav_row = conn.execute("""
            SELECT total_value FROM portfolio_nav
            WHERE strategy_name = ? ORDER BY trade_date DESC LIMIT 1
        """, [strategy_name]).fetchone()
        current_total = nav_row[0] if nav_row else initial_capital

        if side == "BUY":
            # 买入：按 max_position_pct 计算买入数量（A股每手100股，向下取整）
            target_value = current_total * max_position
            lots = int(target_value / price / 100)  # 可买手数
            if lots == 0:
                logger.info(f"  跳过 {sym}：仓位 {max_position*100:.0f}% × 总资产 {current_total:,.0f} = {target_value:,.0f}，不足一手 ({price:.0f}×100={price*100:,.0f})")
                continue
            qty = lots * 100

        elif side == "SELL":
            # 卖出：清仓该标的
            pos = conn.execute("""
                SELECT quantity FROM paper_positions
                WHERE strategy_name = ? AND symbol = ?
                AND trade_date = (SELECT MAX(trade_date) FROM paper_positions WHERE strategy_name = ? AND symbol = ?)
            """, [strategy_name, sym, strategy_name, sym]).fetchone()
            qty = pos[0] if pos else 0
            if qty == 0:
                continue
            cost = qty * price * (commission + stamp_duty)
        else:
            continue

        execution_value = qty * price
        execution_cost = execution_value * (commission + (stamp_duty if side == "SELL" else 0))
        if execution_cost < 5:
            execution_cost = 5

        # 现金余额校验：BUY 需有足够现金
        if side == "BUY":
            required = execution_value + execution_cost
            if session_cash < required:
                logger.warning(f"  跳过 {sym} BUY：现金不足 (可用 {session_cash:,.0f} < 需要 {required:,.0f})")
                continue
            session_cash -= required
        else:
            session_cash += execution_value - execution_cost

        # 4. 记录成交（更新 paper_orders）
        import uuid
        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        conn.execute("""
            INSERT INTO paper_orders (order_id, signal_id, symbol, side, order_qty, order_price, order_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'FILLED')
        """, [order_id, sig["signal_id"], sym, side, qty, price, pd.Timestamp(next_day)])

        # 5. 标记信号已执行
        conn.execute("""
            UPDATE signals SET executed = TRUE, execution_price = ?, execution_date = ?
            WHERE signal_id = ?
        """, [price, next_day, sig["signal_id"]])

        # 6. 更新持仓
        # 获取现有持仓
        existing = conn.execute("""
            SELECT quantity, avg_cost FROM paper_positions
            WHERE strategy_name = ? AND symbol = ? AND trade_date = (
                SELECT MAX(trade_date) FROM paper_positions WHERE strategy_name = ? AND symbol = ?
            )
        """, [strategy_name, sym, strategy_name, sym]).fetchone()

        if side == "BUY":
            if existing:
                old_qty, old_cost = existing
                new_qty = old_qty + qty
                new_cost = (old_qty * old_cost + execution_value + execution_cost) / new_qty
            else:
                new_qty = qty
                new_cost = (execution_value + execution_cost) / qty

            conn.execute("""
                INSERT INTO paper_positions (strategy_name, trade_date, symbol, quantity, avg_cost, current_price)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [strategy_name, next_day, sym, new_qty, new_cost, price])

        elif side == "SELL":
            # 减仓或清仓
            if existing:
                old_qty, old_cost = existing
                new_qty = max(0, old_qty - qty)
                if new_qty > 0:
                    conn.execute("""
                        INSERT INTO paper_positions (strategy_name, trade_date, symbol, quantity, avg_cost, current_price)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, [strategy_name, next_day, sym, new_qty, old_cost, price])
                else:
                    # 清仓：记录零持仓
                    conn.execute("""
                        INSERT INTO paper_positions (strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value, pnl, pnl_pct)
                        VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0)
                    """, [strategy_name, next_day, sym, price])

        executed += 1

    conn.close()
    logger.info(f"Paper engine: {strategy_name} executed {executed}/{len(signals)} signals")
    return {"executed": executed, "pending": len(signals) - executed, "total": len(signals)}


def run_all_strategies(initial_capital: float = 100000.0) -> dict:
    """对所有有信号的策略执行纸交易"""
    from src.data_pipeline.loader import get_connection
    conn = get_connection(read_only=True)
    rows = conn.execute("""
        SELECT s.model_name,
               COALESCE(si.country, 'CN') AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.executed = FALSE
        GROUP BY s.model_name, si.country
    """).fetchall()
    conn.close()

    results = {}
    for name, market in rows:
        results[name] = run(name, initial_capital, market)
    return results


if __name__ == "__main__":
    results = run_all_strategies()
    for name, r in results.items():
        print(f"{name}: {r}")
