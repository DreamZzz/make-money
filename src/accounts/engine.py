"""账户级隔离执行引擎。

每个虚拟账户用自己的有效配置独立套利与执行：
- 信号按账户订阅的模型集过滤；
- 套利去重只在账户内（复用 arbiter._build_decisions 纯函数 + 账户自己的门槛）；
- 在隔离的现金/持仓会话上成交，复用 estimate_buy_execution / check_open_tradeable，
  保证每笔订单的手数与成本机制与现有引擎一致。

不触碰现有 default 链路（paper_engine）；default 账户日后作为一个虚拟账户迁移过来。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.accounts.registry import AccountRecord
from src.config import load_config
from src.portfolio.execution_guards import check_open_tradeable
from src.portfolio.execution_preview import estimate_buy_execution
from src.portfolio.paper_engine import (
    _execution_timestamp,
    _get_next_trading_day,
    _get_open_quote,
)
from src.portfolio.regime_policy import load_latest_regime_policy
from src.signals.arbiter import (
    ACCEPTED,
    _build_decisions,
    _consensus_baselines,
    _load_latest_baseline_predictions,
)


@dataclass
class AccountState:
    cash: float
    positions: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def position_value(self) -> float:
        return float(sum(p.get("market_value", p["qty"] * p.get("price", 0.0)) for p in self.positions.values()))

    def qty(self, symbol: str) -> float:
        return float(self.positions.get(symbol, {}).get("qty", 0.0))


@dataclass(frozen=True)
class AccountRunResult:
    account_id: str
    decisions: int
    accepted: int
    filled: int
    skipped: int
    as_of: date | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "decisions": self.decisions,
            "accepted": self.accepted,
            "filled": self.filled,
            "skipped": self.skipped,
            "as_of": self.as_of,
        }


def rebuild_account_state(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    initial_capital: float,
    up_to_ts: Any | None = None,
) -> AccountState:
    """回放 FILLED account_orders 重建账户现金与持仓（不含当日浮动市值）。"""
    params: list[Any] = [account_id]
    ts_filter = ""
    if up_to_ts is not None:
        ts_filter = "AND order_ts <= ?"
        params.append(up_to_ts)
    orders = conn.execute(
        f"""
        SELECT symbol, side, order_qty, order_price, order_value, fee, order_ts
        FROM account_orders
        WHERE account_id = ? AND status = 'FILLED' {ts_filter}
        ORDER BY order_ts, order_id
        """,
        params,
    ).fetchdf()
    state = AccountState(cash=float(initial_capital))
    for _, o in orders.iterrows():
        sym = str(o["symbol"])
        qty = float(o["order_qty"] or 0)
        value = float(o["order_value"] or 0)
        fee = float(o["fee"] or 0)
        side = str(o["side"]).upper()
        pos = state.positions.setdefault(sym, {"qty": 0.0, "avg_cost": 0.0, "price": float(o["order_price"] or 0)})
        if side == "BUY":
            state.cash -= value + fee
            prev_qty = pos["qty"]
            prev_cost = pos["avg_cost"] * prev_qty
            new_qty = prev_qty + qty
            pos["qty"] = new_qty
            pos["avg_cost"] = (prev_cost + value + fee) / new_qty if new_qty > 0 else 0.0
        else:  # SELL
            state.cash += value - fee
            pos["qty"] = max(pos["qty"] - qty, 0.0)
            if pos["qty"] <= 0:
                pos["avg_cost"] = 0.0
        pos["price"] = float(o["order_price"] or pos.get("price", 0.0))
    # 清掉空仓
    state.positions = {s: p for s, p in state.positions.items() if p["qty"] > 0}
    return state


def _satellite_budget(state: AccountState, eff_config: dict, current_total: float) -> float:
    """按账户 allocation 配置计算可部署 BUY 预算。"""
    alloc = eff_config.get("portfolio", {}).get("allocation", {})
    sat_pct = float(alloc.get("satellite_target_pct", 0.95) or 0.0)
    cash_pct = float(alloc.get("cash_target_pct", 0.0) or 0.0)
    sat_target = current_total * sat_pct
    deployable_by_target = max(sat_target - state.position_value, 0.0)
    deployable_by_cash = max(state.cash - current_total * cash_pct, 0.0)
    return max(min(deployable_by_target, deployable_by_cash), 0.0)


def arbitrate_account_signals(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    eff_config: dict,
    as_of: date | None = None,
) -> pd.DataFrame:
    """用账户自己的配置对其订阅的信号做隔离套利，持久化到 account_decisions。"""
    signals = _load_subscribed_signals(conn, account, as_of=as_of)
    if signals.empty:
        return signals
    baselines = _consensus_baselines(eff_config)
    regime_policy = load_latest_regime_policy(conn, as_of=as_of, config=eff_config)
    baseline_predictions = _load_latest_baseline_predictions(conn, baselines)
    decisions = _build_decisions(signals, baseline_predictions, eff_config, baselines, regime_policy=regime_policy)
    _persist_account_decisions(conn, account.account_id, decisions)
    return decisions


def run_account_forward(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    as_of: date | None = None,
    config: dict | None = None,
) -> AccountRunResult:
    """前向单账户执行：隔离套利 + 在隔离现金/持仓上成交其 ACCEPTED 信号。"""
    base_config = config or load_config()
    eff_config = account.config.effective_config(base_config)

    decisions = arbitrate_account_signals(conn, account, eff_config, as_of=as_of)
    if decisions.empty:
        logger.info(f"[{account.account_id}] 无可套利信号")
        return AccountRunResult(account.account_id, 0, 0, 0, 0, as_of)

    accepted = decisions[decisions["decision"] == ACCEPTED]
    state = rebuild_account_state(conn, account.account_id, account.initial_capital)
    filled, skipped = _execute_accepted(conn, account, accepted, eff_config, state, source="forward")

    return AccountRunResult(
        account_id=account.account_id,
        decisions=len(decisions),
        accepted=len(accepted),
        filled=filled,
        skipped=skipped,
        as_of=as_of,
    )


def _execute_accepted(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    accepted: pd.DataFrame,
    eff_config: dict,
    state: AccountState,
    source: str,
    signal_rows: pd.DataFrame | None = None,
) -> tuple[int, int]:
    """对 ACCEPTED 决策在隔离会话上成交，写 account_orders。返回 (filled, skipped)。

    前向执行从 ``signals`` 表加载信号明细；历史回放传入内存重算的 ``signal_rows``
    （需含 signal_id/symbol/side/signal_ts/score/confidence/max_position_pct/market）。
    """
    if accepted.empty:
        return 0, 0
    if signal_rows is None:
        signal_rows = _load_signal_rows(conn, [str(s) for s in accepted["signal_id"].tolist()])
    if signal_rows.empty:
        return 0, 0
    signal_rows = signal_rows.copy()
    # 排序：SELL/风险释放优先，再按优先级
    priority = {sid: float(sc) for sid, sc in zip(accepted["signal_id"], accepted.get("priority_score", 0))}
    signal_rows["_pri"] = signal_rows["signal_id"].map(lambda s: priority.get(str(s), 0.0))
    signal_rows["_sell_first"] = signal_rows["side"].str.upper().isin(["SELL", "SHORT"]).astype(int)
    signal_rows = signal_rows.sort_values(["_sell_first", "_pri"], ascending=[False, False])

    portfolio_cfg = eff_config.get("portfolio", {})
    max_positions = int(portfolio_cfg.get("max_stock_positions", 10) or 10)
    current_total = state.cash + state.position_value
    sat_budget = _satellite_budget(state, eff_config, current_total)
    handled: set[tuple[str, str, date]] = set()
    filled = skipped = 0

    for _, sig in signal_rows.iterrows():
        sym = str(sig["symbol"])
        side = str(sig["side"] or "").upper()
        order_side = "SELL" if side in {"SELL", "SHORT"} else "BUY"
        market = str(sig.get("market") or account.market or "CN")
        sig_date = pd.to_datetime(sig["signal_ts"]).date()
        next_day = _get_next_trading_day(conn, sig_date, market)
        if next_day is None:
            skipped += 1
            continue
        key = (sym, order_side, next_day)
        if key in handled:
            continue

        quote = _get_open_quote(conn, sym, next_day)
        price = quote.get("open") if quote else None
        if not price or price <= 0:
            skipped += 1
            continue
        if not check_open_tradeable(
            price, quote.get("pre_close"), market=market,
            is_st=quote.get("is_st"), is_suspended=quote.get("is_suspended"),
        ).tradeable:
            skipped += 1
            handled.add(key)
            continue

        market_key = "cn" if market == "CN" else "hk"
        cost_cfg = eff_config["markets"].get(market_key, eff_config["markets"]["cn"])
        commission = float(cost_cfg["commission_rate"])
        stamp_duty = float(cost_cfg.get("stamp_duty_rate", 0))
        min_fee = 5.0 if market == "CN" else 10.0

        if order_side == "BUY":
            held = state.qty(sym)
            if held <= 0 and len(state.positions) >= max_positions:
                skipped += 1
                handled.add(key)
                continue
            max_position = float(sig.get("max_position_pct") or 0.05)
            cap = float(portfolio_cfg.get("max_single_position_pct", 0.10) or 0.10)
            max_position = min(max_position, cap)
            preview = estimate_buy_execution(
                conn=conn, symbol=sym, trade_date=next_day, current_total=current_total,
                max_position_pct=max_position, available_cash=state.cash,
                satellite_budget=sat_budget, market=market, price=price,
                commission_rate=commission, min_fee=min_fee,
            )
            qty = int(preview["rounded_qty"])
            if qty <= 0 or preview["status"] != "EXECUTABLE":
                skipped += 1
                handled.add(key)
                continue
            value = qty * price
            fee = max(value * commission, min_fee)
            required = value + fee
            if required > state.cash + 1e-9:
                skipped += 1
                continue
            state.cash -= required
            sat_budget = max(sat_budget - required, 0.0)
            _apply_buy(state, sym, qty, value, fee, price)
        else:  # SELL
            qty = state.qty(sym)
            if qty <= 0:
                handled.add(key)
                continue
            value = qty * price
            fee = max(value * (commission + stamp_duty), min_fee)
            state.cash += value - fee
            state.positions.pop(sym, None)

        _write_order(conn, account.account_id, sig.get("signal_id"), sym, order_side, qty, price,
                     value, fee, _execution_timestamp(conn, next_day, market), source)
        handled.add(key)
        filled += 1

    return filled, skipped


def _apply_buy(state: AccountState, symbol: str, qty: float, value: float, fee: float, price: float) -> None:
    pos = state.positions.setdefault(symbol, {"qty": 0.0, "avg_cost": 0.0, "price": price})
    prev_qty = pos["qty"]
    prev_cost = pos["avg_cost"] * prev_qty
    new_qty = prev_qty + qty
    pos["qty"] = new_qty
    pos["avg_cost"] = (prev_cost + value + fee) / new_qty if new_qty > 0 else 0.0
    pos["price"] = price


def _write_order(
    conn, account_id, signal_id, symbol, side, qty, price, value, fee, order_ts, source,
) -> None:
    conn.execute(
        """
        INSERT INTO account_orders (
            account_id, order_id, signal_id, symbol, side, order_qty, order_price,
            order_value, fee, order_ts, source, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED')
        """,
        [
            account_id, f"VA-{uuid.uuid4().hex[:10].upper()}", signal_id, symbol, side,
            float(qty), float(price), float(value), float(fee), order_ts, source,
        ],
    )


def _load_subscribed_signals(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    as_of: date | None = None,
) -> pd.DataFrame:
    params: list[Any] = []
    filters = ["s.executed = FALSE", "COALESCE(s.status, 'ACTIVE') = 'ACTIVE'"]
    if account.config.models:
        placeholders = ",".join(["?"] * len(account.config.models))
        filters.append(f"s.model_name IN ({placeholders})")
        params.extend(account.config.models)
    if as_of is not None:
        filters.append("CAST(s.signal_ts AS DATE) <= ?")
        params.append(as_of)
    where = " AND ".join(filters)
    return conn.execute(
        f"""
        SELECT s.signal_id, s.model_name, s.model_version, s.symbol, s.side, s.signal_ts,
               s.score, s.confidence, COALESCE(si.country, 'CN') AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE {where}
        ORDER BY s.signal_ts ASC, s.model_name, s.symbol
        """,
        params,
    ).fetchdf()


def _load_signal_rows(conn: duckdb.DuckDBPyConnection, signal_ids: list[str]) -> pd.DataFrame:
    if not signal_ids:
        return pd.DataFrame()
    placeholders = ",".join(["?"] * len(signal_ids))
    return conn.execute(
        f"""
        SELECT s.signal_id, s.symbol, s.side, s.signal_ts, s.score, s.confidence,
               s.max_position_pct, COALESCE(si.country, 'CN') AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.signal_id IN ({placeholders})
        """,
        signal_ids,
    ).fetchdf()


def _persist_account_decisions(conn, account_id: str, decisions: pd.DataFrame) -> None:
    if decisions.empty:
        return
    rows = []
    for _, d in decisions.iterrows():
        rows.append({
            "account_id": account_id,
            "decision_id": f"{account_id}-{d.get('signal_id')}",
            "signal_id": d.get("signal_id"),
            "decision_date": date.today(),
            "model_name": d.get("model_name"),
            "symbol": d.get("symbol"),
            "side": d.get("side"),
            "decision": d.get("decision"),
            "decision_reason": d.get("decision_reason"),
            "consensus_status": d.get("consensus_status"),
            "priority_score": d.get("priority_score"),
        })
    df = pd.DataFrame(rows)
    conn.execute(
        "DELETE FROM account_decisions WHERE account_id = ? AND signal_id IN "
        "(SELECT signal_id FROM df)",
        [account_id],
    )
    conn.register("df", df)
    conn.execute(
        """
        INSERT INTO account_decisions (
            account_id, decision_id, signal_id, decision_date, model_name, symbol,
            side, decision, decision_reason, consensus_status, priority_score
        )
        SELECT account_id, decision_id, signal_id, decision_date, model_name, symbol,
               side, decision, decision_reason, consensus_status, priority_score
        FROM df
        """
    )
    conn.unregister("df")


def run_all_accounts_forward(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
    config: dict | None = None,
    status: str = "ACTIVE",
) -> list[AccountRunResult]:
    """对所有指定状态的账户做前向隔离执行（每日收盘调用）。"""
    from src.accounts.registry import list_accounts

    results = []
    for account in list_accounts(conn, status=status):
        results.append(run_account_forward(conn, account, as_of=as_of, config=config))
    return results
