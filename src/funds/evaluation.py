"""D1+E: 基金每日评估服务

把零散数据合成 `FundEvaluation` 单条记录(每基金每日一条):
- index_fund_snapshots → 用户录入的份额/成本(取每 fund 最新一条;note JSON 升格为 broker 真值)
- fund_nav            → 最新净值
- index_daily         → 跟踪指数(equity_index/qdii 适用)
- index_allocation    → M4 动态目标权重(仅 equity_index/qdii + active 进池)
- market_exposure     → 宏观目标权益仓位
- account_daily       → 账户总值
- fund_info / watchlist → category (equity_index/balanced/qdii/...) + intent (active/exited/...)

E 分流原则:
- balanced 类:不算 price_pct/MA/M4 目标,只展示 holding PnL + cost 偏离
- exited 状态:不算 delta_amount,thesis "已退出系统不驱动"
- active equity_index/qdii:沿用 D1 流程
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import duckdb
import pandas as pd

from src.index_funds.config import FundWatchItem, get_rules, get_watchlist
from src.index_funds.signals import calculate_signal, load_m4_weights

# snapshot 多久不刷新被视为过期 → 评估不可信
SNAPSHOT_STALE_DAYS = 3
# nav 多久没数据被视为过期 → 评估不可信
NAV_STALE_DAYS = 3


@dataclass
class FundEvaluation:
    eval_date: date
    fund_code: str
    fund_name: str | None
    tracking_index: str | None
    tracking_index_name: str | None
    # E1: 分类
    category: str = "equity_index"
    intent: str = "active"
    # 快照(用户录入)
    snapshot_date: date | None = None
    snapshot_stale_days: int | None = None
    shares: float | None = None
    cost_amount: float | None = None
    # E2: 从 snapshot.note JSON 升格的 broker 真值
    broker_market_value: float | None = None
    broker_latest_nav: float | None = None
    broker_cost_price: float | None = None
    broker_holding_pnl: float | None = None
    broker_holding_return_pct: float | None = None
    broker_day_return_pct: float | None = None
    broker_yesterday_pnl: float | None = None
    holding_days: int | None = None
    snapshot_source: str | None = None
    snapshot_captured_at: str | None = None
    market_value_vs_computed_pct: float | None = None  # broker vs (shares × nav) 偏差校验
    # 净值
    nav: float | None = None
    nav_date: date | None = None
    nav_stale_days: int | None = None
    current_value: float | None = None
    return_amount: float | None = None
    return_pct: float | None = None
    # 估值/趋势 (仅 equity_index/qdii 适用)
    price_pct: float | None = None
    ma_fast: float | None = None
    ma_slow: float | None = None
    trend_healthy: bool | None = None
    trend_weak: bool | None = None
    # 权重决策
    target_weight_m4: float | None = None
    equity_exposure: float | None = None
    target_value: float | None = None
    target_account_weight: float | None = None
    current_weight: float | None = None
    current_account_weight: float | None = None
    drift_pct: float | None = None
    delta_amount: float | None = None
    delta_shares: float | None = None
    action: str = "HOLD"
    confidence: float = 0.0
    thesis: str = ""
    risk_tags: list[str] = field(default_factory=list)
    account_total_value: float | None = None


def _latest_snapshot_per_fund(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT fund_code, snapshot_date, shares, cost_amount, note
        FROM index_fund_snapshots
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY fund_code
            ORDER BY snapshot_date DESC, created_at DESC
        ) = 1
        """
    ).fetchall()
    return {fc: {"snapshot_date": sd, "shares": float(s or 0), "cost_amount": float(c or 0),
                 "note": note or ""}
            for fc, sd, s, c, note in rows}


def _parse_snapshot_note(note: str) -> dict[str, Any]:
    """E2: snapshot.note 字段可能是 broker 截图导出的 JSON,把字段升格出来。

    兼容:空串/旧版纯文本 → 返回 {}。
    """
    if not note or not note.strip().startswith("{"):
        return {}
    try:
        parsed = json.loads(note)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _latest_nav(conn: duckdb.DuckDBPyConnection, fund_code: str) -> tuple[float | None, date | None]:
    row = conn.execute(
        "SELECT nav, trade_date FROM fund_nav WHERE fund_code = ? ORDER BY trade_date DESC LIMIT 1",
        [fund_code],
    ).fetchone()
    if not row:
        return None, None
    return (float(row[0]) if row[0] is not None else None, row[1])


