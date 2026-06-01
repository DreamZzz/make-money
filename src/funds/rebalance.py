"""G5: 再平衡执行计划生成器(散户档)。

把 fund_evaluations + net_action 转成"具体买/卖多少"的可执行单:
- 输入:持仓基金 evaluation(含 current_value/target_value/net_action)、当前现金、M4 权重
- 算法:贪心,按"欠配缺口 × 综合分"优先级排序;只对 net_action 允许加/减的基金动手
- 散户档约束:
    * 最小单笔 ¥1000(避免摩擦吃掉收益)
    * drift < 10% 标 HOLD(不在阈值内的不动)
    * 时间触发(每月一次)+ 阈值触发(drift ≥ 10%)
    * 优先按"超额表现替代品"补,而不是补回原持仓
- 输出:RebalancePlan,每行 fund_code / action / amount / reason
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import duckdb
import pandas as pd

# 散户档默认参数
DEFAULT_MIN_ACTION_AMOUNT = 1000.0    # < ¥1000 不动
DEFAULT_DRIFT_THRESHOLD = 0.10        # |drift| < 10% 标 HOLD
DEFAULT_MAX_BUY_PER_RUN = 3           # 一次最多 3 个 BUY,避免散户操作疲劳

# 触发类型
TRIGGER_MONTHLY = "monthly"
TRIGGER_DRIFT = "drift_threshold"
TRIGGER_MANUAL = "manual"

# 允许在 net_action 哪些档位下产出 BUY
NET_ACTIONS_ALLOWING_BUY = {"ADD_TO_TARGET", "ADD_WINDOW_OPEN"}
NET_ACTIONS_ALLOWING_SELL = {"EXIT_NOW", "REDUCE_TO_TARGET"}
NET_ACTIONS_HOLD = {"HOLD_WAIT_TREND", "HOLD_AS_PLANNED", "CONSIDER_SWITCH", "NO_DATA"}


@dataclass
class RebalanceAction:
    fund_code: str
    fund_name: str | None
    action: str                # BUY / SELL / HOLD
    amount: float
    estimated_units: float | None
    nav: float | None
    current_value: float | None
    target_value: float | None
    drift_pct: float | None
    priority: int
    reason: str
    rank: int = 0
    constraint_tags: list[str] = field(default_factory=list)


@dataclass
class RebalancePlan:
    plan_id: str
    plan_date: date
    trigger_type: str
    trigger_reason: str
    account_total: float | None
    equity_exposure: float | None
    actions: list[RebalanceAction]
    headline: str
    total_actions: int
    total_buy_amount: float
    total_sell_amount: float


def _priority_score(drift_pct: float | None, scanner_score: float | None) -> float:
    """优先级 = |drift| × scanner_score(归一化 0-1);谁缺口大且综合分高,谁先做。"""
    if drift_pct is None or scanner_score is None:
        return 0.0
    return abs(drift_pct) * (float(scanner_score) / 100.0)


def _decide_action(net_action: str, drift_pct: float | None,
                   drift_threshold: float) -> tuple[str, str]:
    """根据 net_action + drift 决定 BUY/SELL/HOLD。返回 (action, reason)。"""
    if drift_pct is None:
        return "HOLD", "无法计算 drift"
    abs_d = abs(drift_pct)
    if net_action == "EXIT_NOW":
        return "SELL", "触发 EXIT_NOW 止损"
    if net_action in NET_ACTIONS_HOLD:
        return "HOLD", f"net_action={net_action},不动"
    if abs_d < drift_threshold:
        return "HOLD", f"|drift| {abs_d:.0%} < 阈值 {drift_threshold:.0%}"
    if net_action in NET_ACTIONS_ALLOWING_BUY and drift_pct < 0:
        return "BUY", f"net_action={net_action} + 欠配 {drift_pct:.0%}"
    if net_action in NET_ACTIONS_ALLOWING_SELL and drift_pct > 0:
        return "SELL", f"net_action={net_action} + 超配 {drift_pct:.0%}"
    return "HOLD", f"net_action={net_action} 与 drift {drift_pct:.0%} 不一致"


def build_rebalance_plan(
    conn: duckdb.DuckDBPyConnection,
    *,
    trigger_type: str = TRIGGER_MONTHLY,
    trigger_reason: str = "月度再平衡",
    min_action_amount: float = DEFAULT_MIN_ACTION_AMOUNT,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    max_buy_per_run: int = DEFAULT_MAX_BUY_PER_RUN,
    plan_date: date | None = None,
    persist: bool = False,
) -> RebalancePlan:
    """主入口:基于当前 evaluation + monitoring + 现金,产出执行计划。"""
    from src.funds.evaluation import evaluate_funds
    from src.funds.monitoring import monitor_holdings
    from src.funds.net_action import derive_net_action

    plan_date = plan_date or date.today()
    plan_id = f"REBAL-{plan_date.isoformat()}-{uuid.uuid4().hex[:6].upper()}"

    evals = evaluate_funds(conn)
    alerts = [asdict(a) for a in monitor_holdings(conn)]

    account_total = evals[0].account_total_value if evals else None
    equity_exposure = evals[0].equity_exposure if evals else None

    # 现金 = account_total - sum(current_value of 所有持仓)
    total_value_held = sum((e.current_value or 0.0) for e in evals)
    cash = (account_total - total_value_held) if account_total is not None else None

    # 取 scanner 分用于优先级
    scanner_scores: dict[str, float] = {}
    try:
        rows = conn.execute(
            "SELECT fund_code, total_score FROM fund_screening_results "
            "WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results)"
        ).fetchall()
        scanner_scores = {fc: float(s) for fc, s in rows if s is not None}
    except Exception:  # noqa: BLE001
        pass

    raw_actions: list[RebalanceAction] = []
    for e in evals:
        fund_alerts = [a for a in alerts if a.get("fund_code") == e.fund_code]
        net = derive_net_action(fund_alerts)
        drift = e.drift_pct
        action_kind, reason = _decide_action(net.net_action, drift, drift_threshold)
        target_amount = 0.0
        if action_kind in {"BUY", "SELL"} and e.delta_amount is not None:
            target_amount = abs(e.delta_amount)
        # 单笔最小阈值
        constraint_tags: list[str] = []
        if action_kind in {"BUY", "SELL"} and target_amount < min_action_amount:
            constraint_tags.append("below_min_amount")
            action_kind = "HOLD"
            reason = f"目标金额 ¥{target_amount:.0f} < 单笔最小 ¥{min_action_amount:.0f}"
        priority = int(_priority_score(drift, scanner_scores.get(e.fund_code)) * 100)
        units = (target_amount / e.nav) if (action_kind != "HOLD" and e.nav and e.nav > 0) else None
        raw_actions.append(RebalanceAction(
            fund_code=e.fund_code,
            fund_name=e.fund_name,
            action=action_kind,
            amount=target_amount if action_kind != "HOLD" else 0.0,
            estimated_units=units,
            nav=e.nav,
            current_value=e.current_value,
            target_value=e.target_value,
            drift_pct=drift,
            priority=priority,
            reason=f"{reason};{net.headline}",
            constraint_tags=constraint_tags,
        ))

    # 排序:BUY/SELL 在前(按 priority 倒序),HOLD 在后(原顺序)
    actionable = [a for a in raw_actions if a.action != "HOLD"]
    held = [a for a in raw_actions if a.action == "HOLD"]
    actionable.sort(key=lambda a: -a.priority)

    # 散户档:一次最多 max_buy_per_run 个 BUY(SELL 不限,避免错过止损)
    buys = [a for a in actionable if a.action == "BUY"]
    sells = [a for a in actionable if a.action == "SELL"]
    if len(buys) > max_buy_per_run:
        for a in buys[max_buy_per_run:]:
            a.action = "HOLD"
            a.amount = 0.0
            a.estimated_units = None
            a.constraint_tags.append("exceeded_buy_quota")
            a.reason += f";本轮 BUY 配额已用完(最多 {max_buy_per_run})"
        held.extend(buys[max_buy_per_run:])
        buys = buys[:max_buy_per_run]

    # 现金约束:BUY 总额不超过现金;不够则按 priority 顺序截断
    if cash is not None and buys:
        remaining = float(cash)
        for a in buys:
            if a.amount <= remaining:
                remaining -= a.amount
            else:
                if remaining >= min_action_amount:
                    a.amount = round(remaining, 0)
                    a.estimated_units = (a.amount / a.nav) if (a.nav and a.nav > 0) else None
                    a.constraint_tags.append("cash_truncated")
                    a.reason += f";现金不足,截到剩余 ¥{remaining:.0f}"
                    remaining = 0.0
                else:
                    a.action = "HOLD"
                    a.amount = 0.0
                    a.estimated_units = None
                    a.constraint_tags.append("cash_insufficient")
                    a.reason += ";现金不足"
                    held.append(a)
        buys = [a for a in buys if a.action == "BUY"]

    # rank 编号
    final_ordered = buys + sells + held
    for i, a in enumerate(final_ordered, 1):
        a.rank = i

    total_buy = sum(a.amount for a in buys)
    total_sell = sum(a.amount for a in sells)
    n_actionable = len(buys) + len(sells)

    if n_actionable == 0:
        headline = f"本轮无需操作(drift 全部 < {drift_threshold:.0%} 或 net_action 标 HOLD)"
    else:
        parts = []
        if buys:
            parts.append(f"{len(buys)} 笔 BUY 共 ¥{total_buy:,.0f}")
        if sells:
            parts.append(f"{len(sells)} 笔 SELL 共 ¥{total_sell:,.0f}")
        headline = " / ".join(parts)

    plan = RebalancePlan(
        plan_id=plan_id, plan_date=plan_date,
        trigger_type=trigger_type, trigger_reason=trigger_reason,
        account_total=account_total, equity_exposure=equity_exposure,
        actions=final_ordered,
        headline=headline,
        total_actions=n_actionable,
        total_buy_amount=total_buy,
        total_sell_amount=total_sell,
    )

    if persist:
        _persist(conn, plan)
    return plan


def _persist(conn: duckdb.DuckDBPyConnection, plan: RebalancePlan) -> None:
    conn.execute(
        "INSERT INTO fund_rebalance_plan (plan_id, plan_date, trigger_type, trigger_reason, "
        "account_total, equity_exposure, headline, total_actions, total_buy_amount, total_sell_amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [plan.plan_id, plan.plan_date, plan.trigger_type, plan.trigger_reason,
         plan.account_total, plan.equity_exposure, plan.headline,
         plan.total_actions, plan.total_buy_amount, plan.total_sell_amount],
    )
    if not plan.actions:
        return
    df = pd.DataFrame([{"plan_id": plan.plan_id, **asdict(a)} for a in plan.actions])  # noqa: F841 - DuckDB 引用
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_rebal_act AS SELECT * FROM df")
    conn.execute(
        """
        INSERT INTO fund_rebalance_action (
            plan_id, fund_code, fund_name, action, amount, estimated_units,
            nav, current_value, target_value, drift_pct, priority, reason, rank, constraint_tags
        )
        SELECT plan_id, fund_code, fund_name, action, amount, estimated_units,
               nav, current_value, target_value, drift_pct, priority, reason, rank, constraint_tags
        FROM _tmp_rebal_act
        """
    )


def load_latest_plan(conn: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    """读最新一份 plan(plan + actions),Dashboard 用。"""
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='fund_rebalance_plan'"
    ).fetchone():
        return None
    row = conn.execute(
        "SELECT * FROM fund_rebalance_plan ORDER BY plan_date DESC, created_at DESC LIMIT 1"
    ).fetchdf()
    if row.empty:
        return None
    plan = row.to_dict(orient="records")[0]
    acts = conn.execute(
        "SELECT * FROM fund_rebalance_action WHERE plan_id = ? ORDER BY rank",
        [plan["plan_id"]],
    ).fetchdf()
    plan["actions"] = acts.to_dict(orient="records") if not acts.empty else []
    return plan
