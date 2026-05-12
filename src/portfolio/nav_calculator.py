"""
净值计算引擎 — 基于纸交易持仓和日线收盘价，逐日滚动计算组合净值。

算法：
  1. 获取所有交易日（全量，不限于交易发生日）
  2. 初始化：现金 = 初始资金，持仓 = {}
  3. 逐日：
     a. 应用当日成交 → 更新持仓和现金
     b. 按收盘价计价持仓
     c. 计算总资产、净值、收益率、回撤
  4. 写入 portfolio_nav
"""
import math
from collections import defaultdict
from datetime import date as DateType
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


def _default_initial_capital(config: dict, market: str) -> float:
    key = "initial_capital_cn" if market == "CN" else "initial_capital_hk"
    return float(config.get("portfolio", {}).get(key, 100000.0))


def calculate(strategy_name: str, initial_capital: Optional[float] = None,
              market: str = "CN") -> pd.DataFrame:
    """
    计算策略逐日净值。

    参数:
        strategy_name: 策略名称
        initial_capital: 初始资金
        market: CN / HK（用于过滤交易日）

    返回:
        DataFrame of NAV history，列名: trade_date, nav, daily_return, cash,
        position_value, total_value, drawdown, sharpe_rolling
    """
    from src.config import load_config
    from src.data_pipeline.loader import get_connection
    from src.portfolio.cashbook import (
        compute_cashflow_adjusted_return,
        ensure_initial_cashflow,
        load_cashflows,
        rebuild_account_daily,
        signed_flow,
    )

    config = load_config()
    initial_capital = initial_capital or _default_initial_capital(config, market)
    conn = get_connection(read_only=True)

    # ---- 1. 加载订单（成交记录） ----
    orders = conn.execute("""
        SELECT po.symbol, po.side, po.order_qty, po.order_price,
               COALESCE(s.execution_date, CAST(po.order_ts AS DATE)) AS trade_date
        FROM paper_orders po
        JOIN signals s ON po.signal_id = s.signal_id
        WHERE s.model_name = ?
        ORDER BY trade_date
    """, [strategy_name]).fetchdf()

    if orders.empty:
        logger.info(f"No orders for {strategy_name}")
        conn.close()
        return pd.DataFrame()

    orders["trade_date"] = pd.to_datetime(orders["trade_date"]).dt.date
    first_order_date = orders["trade_date"].min()
    conn.close()

    ensure_initial_cashflow(initial_capital=initial_capital, flow_date=first_order_date)
    cashflows = load_cashflows()
    start_date = min(first_order_date, cashflows["flow_date"].min()) if not cashflows.empty else first_order_date

    conn = get_connection(read_only=True)
    # 延伸到最新可用行情日期
    country = "CN" if market == "CN" else "HK"
    latest_price_date = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country=?)",
        [country],
    ).fetchone()[0]
    end_date = max(orders["trade_date"].max(), latest_price_date)

    # 获取涉及的所有股票
    symbols = orders["symbol"].unique().tolist()

    # ---- 2. 加载所有交易日 ----
    all_dates = conn.execute("""
        SELECT DISTINCT trade_date FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country=?)
        AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """, [country, start_date, end_date]).fetchdf()
    all_dates["trade_date"] = pd.to_datetime(all_dates["trade_date"]).dt.date
    trading_days = sorted(set(all_dates["trade_date"].tolist()) | set(cashflows["flow_date"].tolist()))

    if not trading_days:
        conn.close()
        logger.warning(f"No trading days found for {strategy_name}")
        return pd.DataFrame()

    # ---- 3. 加载收盘价（全量） ----
    placeholders = ",".join(["?"] * len(symbols))
    prices_df = conn.execute(f"""
        SELECT symbol, trade_date, close
        FROM daily_price
        WHERE symbol IN ({placeholders}) AND trade_date >= ? AND trade_date <= ?
        ORDER BY symbol, trade_date
    """, [*symbols, start_date, end_date]).fetchdf()
    prices_df["trade_date"] = pd.to_datetime(prices_df["trade_date"]).dt.date

    # 收盘价字典: price_map[symbol][date] → close
    prices_df["trade_date"] = pd.to_datetime(prices_df["trade_date"]).dt.date
    price_map: dict[str, dict[DateType, float]] = (
        prices_df.groupby("symbol")
        .apply(lambda g: dict(zip(g["trade_date"], g["close"])), include_groups=False)
        .to_dict()
    )

    conn.close()

    # ---- 4. 按日期整理订单 ----
    order_map: dict[DateType, list[dict]] = defaultdict(list)
    flow_map: dict[DateType, float] = defaultdict(float)
    cost_cfg = config["markets"]["cn" if market == "CN" else "hk"]
    commission = cost_cfg["commission_rate"]          # 佣金率
    stamp = cost_cfg.get("stamp_duty_rate", 0)         # 印花税率（仅卖出）

    for _, order in orders.iterrows():
        odate = order["trade_date"]
        order_map[odate].append({
            "symbol": order["symbol"],
            "side": order["side"],
            "qty": order["order_qty"],
            "price": order["order_price"],
        })

    for _, flow in cashflows.iterrows():
        flow_map[flow["flow_date"]] += signed_flow(flow["flow_type"], flow["amount"])

    # ---- 5. 逐日滚动计算净值 ----
    cash = 0.0
    net_contribution = 0.0
    positions: dict[str, dict] = {}  # positions[symbol] = {qty, avg_cost}
    nav_rows = []
    position_rows = []
    daily_returns = []
    investment_nav = 1.0
    peak_nav = 1.0
    prev_total_value = 0.0   # 用于现金流校正收益率

    cost_min_cn = 5.0      # A 股最低佣金
    cost_min_hk = 10.0     # 港股最低佣金

    for dt in trading_days:
        # 5a. 应用当日外部现金流，再应用成交
        external_flow = flow_map.get(dt, 0.0)
        cash += external_flow
        net_contribution += external_flow

        if dt in order_map:
            for od in order_map[dt]:
                sym = od["symbol"]
                side = od["side"]
                qty = od["qty"]
                price = od["price"]
                value = qty * price

                if side == "BUY":
                    # 计算佣金
                    fee = max(commission * value, cost_min_cn if market == "CN" else cost_min_hk)
                    cash -= (value + fee)

                    if sym in positions:
                        old = positions[sym]
                        new_qty = old["qty"] + qty
                        new_cost = (old["qty"] * old["avg_cost"] + value + fee) / new_qty
                        positions[sym] = {"qty": new_qty, "avg_cost": new_cost}
                    else:
                        positions[sym] = {"qty": qty, "avg_cost": (value + fee) / qty}

                elif side == "SELL":
                    fee = max((commission + stamp) * value, cost_min_cn if market == "CN" else cost_min_hk)
                    cash += (value - fee)

                    if sym in positions:
                        old = positions[sym]
                        remaining = old["qty"] - qty
                        if remaining <= 0:
                            del positions[sym]
                        else:
                            positions[sym] = {"qty": remaining, "avg_cost": old["avg_cost"]}

        # 5b. 按收盘价计价持仓
        position_value = 0.0
        valued_positions = []
        for sym, pos in positions.items():
            close_price = price_map.get(sym, {}).get(dt)
            if close_price is None:
                # 优先用前一日价，都没有则用成本价
                prev_dates = [d for d in price_map.get(sym, {}).keys() if d < dt]
                close_price = price_map[sym][max(prev_dates)] if prev_dates else pos["avg_cost"]
            market_value = pos["qty"] * close_price
            pnl = (close_price - pos["avg_cost"]) * pos["qty"]
            pnl_pct = (close_price / pos["avg_cost"] - 1) if pos["avg_cost"] else 0.0
            position_value += market_value
            valued_positions.append({
                "strategy_name": strategy_name,
                "trade_date": dt,
                "symbol": sym,
                "quantity": float(pos["qty"]),
                "avg_cost": float(pos["avg_cost"]),
                "current_price": float(close_price),
                "market_value": float(market_value),
                "pnl": float(pnl),
                "pnl_pct": float(pnl_pct),
            })

        total_value = cash + position_value
        for row in valued_positions:
            row["weight"] = row["market_value"] / total_value if total_value > 0 else 0.0
            position_rows.append(row)

        # 5c. 计算指标
        daily_ret = compute_cashflow_adjusted_return(prev_total_value, total_value, external_flow)
        prev_total_value = total_value
        daily_returns.append(daily_ret)

        investment_nav *= 1 + daily_ret
        peak_nav = max(peak_nav, investment_nav)
        drawdown = (investment_nav - peak_nav) / peak_nav if peak_nav > 0 else 0.0

        sharpe = 0.0
        if len(daily_returns) >= 20:
            recent = daily_returns[-20:]
            std = np.std(recent)
            if std > 0:
                sharpe = ((np.mean(recent) - 0.03 / 252) / std) * math.sqrt(252)

        nav_rows.append({
            "strategy_name": strategy_name,
            "trade_date": dt,
            "nav": round(investment_nav, 6),
            "daily_return": round(daily_ret, 6),
            "cash": round(cash, 2),
            "position_value": round(position_value, 2),
            "total_value": round(total_value, 2),
            "external_flow": round(external_flow, 2),
            "net_contribution": round(net_contribution, 2),
            "investment_nav": round(investment_nav, 6),
            "drawdown": round(drawdown, 6),
            "sharpe_rolling": round(sharpe, 4),
        })

    # ---- 6. 写入数据库 ----
    if nav_rows:
        conn_w = get_connection()
        df_nav = pd.DataFrame(nav_rows)
        df_pos = pd.DataFrame(position_rows)
        conn_w.execute("DELETE FROM portfolio_nav WHERE strategy_name = ?", [strategy_name])
        conn_w.execute("DELETE FROM paper_positions WHERE strategy_name = ?", [strategy_name])
        conn_w.execute("CREATE OR REPLACE TEMP TABLE _tmp_nav AS SELECT * FROM df_nav")
        conn_w.execute("""
            INSERT OR REPLACE INTO portfolio_nav (
                strategy_name, trade_date, nav, daily_return, cash, position_value,
                total_value, external_flow, net_contribution, investment_nav,
                drawdown, sharpe_rolling
            )
            SELECT strategy_name, trade_date, nav, daily_return, cash, position_value,
                   total_value, external_flow, net_contribution, investment_nav,
                   drawdown, sharpe_rolling
            FROM _tmp_nav
        """)
        if not df_pos.empty:
            conn_w.execute("CREATE OR REPLACE TEMP TABLE _tmp_positions AS SELECT * FROM df_pos")
            conn_w.execute("""
                INSERT OR REPLACE INTO paper_positions (
                    strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
                    market_value, pnl, pnl_pct, weight
                )
                SELECT strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
                       market_value, pnl, pnl_pct, weight
                FROM _tmp_positions
            """)
        conn_w.close()
        rebuild_account_daily()

    logger.info(
        f"NAV {strategy_name}: {len(nav_rows)} 天, "
        f"起始 {nav_rows[0]['nav']:.4f} → 最终 {nav_rows[-1]['nav']:.4f}, "
        f"最大回撤 {min(r['drawdown'] for r in nav_rows)*100:.1f}%"
    )
    return pd.DataFrame(nav_rows)


