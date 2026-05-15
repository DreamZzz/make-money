"""Core-satellite account-level allocation planner."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pandas as pd

from src.config import load_config

DEFAULT_ACCOUNT_ID = "default"


@dataclass(frozen=True)
class AllocationConfig:
    enabled: bool = True
    core_target_pct: float = 0.60
    satellite_target_pct: float = 0.40
    rebalance_tolerance_pct: float = 0.05
    min_trade_amount: float = 1000.0
    core_cash_priority: bool = True

    @classmethod
    def from_app_config(cls, config: dict[str, Any] | None = None) -> AllocationConfig:
        app_config = config or load_config()
        raw = app_config.get("portfolio", {}).get("allocation", {})
        if not raw:
            return cls(enabled=False)
        return cls(
            enabled=bool(raw.get("enabled", True)),
            core_target_pct=float(raw.get("core_target_pct", 0.60)),
            satellite_target_pct=float(raw.get("satellite_target_pct", 0.40)),
            rebalance_tolerance_pct=float(raw.get("rebalance_tolerance_pct", 0.05)),
            min_trade_amount=float(raw.get("min_trade_amount", 1000.0)),
            core_cash_priority=bool(raw.get("core_cash_priority", True)),
        )

    def normalized(self) -> AllocationConfig:
        core = max(float(self.core_target_pct or 0), 0.0)
        satellite = max(float(self.satellite_target_pct or 0), 0.0)
        total = core + satellite
        if total <= 0:
            core, satellite = 0.60, 0.40
        elif abs(total - 1.0) > 1e-9:
            core, satellite = core / total, satellite / total
        return AllocationConfig(
            enabled=self.enabled,
            core_target_pct=core,
            satellite_target_pct=satellite,
            rebalance_tolerance_pct=max(float(self.rebalance_tolerance_pct or 0), 0.0),
            min_trade_amount=max(float(self.min_trade_amount or 0), 0.0),
            core_cash_priority=self.core_cash_priority,
        )


@dataclass(frozen=True)
class AllocationInputs:
    plan_date: date
    account_id: str = DEFAULT_ACCOUNT_ID
    cash: float = 0.0
    core_value: float = 0.0
    satellite_value: float = 0.0
    total_value: float | None = None


@dataclass(frozen=True)
class AllocationPlanItem:
    plan_id: str
    sleeve: str
    instrument_type: str
    instrument_id: str
    action: str
    current_value: float
    target_value: float
    budget_delta: float
    priority: int
    reason: str

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class AllocationPlan:
    plan_id: str
    plan_date: date
    account_id: str
    total_value: float
    cash: float
    core_target_pct: float
    satellite_target_pct: float
    core_value: float
    satellite_value: float
    core_budget: float
    satellite_budget: float
    core_drift_pct: float
    satellite_drift_pct: float
    status: str
    items: list[AllocationPlanItem] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_date": self.plan_date,
            "account_id": self.account_id,
            "total_value": self.total_value,
            "cash": self.cash,
            "core_target_pct": self.core_target_pct,
            "satellite_target_pct": self.satellite_target_pct,
            "core_value": self.core_value,
            "satellite_value": self.satellite_value,
            "core_budget": self.core_budget,
            "satellite_budget": self.satellite_budget,
            "core_drift_pct": self.core_drift_pct,
            "satellite_drift_pct": self.satellite_drift_pct,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, Any]:
        row = self.to_row()
        row["items"] = [item.to_row() for item in self.items]
        return row


def compute_allocation_plan(inputs: AllocationInputs, config: AllocationConfig | None = None) -> AllocationPlan:
    cfg = (config or AllocationConfig()).normalized()
    account_id = str(inputs.account_id or DEFAULT_ACCOUNT_ID)
    cash = max(float(inputs.cash or 0.0), 0.0)
    core_value = max(float(inputs.core_value or 0.0), 0.0)
    satellite_value = max(float(inputs.satellite_value or 0.0), 0.0)
    total_value = float(inputs.total_value) if inputs.total_value is not None else cash + core_value + satellite_value
    total_value = max(total_value, 0.0)

    plan_id = _plan_id(account_id, inputs.plan_date)
    if total_value <= 0 or not cfg.enabled:
        return AllocationPlan(
            plan_id=plan_id,
            plan_date=inputs.plan_date,
            account_id=account_id,
            total_value=total_value,
            cash=cash,
            core_target_pct=cfg.core_target_pct,
            satellite_target_pct=cfg.satellite_target_pct,
            core_value=core_value,
            satellite_value=satellite_value,
            core_budget=0.0,
            satellite_budget=0.0,
            core_drift_pct=0.0,
            satellite_drift_pct=0.0,
            status="DISABLED" if not cfg.enabled else "EMPTY",
            items=[],
        )

    core_target_value = total_value * cfg.core_target_pct
    satellite_target_value = total_value * cfg.satellite_target_pct
    core_drift_pct = core_value / total_value - cfg.core_target_pct
    satellite_drift_pct = satellite_value / total_value - cfg.satellite_target_pct
    values = {
        "core": {
            "current": core_value,
            "target": core_target_value,
            "drift": core_drift_pct,
            "target_pct": cfg.core_target_pct,
        },
        "satellite": {
            "current": satellite_value,
            "target": satellite_target_value,
            "drift": satellite_drift_pct,
            "target_pct": cfg.satellite_target_pct,
        },
    }
    budgets = {"core": 0.0, "satellite": 0.0}
    remaining_cash = cash
    order = ["core", "satellite"] if cfg.core_cash_priority else ["satellite", "core"]
    for sleeve in order:
        value = values[sleeve]
        gap = max(value["target"] - value["current"], 0.0)
        if value["drift"] >= -cfg.rebalance_tolerance_pct:
            continue
        allocation = min(remaining_cash, gap)
        if allocation >= cfg.min_trade_amount:
            budgets[sleeve] = allocation
            remaining_cash -= allocation

    items = [
        _build_item(
            plan_id=plan_id,
            sleeve="core",
            value=values["core"],
            budget=budgets["core"],
            tolerance=cfg.rebalance_tolerance_pct,
            min_trade_amount=cfg.min_trade_amount,
            priority=10,
        ),
        _build_item(
            plan_id=plan_id,
            sleeve="satellite",
            value=values["satellite"],
            budget=budgets["satellite"],
            tolerance=cfg.rebalance_tolerance_pct,
            min_trade_amount=cfg.min_trade_amount,
            priority=20,
        ),
    ]
    return AllocationPlan(
        plan_id=plan_id,
        plan_date=inputs.plan_date,
        account_id=account_id,
        total_value=total_value,
        cash=cash,
        core_target_pct=cfg.core_target_pct,
        satellite_target_pct=cfg.satellite_target_pct,
        core_value=core_value,
        satellite_value=satellite_value,
        core_budget=budgets["core"],
        satellite_budget=budgets["satellite"],
        core_drift_pct=core_drift_pct,
        satellite_drift_pct=satellite_drift_pct,
        status="ACTIVE",
        items=items,
    )


def load_allocation_inputs(conn: Any, account_id: str = DEFAULT_ACCOUNT_ID, as_of: date | None = None) -> AllocationInputs:
    params: list[Any] = [account_id]
    date_filter = ""
    if as_of is not None:
        date_filter = "AND trade_date <= ?"
        params.append(as_of)
    account = conn.execute(f"""
        SELECT trade_date, cash, position_value
        FROM account_daily
        WHERE account_id = ?
          {date_filter}
        ORDER BY trade_date DESC
        LIMIT 1
    """, params).fetchone()
    if account:
        plan_date = pd.to_datetime(account[0]).date()
        cash = _safe_float(account[1])
        satellite_value = _safe_float(account[2])
    else:
        portfolio_cfg = load_config().get("portfolio", {})
        plan_date = as_of or date.today()
        cash = _safe_float(portfolio_cfg.get("initial_capital_cn", 0.0))
        satellite_value = 0.0
    core_value = _load_core_value(conn)
    return AllocationInputs(
        plan_date=as_of or plan_date,
        account_id=account_id,
        cash=cash,
        core_value=core_value,
        satellite_value=satellite_value,
    )


def create_allocation_plan(
    conn: Any,
    account_id: str = DEFAULT_ACCOUNT_ID,
    as_of: date | None = None,
    config: AllocationConfig | None = None,
    persist: bool = True,
) -> AllocationPlan:
    cfg = config or AllocationConfig.from_app_config()
    plan = compute_allocation_plan(
        load_allocation_inputs(conn, account_id=account_id, as_of=as_of),
        cfg,
    )
    plan = attach_core_execution_plan(
        plan,
        _load_latest_index_fund_signals(conn, plan.plan_date),
        _load_core_holdings(conn),
        cfg,
    )
    if persist:
        persist_allocation_plan(conn, plan)
    return plan


def attach_core_execution_plan(
    plan: AllocationPlan,
    signals: pd.DataFrame,
    holdings: pd.DataFrame | None = None,
    config: AllocationConfig | None = None,
) -> AllocationPlan:
    """Attach fund-level advisory actions that consume the core sleeve budget."""
    if plan.status != "ACTIVE" or signals.empty:
        return plan

    cfg = (config or AllocationConfig()).normalized()
    fund_signals = signals.copy()
    fund_signals = fund_signals[fund_signals.get("fund_code", "").notna()].copy()
    if fund_signals.empty:
        return plan

    fund_signals["fund_code"] = fund_signals["fund_code"].astype(str)
    fund_signals["action"] = fund_signals["action"].fillna("HOLD").astype(str).str.upper()
    fund_signals["target_weight"] = pd.to_numeric(fund_signals.get("target_weight", 0), errors="coerce").fillna(0.0)
    fund_signals["confidence"] = pd.to_numeric(fund_signals.get("confidence", 0), errors="coerce").fillna(0.0)

    weights = fund_signals["target_weight"].clip(lower=0)
    weight_sum = float(weights.sum())
    if weight_sum <= 0:
        fund_signals["_target_weight_norm"] = 1.0 / len(fund_signals)
    else:
        fund_signals["_target_weight_norm"] = weights / weight_sum

    current_values = _core_holding_values(holdings)
    core_target_value = plan.total_value * plan.core_target_pct
    rows = []
    desired_buys: list[tuple[int, float]] = []
    for idx, row in fund_signals.reset_index(drop=True).iterrows():
        target_value = core_target_value * float(row["_target_weight_norm"])
        current_value = current_values.get(str(row["fund_code"]), 0.0)
        action = str(row["action"])
        desired = max(target_value - current_value, 0.0) if action in {"BUY", "ADD"} else 0.0
        rows.append({
            "fund_code": str(row["fund_code"]),
            "action": action,
            "current_value": current_value,
            "target_value": target_value,
            "desired_buy": desired,
            "thesis": str(row.get("thesis") or ""),
            "confidence": float(row["confidence"]),
        })
        if desired > 0 and action in {"BUY", "ADD"}:
            desired_buys.append((idx, desired))

    total_desired_buy = sum(desired for _, desired in desired_buys)
    buy_allocations = {idx: 0.0 for idx, _ in desired_buys}
    if plan.core_budget > 0 and total_desired_buy > 0:
        for idx, desired in desired_buys:
            buy_allocations[idx] = min(desired, plan.core_budget * desired / total_desired_buy)

    items = list(plan.items)
    for idx, row in enumerate(rows):
        budget_delta = 0.0
        action = row["action"]
        if action in {"BUY", "ADD"}:
            budget_delta = buy_allocations.get(idx, 0.0)
            if 0 < budget_delta < cfg.min_trade_amount:
                budget_delta = 0.0
        elif action == "REDUCE":
            budget_delta = min(row["target_value"] - row["current_value"], 0.0)
            if abs(budget_delta) < cfg.min_trade_amount:
                budget_delta = 0.0

        items.append(AllocationPlanItem(
            plan_id=plan.plan_id,
            sleeve="core",
            instrument_type="index_fund",
            instrument_id=row["fund_code"],
            action=action,
            current_value=round(row["current_value"], 2),
            target_value=round(row["target_value"], 2),
            budget_delta=round(budget_delta, 2),
            priority=11 + idx,
            reason=_core_fund_reason(action, budget_delta, row["thesis"]),
        ))

    return replace(plan, items=items)


def persist_allocation_plan(conn: Any, plan: AllocationPlan) -> None:
    plan_df = pd.DataFrame([plan.to_row()])
    items_df = pd.DataFrame([item.to_row() for item in plan.items])
    conn.execute(
        "DELETE FROM allocation_plan_items WHERE plan_id IN "
        "(SELECT plan_id FROM allocation_plans WHERE account_id = ? AND plan_date = ?)",
        [plan.account_id, plan.plan_date],
    )
    conn.execute("DELETE FROM allocation_plans WHERE account_id = ? AND plan_date = ?", [plan.account_id, plan.plan_date])
    conn.register("_tmp_allocation_plans_df", plan_df)
    conn.execute("""
        INSERT INTO allocation_plans (
            plan_id, plan_date, account_id, total_value, cash,
            core_target_pct, satellite_target_pct, core_value, satellite_value,
            core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        )
        SELECT plan_id, plan_date, account_id, total_value, cash,
               core_target_pct, satellite_target_pct, core_value, satellite_value,
               core_budget, satellite_budget, core_drift_pct, satellite_drift_pct, status
        FROM _tmp_allocation_plans_df
    """)
    if not items_df.empty:
        conn.register("_tmp_allocation_plan_items_df", items_df)
        conn.execute("""
            INSERT INTO allocation_plan_items (
                plan_id, sleeve, instrument_type, instrument_id, action,
                current_value, target_value, budget_delta, priority, reason
            )
            SELECT plan_id, sleeve, instrument_type, instrument_id, action,
                   current_value, target_value, budget_delta, priority, reason
            FROM _tmp_allocation_plan_items_df
        """)


def load_latest_allocation_plan(conn: Any, account_id: str = DEFAULT_ACCOUNT_ID) -> dict[str, pd.DataFrame]:
    plans = conn.execute("""
        SELECT *
        FROM allocation_plans
        WHERE account_id = ?
        ORDER BY plan_date DESC, created_at DESC
        LIMIT 1
    """, [account_id]).fetchdf()
    if plans.empty:
        return {"plan": plans, "items": pd.DataFrame()}
    plan_id = str(plans.iloc[0]["plan_id"])
    items = conn.execute("""
        SELECT *
        FROM allocation_plan_items
        WHERE plan_id = ?
        ORDER BY priority, sleeve, instrument_id
    """, [plan_id]).fetchdf()
    return {"plan": plans, "items": items}


def _load_latest_index_fund_signals(conn: Any, as_of: date) -> pd.DataFrame:
    try:
        return conn.execute("""
            SELECT signal_id, fund_code, index_code, signal_date, action,
                   target_weight, confidence, thesis, risk_tags, created_at
            FROM index_fund_signals
            WHERE signal_date = (
                SELECT MAX(signal_date)
                FROM index_fund_signals
                WHERE signal_date <= ?
            )
            ORDER BY confidence DESC, fund_code
        """, [as_of]).fetchdf()
    except Exception:
        return pd.DataFrame()


def _load_core_holdings(conn: Any) -> pd.DataFrame:
    try:
        from src.index_funds.performance import evaluate_holdings

        return evaluate_holdings(conn)
    except Exception:
        return pd.DataFrame()


def _load_core_value(conn: Any) -> float:
    holdings = _load_core_holdings(conn)
    if holdings.empty or "market_value" not in holdings:
        return 0.0
    return float(pd.to_numeric(holdings["market_value"], errors="coerce").fillna(0.0).sum())


def _build_item(
    plan_id: str,
    sleeve: str,
    value: dict[str, float],
    budget: float,
    tolerance: float,
    min_trade_amount: float,
    priority: int,
) -> AllocationPlanItem:
    drift = float(value["drift"])
    target = float(value["target"])
    current = float(value["current"])
    delta = target - current
    if drift > tolerance:
        action = "REDUCE"
        reason = f"{_sleeve_label(sleeve)}当前占比高于目标，超配 {drift:.1%}，暂停新增"
    elif budget >= min_trade_amount:
        action = "ADD"
        reason = f"{_sleeve_label(sleeve)}低于目标，分配可部署现金 {budget:,.0f}"
    elif drift < -tolerance:
        action = "HOLD"
        reason = f"{_sleeve_label(sleeve)}低于目标但可用现金不足最小交易额"
    else:
        action = "HOLD"
        reason = f"{_sleeve_label(sleeve)}在目标容忍区间内"
    return AllocationPlanItem(
        plan_id=plan_id,
        sleeve=sleeve,
        instrument_type="sleeve",
        instrument_id=sleeve,
        action=action,
        current_value=round(current, 2),
        target_value=round(target, 2),
        budget_delta=round(delta if action == "REDUCE" else budget, 2),
        priority=priority,
        reason=reason,
    )


def _core_holding_values(holdings: pd.DataFrame | None) -> dict[str, float]:
    if holdings is None or holdings.empty or "fund_code" not in holdings or "market_value" not in holdings:
        return {}
    values: dict[str, float] = {}
    for _, row in holdings.iterrows():
        fund_code = str(row.get("fund_code") or "")
        if not fund_code:
            continue
        values[fund_code] = _safe_float(row.get("market_value"))
    return values


def _core_fund_reason(action: str, budget_delta: float, thesis: str) -> str:
    if action in {"BUY", "ADD"} and budget_delta > 0:
        prefix = f"Core预算分配 {budget_delta:,.0f}"
    elif action in {"BUY", "ADD"}:
        prefix = "Core预算不足或该基金未低于目标，暂不新增"
    elif action == "REDUCE" and budget_delta < 0:
        prefix = f"基金高于目标，建议减仓 {abs(budget_delta):,.0f}"
    elif action == "REDUCE":
        prefix = "指数基金信号建议减仓，但当前未高于资金池目标"
    elif action == "PAUSE":
        prefix = "指数基金信号暂停新增"
    else:
        prefix = "指数基金信号维持观察"
    return f"{prefix}；{thesis}" if thesis else prefix


def _plan_id(account_id: str, plan_date: date) -> str:
    clean_account = "".join(ch if ch.isalnum() else "_" for ch in account_id.upper())
    return f"ALLOC-{clean_account}-{plan_date.strftime('%Y%m%d')}"


def _safe_float(value: Any) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sleeve_label(sleeve: str) -> str:
    return "Core指数基金" if sleeve == "core" else "Satellite个股策略"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Core-satellite allocation planner")
    sub = parser.add_subparsers(dest="command", required=True)
    p_plan = sub.add_parser("plan", help="生成并保存统一资金分配计划")
    p_plan.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    p_plan.add_argument("--date", default=None, help="计划日期 YYYY-MM-DD，默认取账户最新日期")
    p_plan.add_argument("--no-persist", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "plan":
        from src.data_pipeline.loader import get_connection, init_db

        as_of = pd.to_datetime(args.date).date() if args.date else None
        conn = get_connection()
        try:
            init_db(conn)
            plan = create_allocation_plan(conn, account_id=args.account_id, as_of=as_of, persist=not args.no_persist)
            print(json.dumps(plan.to_dict(), ensure_ascii=False, default=str, indent=2))
        finally:
            conn.close()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