def _account_total(conn: duckdb.DuckDBPyConnection, account_id: str = "default") -> tuple[float | None, date | None]:
    row = conn.execute(
        "SELECT total_value, trade_date FROM account_daily WHERE account_id = ? "
        "ORDER BY trade_date DESC LIMIT 1",
        [account_id],
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    return (float(row[0]), row[1])


def _equity_exposure_target(conn: duckdb.DuckDBPyConnection) -> float | None:
    row = conn.execute(
        "SELECT target_exposure FROM market_exposure ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _stale_days(reference: date, value: date | None) -> int | None:
    if value is None:
        return None
    return max((reference - value).days, 0)


def evaluate_fund(
    conn: duckdb.DuckDBPyConnection,
    item: FundWatchItem,
    *,
    snapshots: dict[str, dict[str, Any]],
    m4_weights: dict[str, float],
    equity_exposure: float | None,
    account_total: float | None,
    rules: dict[str, Any],
    eval_date: date,
) -> FundEvaluation | None:
    category = (item.category or "equity_index").lower()
    intent = (item.intent or "active").lower()
    is_active = intent == "active"
    is_exited = intent == "exited"

    nav, nav_date = _latest_nav(conn, item.fund_code)
    nav_stale = _stale_days(eval_date, nav_date)

    snap = snapshots.get(item.fund_code)
    shares = float(snap["shares"]) if snap else None
    cost = float(snap["cost_amount"]) if snap else None
    snap_date = snap["snapshot_date"] if snap else None
    snap_stale = _stale_days(eval_date, snap_date) if snap_date else None

    # E2: 解析 broker 快照 note JSON,优先用 broker 真值
    note = snap.get("note") if snap else ""
    note_data = _parse_snapshot_note(note) if note else {}
    broker_mv = _opt_float(note_data, "market_value")
    broker_nav = _opt_float(note_data, "latest_nav")
    broker_cost_price = _opt_float(note_data, "cost_price_display")
    broker_holding_pnl = _opt_float(note_data, "holding_pnl")
    broker_holding_ret = _opt_float(note_data, "holding_return_pct")
    broker_day_ret = _opt_float(note_data, "day_return_pct")
    broker_yest_pnl = _opt_float(note_data, "yesterday_pnl")
    holding_days = note_data.get("holding_days")
    holding_days = int(holding_days) if isinstance(holding_days, (int, float)) else None
    snap_source = note_data.get("source") if isinstance(note_data.get("source"), str) else None
    snap_captured_at = note_data.get("captured_at") if isinstance(note_data.get("captured_at"), str) else None

    # E2: broker 真实 market_value 优先, 缺时 fallback shares × nav
    current_value = broker_mv if broker_mv is not None else (
        (shares * nav) if (shares is not None and nav is not None) else None
    )
    # broker vs computed 偏差校验
    mv_vs_computed_pct = None
    if broker_mv is not None and shares is not None and nav is not None and nav > 0:
        computed = shares * nav
        if computed > 0:
            mv_vs_computed_pct = (broker_mv - computed) / computed

    # E2: 收益用 broker 真值优先
    if broker_holding_pnl is not None:
        return_amount = broker_holding_pnl
    elif current_value is not None and cost is not None:
        return_amount = current_value - cost
    else:
        return_amount = None
    if broker_holding_ret is not None:
        return_pct = broker_holding_ret
    elif return_amount is not None and cost and cost > 0:
        return_pct = return_amount / cost
    else:
        return_pct = None

    current_account_weight = (current_value / account_total) if (current_value is not None and account_total) else 0.0

    # E3: 按 category/intent 分流
    risk_tags: list[str] = []
    thesis_parts: list[str] = []
    price_pct = ma_fast = ma_slow = None
    trend_healthy = trend_weak = None
    target_weight_m4: float | None = None
    target_account_weight: float | None = None
    target_value: float | None = None
    drift_pct: float | None = None
    delta_amount: float | None = None
    delta_shares: float | None = None
    action = "HOLD"
    confidence = 0.0

    if is_exited:
        # 已清仓,系统不再驱动加减;只展示真实 PnL 和持有天数
        action = "HOLD"
        risk_tags.append("exited")
        if return_pct is not None and return_pct > 0:
            thesis_parts.append(
                f"已退出 (用户清仓后残留 ¥{current_value or 0:,.0f}),系统不再驱动调仓;"
                f"残留收益 {return_pct:.1%}"
            )
        else:
            thesis_parts.append("已退出,系统不再驱动调仓")
    elif category == "balanced":
        # 股债混合,不适用 price_pct/MA/M4;只展示持有 PnL + cost 偏离
        action = "HOLD"
        risk_tags.append("balanced_no_equity_rules")
        if return_pct is not None:
            verdict = "盈利" if return_pct >= 0 else "浮亏"
            thesis_parts.append(
                f"股债混合基金({verdict} {return_pct:.2%}),不适用纯权益指数评估口径;"
                f"参考指数 {item.tracking_index_name},仅做趋势参考不强制对齐"
            )
        else:
            thesis_parts.append("股债混合基金,持仓收益不可算")
    else:
        # equity_index / qdii + active: 沿用 D1 流程
        idx_df = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code = ? ORDER BY trade_date",
            [item.tracking_index],
        ).fetchdf()
        m4_w = m4_weights.get(item.fund_code) if is_active else None
        target_weight_m4 = m4_w
        if m4_w is not None:
            tgt_override = float(m4_w)
            tgt_source = "m4"
        else:
            tgt_override = None
            tgt_source = "config_fallback"
        signal = calculate_signal(
            item, idx_df, rules,
            current_weight=current_account_weight,
            target_weight_override=tgt_override,
            target_weight_source=tgt_source,
        )
        if signal is not None:
            action = signal.action
            confidence = float(signal.confidence)
            risk_tags.extend(signal.risk_tags)
            thesis_parts.append(signal.thesis)
            from src.index_funds.signals import compute_index_state
            st = compute_index_state(idx_df, rules)
            if st:
                price_pct = st.get("price_percentile")
                ma_fast = st.get("ma_fast")
                ma_slow = st.get("ma_slow")
                trend_healthy = bool(st.get("trend_healthy"))
                trend_weak = bool(st.get("trend_weak"))

        if equity_exposure is not None and target_weight_m4 is not None:
            target_account_weight = float(equity_exposure) * float(target_weight_m4)
        if account_total is not None and target_account_weight is not None:
            target_value = account_total * target_account_weight
        if current_value is not None and target_value is not None:
            drift = current_value - target_value
            if target_value > 0:
                drift_pct = drift / target_value
            delta_amount = target_value - current_value
        if delta_amount is not None and nav and nav > 0:
            delta_shares = delta_amount / nav
        if target_weight_m4 is None and is_active:
            risk_tags.append("m4_missing")

    # 通用风险标签
    if snap is None:
        risk_tags.append("no_snapshot")
        thesis_parts.append("缺少用户录入的份额/成本")
    elif snap_stale is not None and snap_stale > SNAPSHOT_STALE_DAYS:
        risk_tags.append("snapshot_stale")
        thesis_parts.append(f"快照已 {snap_stale} 天未刷新,请在 Dashboard 录入今日份额")
    if nav_stale is not None and nav_stale > NAV_STALE_DAYS:
        risk_tags.append("nav_stale")
        thesis_parts.append(f"净值已 {nav_stale} 天未更新")
    if mv_vs_computed_pct is not None and abs(mv_vs_computed_pct) > 0.01:
        risk_tags.append("broker_mismatch")
        thesis_parts.append(f"broker 市值与 shares×nav 偏差 {mv_vs_computed_pct:+.2%}")

    thesis = "；".join([p for p in thesis_parts if p])
    risk_tags = list(dict.fromkeys(risk_tags))

    return FundEvaluation(
        eval_date=eval_date,
        fund_code=item.fund_code,
        fund_name=item.name,
        tracking_index=item.tracking_index,
        tracking_index_name=item.tracking_index_name,
        category=category,
        intent=intent,
        snapshot_date=snap_date,
        snapshot_stale_days=snap_stale,
        shares=shares,
        cost_amount=cost,
        broker_market_value=broker_mv,
        broker_latest_nav=broker_nav,
        broker_cost_price=broker_cost_price,
        broker_holding_pnl=broker_holding_pnl,
        broker_holding_return_pct=broker_holding_ret,
        broker_day_return_pct=broker_day_ret,
        broker_yesterday_pnl=broker_yest_pnl,
        holding_days=holding_days,
        snapshot_source=snap_source,
        snapshot_captured_at=snap_captured_at,
        market_value_vs_computed_pct=mv_vs_computed_pct,
        nav=nav,
        nav_date=nav_date,
        nav_stale_days=nav_stale,
        current_value=current_value,
        return_amount=return_amount,
        return_pct=return_pct,
        price_pct=price_pct,
        ma_fast=ma_fast,
        ma_slow=ma_slow,
        trend_healthy=trend_healthy,
        trend_weak=trend_weak,
        target_weight_m4=target_weight_m4,
        equity_exposure=equity_exposure,
        target_value=target_value,
        target_account_weight=target_account_weight,
        current_weight=current_account_weight,
        current_account_weight=current_account_weight,
        drift_pct=drift_pct,
        delta_amount=delta_amount,
        delta_shares=delta_shares,
        action=action,
        confidence=confidence,
        thesis=thesis,
        risk_tags=risk_tags or ["normal"],
        account_total_value=account_total,
    )


def _opt_float(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_funds(
    conn: duckdb.DuckDBPyConnection,
    *,
    eval_date: date | None = None,
    account_id: str = "default",
    persist: bool = False,
) -> list[FundEvaluation]:
    watchlist = get_watchlist()
    if not watchlist:
        return []
    rules = get_rules()
    snapshots = _latest_snapshot_per_fund(conn)
    m4_weights = load_m4_weights(conn)
    equity_exposure = _equity_exposure_target(conn)
    account_total, account_date = _account_total(conn, account_id)
    eval_date = eval_date or account_date or date.today()
    out: list[FundEvaluation] = []
    for item in watchlist:
        ev = evaluate_fund(
            conn, item,
            snapshots=snapshots, m4_weights=m4_weights,
            equity_exposure=equity_exposure, account_total=account_total,
            rules=rules, eval_date=eval_date,
        )
        if ev is not None:
            out.append(ev)
    if persist and out:
        _persist(conn, out)
    return out


def _persist(conn: duckdb.DuckDBPyConnection, evals: list[FundEvaluation]) -> None:
    df = pd.DataFrame([asdict(e) for e in evals])
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fund_evals AS SELECT * FROM df")
    eval_dates = df["eval_date"].dropna().unique().tolist()
    for d in eval_dates:
        conn.execute("DELETE FROM fund_evaluations WHERE eval_date = ?", [d])
    conn.execute(
        """
        INSERT INTO fund_evaluations (
            eval_date, fund_code, fund_name, tracking_index, tracking_index_name,
            category, intent,
            snapshot_date, snapshot_stale_days, shares, cost_amount,
            broker_market_value, broker_latest_nav, broker_cost_price,
            broker_holding_pnl, broker_holding_return_pct, broker_day_return_pct,
            broker_yesterday_pnl, holding_days, snapshot_source, snapshot_captured_at,
            market_value_vs_computed_pct,
            nav, nav_date, nav_stale_days, current_value, return_amount, return_pct,
            price_pct, ma_fast, ma_slow, trend_healthy, trend_weak,
            target_weight_m4, equity_exposure, target_value, target_account_weight,
            current_weight, current_account_weight, drift_pct, delta_amount, delta_shares,
            action, confidence, thesis, risk_tags, account_total_value
        )
        SELECT
            eval_date, fund_code, fund_name, tracking_index, tracking_index_name,
            category, intent,
            snapshot_date, snapshot_stale_days, shares, cost_amount,
            broker_market_value, broker_latest_nav, broker_cost_price,
            broker_holding_pnl, broker_holding_return_pct, broker_day_return_pct,
            broker_yesterday_pnl, holding_days, snapshot_source, snapshot_captured_at,
            market_value_vs_computed_pct,
            nav, nav_date, nav_stale_days, current_value, return_amount, return_pct,
            price_pct, ma_fast, ma_slow, trend_healthy, trend_weak,
            target_weight_m4, equity_exposure, target_value, target_account_weight,
            current_weight, current_account_weight, drift_pct, delta_amount, delta_shares,
            action, confidence, thesis, risk_tags, account_total_value
        FROM _tmp_fund_evals
        """
    )


def load_latest_evaluations(
    conn: duckdb.DuckDBPyConnection,
) -> list[dict[str, Any]]:
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='fund_evaluations'"
    ).fetchone():
        return []
    rows = conn.execute(
        """
        SELECT * FROM fund_evaluations
        WHERE eval_date = (SELECT MAX(eval_date) FROM fund_evaluations)
        ORDER BY COALESCE(target_account_weight, 0) DESC, fund_code
        """
    ).fetchdf()
    if rows.empty:
        return []
    return rows.to_dict(orient="records")
