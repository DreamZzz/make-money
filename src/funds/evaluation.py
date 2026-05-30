"""D1: 基金每日评估服务

把零散数据合成 `FundEvaluation` 单条记录(每基金每日一条):
- index_fund_snapshots → 用户录入的份额/成本(取每 fund 最新一条)
- fund_nav            → 最新净值
- index_daily         → 跟踪指数(用于 price_pct / MA 趋势)
- index_allocation    → M4 动态目标权重
- market_exposure     → 宏观目标权益仓位 (target)
- account_daily       → 账户总值
- fund_info           → 基金名 / 跟踪指数

输出:
- 当前持仓 / 收益 / 估值 / 趋势 / 应执行金额 / 风险标签
- 决策由 calculate_signal (D3 已统一为 M4 权重) 复用,不重复实现

设计原则:
- 评估"不下单",只把已有数据合成可消费的视图;Dashboard 直接读
- snapshot 过期 (>3 天) 用 risk_tag `snapshot_stale` 标注,delta_amount 仍算但 advice 降一档
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
    snapshot_date: date | None
    snapshot_stale_days: int | None
    shares: float | None
    cost_amount: float | None
    nav: float | None
    nav_date: date | None
    nav_stale_days: int | None
    current_value: float | None
    return_amount: float | None
    return_pct: float | None
    price_pct: float | None
    ma_fast: float | None
    ma_slow: float | None
    trend_healthy: bool | None
    trend_weak: bool | None
    target_weight_m4: float | None
    equity_exposure: float | None
    target_value: float | None
    target_account_weight: float | None
    current_weight: float | None
    current_account_weight: float | None
    drift_pct: float | None
    delta_amount: float | None
    delta_shares: float | None
    action: str
    confidence: float
    thesis: str
    risk_tags: list[str]
    account_total_value: float | None


def _latest_snapshot_per_fund(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT fund_code, snapshot_date, shares, cost_amount
        FROM index_fund_snapshots
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY fund_code
            ORDER BY snapshot_date DESC, created_at DESC
        ) = 1
        """
    ).fetchall()
    return {fc: {"snapshot_date": sd, "shares": float(s or 0), "cost_amount": float(c or 0)}
            for fc, sd, s, c in rows}


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
    nav, nav_date = _latest_nav(conn, item.fund_code)
    nav_stale = _stale_days(eval_date, nav_date)

    snap = snapshots.get(item.fund_code)
    shares = float(snap["shares"]) if snap else None
    cost = float(snap["cost_amount"]) if snap else None
    snap_date = snap["snapshot_date"] if snap else None
    snap_stale = _stale_days(eval_date, snap_date) if snap_date else None
    current_value = (shares * nav) if (shares is not None and nav is not None) else None
    return_amount = (current_value - cost) if (current_value is not None and cost is not None) else None
    return_pct = (return_amount / cost) if (return_amount is not None and cost and cost > 0) else None

    # 拉指数日线给 signal 复用
    idx_df = conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE index_code = ? ORDER BY trade_date",
        [item.tracking_index],
    ).fetchdf()

    # 复用 calculate_signal 决定 action/thesis;以"账户级权重"作 current_weight
    current_account_weight = (current_value / account_total) if (current_value is not None and account_total) else 0.0
    m4_w = m4_weights.get(item.fund_code)
    target_weight_m4 = m4_w
    # 信号层用 "M4 权重" 作目标(D3 已支持),current 用账户级权重
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

    price_pct = ma_fast = ma_slow = None
    trend_healthy = trend_weak = None
    if signal is not None:
        from src.index_funds.signals import compute_index_state
        st = compute_index_state(idx_df, rules)
        if st:
            price_pct = st.get("price_percentile")
            ma_fast = st.get("ma_fast")
            ma_slow = st.get("ma_slow")
            trend_healthy = bool(st.get("trend_healthy"))
            trend_weak = bool(st.get("trend_weak"))

    # 目标账户级权重 = 宏观权益仓位 * M4 权重
    target_account_weight = None
    target_value = None
    drift_pct = None
    delta_amount = None
    delta_shares = None
    if equity_exposure is not None and target_weight_m4 is not None:
        target_account_weight = float(equity_exposure) * float(target_weight_m4)
    if account_total is not None and target_account_weight is not None:
        target_value = account_total * target_account_weight
    if current_value is not None and target_value is not None:
        drift = (current_value - target_value)
        if target_value > 0:
            drift_pct = drift / target_value
        delta_amount = target_value - current_value
    if delta_amount is not None and nav and nav > 0:
        delta_shares = delta_amount / nav

    risk_tags = list(signal.risk_tags) if signal else []
    thesis_parts = [signal.thesis] if signal else []
    if snap is None:
        risk_tags.append("no_snapshot")
        thesis_parts.append("缺少用户录入的份额/成本,持仓与收益无法计算")
    elif snap_stale is not None and snap_stale > SNAPSHOT_STALE_DAYS:
        risk_tags.append("snapshot_stale")
        thesis_parts.append(f"快照已 {snap_stale} 天未刷新,持仓数据可能滞后,建议先在 Dashboard 录入今日份额")
    if nav_stale is not None and nav_stale > NAV_STALE_DAYS:
        risk_tags.append("nav_stale")
        thesis_parts.append(f"净值已 {nav_stale} 天未更新,估值可能失真")
    if target_weight_m4 is None:
        risk_tags.append("m4_missing")
    thesis = "；".join([p for p in thesis_parts if p])

    return FundEvaluation(
        eval_date=eval_date,
        fund_code=item.fund_code,
        fund_name=item.name,
        tracking_index=item.tracking_index,
        tracking_index_name=item.tracking_index_name,
        snapshot_date=snap_date,
        snapshot_stale_days=snap_stale,
        shares=shares,
        cost_amount=cost,
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
        current_weight=current_account_weight,  # 此处与账户级权重同口径,前端同步显示
        current_account_weight=current_account_weight,
        drift_pct=drift_pct,
        delta_amount=delta_amount,
        delta_shares=delta_shares,
        action=signal.action if signal else "HOLD",
        confidence=float(signal.confidence) if signal else 0.0,
        thesis=thesis,
        risk_tags=list(dict.fromkeys(risk_tags)) or ["normal"],
        account_total_value=account_total,
    )


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
            snapshot_date, snapshot_stale_days, shares, cost_amount,
            nav, nav_date, nav_stale_days, current_value, return_amount, return_pct,
            price_pct, ma_fast, ma_slow, trend_healthy, trend_weak,
            target_weight_m4, equity_exposure, target_value, target_account_weight,
            current_weight, current_account_weight, drift_pct, delta_amount, delta_shares,
            action, confidence, thesis, risk_tags, account_total_value
        )
        SELECT
            eval_date, fund_code, fund_name, tracking_index, tracking_index_name,
            snapshot_date, snapshot_stale_days, shares, cost_amount,
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