def get_benchmark_nav(index_code: str = "000300", start_date=None, end_date=None) -> pd.DataFrame:
    """获取基准指数净值（归一化）"""
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)

    conditions = ["index_code = ?"]
    params = [index_code]
    if start_date:
        conditions.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("trade_date <= ?")
        params.append(end_date)

    df = conn.execute(f"""
        SELECT trade_date, close FROM index_daily
        WHERE {' AND '.join(conditions)}
        ORDER BY trade_date
    """, params).fetchdf()
    conn.close()

    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    initial = df["close"].iloc[0]
    df["nav"] = df["close"] / initial
    df["daily_return"] = df["close"].pct_change()
    return df


def calculate_all_strategies() -> dict[str, int]:
    """Rebuild NAV and position snapshots for every strategy with paper orders."""
    from src.data_pipeline.loader import get_connection
    from src.portfolio.cashbook import rebuild_account_daily

    conn = get_connection(read_only=True)
    try:
        rows = conn.execute("""
            SELECT s.model_name, COALESCE(si.country, 'CN') AS market
            FROM paper_orders po
            JOIN signals s ON po.signal_id = s.signal_id
            LEFT JOIN stock_info si ON po.symbol = si.symbol
            GROUP BY s.model_name, si.country
        """).fetchall()
    finally:
        conn.close()

    results = {}
    for strategy_name, market in rows:
        df = calculate(strategy_name, market=market)
        results[strategy_name] = len(df)
    if not rows:
        rebuild_account_daily()
    return results


if __name__ == "__main__":
    results = calculate_all_strategies()
    for name, rows in results.items():
        print(f"{name}: {rows} nav rows")
