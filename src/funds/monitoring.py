"""F3: 持仓基金日维度风险监控 + 严格告警。

用户决策(2026-05-30)严格档:
- stop_loss:     holding_return_pct < -5%       → exit_stop_loss
- ma60_break:    nav 跌破 MA60                  → reduce_partial
- drawdown_10d:  近 10 日回撤 > 8%              → reduce_partial
- trend_weak:    nav < MA120 (与 scanner 一致)  → monitor
- target_drift:  current_account_weight 偏离 M4 > 20%  → reduce_partial / add_window_open
- add_window_open: 基金被 scanner 判 in_window  → add_window_open

只对真实持仓(intent != exited 且 shares > 0)跑。已退出的不产生告警。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np  # noqa: F401  - 保留供未来扩展(年化波动等)
import pandas as pd
from loguru import logger

from src.index_funds.config import get_watchlist

# 严格阈值
STOP_LOSS_PCT = -0.05         # holding_return_pct < -5%
DRAWDOWN_10D_PCT = -0.08      # 10 日回撤 < -8%
MA60_WINDOW = 60
MA120_WINDOW = 120
TARGET_DRIFT_PCT = 0.20       # |current - target| / target > 20%
# G2: 同跟踪指数有综合分高 +N 的替代品 → 提示
ALTERNATIVE_BEAT_DELTA = 5.0
ALTERNATIVE_TOP_N = 2         # 每个持仓最多提示 N 个更强替代


@dataclass
class HoldingAlert:
    eval_date: date
    fund_code: str
    alert_type: str
    alert_level: str           # info / warning / critical
    metric_name: str
    metric_value: float | None
    threshold: float | None
    suggested_action: str      # hold / reduce_partial / exit_stop_loss / add_window_open / monitor
    headline: str


def _ma(series: pd.Series, window: int) -> float | None:
    if len(series) < max(window // 2, 20):
        return None
    ma = series.rolling(window, min_periods=max(window // 2, 20)).mean().iloc[-1]
    return None if pd.isna(ma) else float(ma)


def _recent_drawdown(series: pd.Series, n_days: int) -> float | None:
    """近 n 日内 (max - min) / max。回撤定义:取最近 n+1 日 nav,从局部最高跌到当前的比例。"""
    if len(series) < n_days + 1:
        return None
    recent = series.tail(n_days + 1)
    rolling_max = recent.cummax()
    dd = (recent / rolling_max - 1.0)
    return float(dd.iloc[-1])  # 当前距 n+1 日内最高的回撤


def _load_holding_funds(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """读取当前真实持仓的基金: snapshot.shares > 0 且 watchlist intent != exited。

    把 watchlist 的 category/intent + snapshot 的 shares/cost 拼到一行。
    """
    watchlist = {item.fund_code: item for item in get_watchlist()}
    snapshots = conn.execute(
        """
        SELECT fund_code, snapshot_date, shares, cost_amount, note
        FROM index_fund_snapshots
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY fund_code
            ORDER BY snapshot_date DESC, created_at DESC
        ) = 1
        """
    ).fetchall()
    out = []
    for fc, snap_date, shares, cost, note in snapshots:
        if not shares or shares <= 0:
            continue
        item = watchlist.get(fc)
        if item is None or item.intent == "exited":
            continue
        out.append({
            "fund_code": fc,
            "category": item.category,
            "intent": item.intent,
            "shares": float(shares),
            "cost": float(cost or 0),
            "snapshot_date": snap_date,
            "note": note or "",
        })
    return out


def _broker_return_pct(note: str) -> float | None:
    """优先用 broker note 里的 holding_return_pct。"""
    import json
    if not note or not note.strip().startswith("{"):
        return None
    try:
        data = json.loads(note)
    except Exception:
        return None
    v = data.get("holding_return_pct")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_holding_alerts(
    conn: duckdb.DuckDBPyConnection,
    holding: dict[str, Any],
    *,
    eval_date: date,
    in_window_set: set[str],
    m4_weights: dict[str, float],
    equity_exposure: float | None,
    account_total: float | None,
) -> list[HoldingAlert]:
    fund_code = holding["fund_code"]
    category = holding["category"]
    alerts: list[HoldingAlert] = []

    nav_df = conn.execute(
        "SELECT trade_date, nav FROM fund_nav WHERE fund_code = ? AND nav IS NOT NULL ORDER BY trade_date",
        [fund_code],
    ).fetchdf()
    if nav_df.empty:
        return alerts

    nav_series = nav_df["nav"].astype(float)
    latest_nav = float(nav_series.iloc[-1])

    # 1. stop_loss: 优先 broker return,缺则用 cost
    ret = _broker_return_pct(holding["note"])
    if ret is None and holding["cost"] > 0:
        ret = (holding["shares"] * latest_nav - holding["cost"]) / holding["cost"]
    if ret is not None and ret < STOP_LOSS_PCT:
        alerts.append(HoldingAlert(
            eval_date=eval_date, fund_code=fund_code,
            alert_type="stop_loss", alert_level="critical",
            metric_name="holding_return_pct",
            metric_value=ret, threshold=STOP_LOSS_PCT,
            suggested_action="exit_stop_loss",
            headline=f"持仓收益 {ret:.1%} < 止损线 {STOP_LOSS_PCT:.0%},建议止损离场",
        ))

    # 2. ma60_break - 仅对权益类有意义
    if category in {"equity_index", "broad", "qdii"}:
        ma60 = _ma(nav_series, MA60_WINDOW)
        if ma60 is not None and latest_nav < ma60:
            alerts.append(HoldingAlert(
                eval_date=eval_date, fund_code=fund_code,
                alert_type="ma60_break", alert_level="warning",
                metric_name="nav_vs_ma60",
                metric_value=latest_nav / ma60 - 1, threshold=0.0,
                suggested_action="reduce_partial",
                headline=f"nav {latest_nav:.4f} 跌穿 MA60 {ma60:.4f},建议部分减仓",
            ))

        # 3. drawdown_10d
        dd10 = _recent_drawdown(nav_series, 10)
        if dd10 is not None and dd10 < DRAWDOWN_10D_PCT:
            alerts.append(HoldingAlert(
                eval_date=eval_date, fund_code=fund_code,
                alert_type="drawdown_10d", alert_level="warning",
                metric_name="drawdown_10d",
                metric_value=dd10, threshold=DRAWDOWN_10D_PCT,
                suggested_action="reduce_partial",
                headline=f"近 10 日回撤 {dd10:.1%} > 阈值 {DRAWDOWN_10D_PCT:.0%},建议部分减仓",
            ))

        # 4. trend_weak (nav < MA120)
        ma120 = _ma(nav_series, MA120_WINDOW)
        if ma120 is not None and latest_nav < ma120:
            # 已在 ma60_break 里给了 reduce 建议,这里只给 info 提示
            alerts.append(HoldingAlert(
                eval_date=eval_date, fund_code=fund_code,
                alert_type="trend_weak", alert_level="info",
                metric_name="nav_vs_ma120",
                metric_value=latest_nav / ma120 - 1, threshold=0.0,
                suggested_action="monitor",
                headline=f"nav 跌穿 MA120 {ma120:.4f},长期趋势转弱",
            ))

    # 5. target_drift - 仅对 active 且权益类
    if category in {"equity_index", "broad", "qdii"} and holding["intent"] == "active":
        m4_w = m4_weights.get(fund_code)
        if m4_w is not None and equity_exposure is not None and account_total is not None:
            current_value = holding["shares"] * latest_nav
            target_acct = float(equity_exposure) * float(m4_w)
            target_value = account_total * target_acct
            if target_value > 0:
                drift = (current_value - target_value) / target_value
                if abs(drift) > TARGET_DRIFT_PCT:
                    if drift > 0:  # 超配
                        action, headline = ("reduce_partial",
                            f"持仓 {current_value/10000:.1f} 万 超目标 {target_value/10000:.1f} 万 (+{drift:.0%}),建议减至目标")
                    else:
                        action, headline = ("add_window_open",
                            f"持仓 {current_value/10000:.1f} 万 低于目标 {target_value/10000:.1f} 万 ({drift:.0%}),欠配")
                    alerts.append(HoldingAlert(
                        eval_date=eval_date, fund_code=fund_code,
                        alert_type="target_drift", alert_level="warning",
                        metric_name="account_weight_drift",
                        metric_value=drift, threshold=TARGET_DRIFT_PCT,
                        suggested_action=action,
                        headline=headline,
                    ))

    # 6. add_window_open - 来自 scanner
    if fund_code in in_window_set:
        alerts.append(HoldingAlert(
            eval_date=eval_date, fund_code=fund_code,
            alert_type="add_window_open", alert_level="info",
            metric_name="scanner_signal_tag",
            metric_value=None, threshold=None,
            suggested_action="add_window_open",
            headline=f"扫描器判定 {fund_code} 进入加仓窗口期 (in_window)",
        ))

    # 7. alternative_available - G2: 同跟踪指数有综合分显著更高的候选
    # 查持仓自身在 scanner 表里的最新 total_score,再查同 tracking 其它 ETF 的 total_score
    tracking = conn.execute(
        "SELECT tracking_index FROM fund_info WHERE fund_code = ? LIMIT 1",
        [fund_code],
    ).fetchone()
    tracking_idx = tracking[0] if tracking else None
    if tracking_idx and category in {"equity_index", "broad", "qdii"}:
        held_score_row = conn.execute(
            "SELECT total_score FROM fund_screening_results "
            "WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results) "
            "AND fund_code = ? LIMIT 1",
            [fund_code],
        ).fetchone()
        held_score = float(held_score_row[0]) if held_score_row and held_score_row[0] is not None else None
        if held_score is not None:
            alt_rows = conn.execute(
                "SELECT fund_code, fund_name, total_score FROM fund_screening_results "
                "WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results) "
                "AND tracking_index = ? AND fund_code != ? AND total_score IS NOT NULL "
                "AND total_score > ? + ? "
                "ORDER BY total_score DESC LIMIT ?",
                [tracking_idx, fund_code, held_score, ALTERNATIVE_BEAT_DELTA, ALTERNATIVE_TOP_N],
            ).fetchall()
            if alt_rows:
                # Top N 候选合并成一条告警(PK = eval_date+fund_code+alert_type)
                parts = [f"{c} {n or ''} 综合分 {s:.0f} (+{float(s)-held_score:.0f})"
                         for c, n, s in alt_rows]
                best_delta = float(alt_rows[0][2]) - held_score
                alerts.append(HoldingAlert(
                    eval_date=eval_date, fund_code=fund_code,
                    alert_type="alternative_available", alert_level="info",
                    metric_name="score_delta_vs_alternative",
                    metric_value=best_delta, threshold=ALTERNATIVE_BEAT_DELTA,
                    suggested_action="consider_switch",
                    headline=(
                        f"同跟踪 {tracking_idx} 有更强替代 (本基金 {held_score:.0f}): "
                        + "; ".join(parts)
                    ),
                ))

    return alerts


def monitor_holdings(
    conn: duckdb.DuckDBPyConnection,
    *,
    eval_date: date | None = None,
    persist: bool = False,
) -> list[HoldingAlert]:
    """对当前所有持仓基金跑严格告警检查。"""
    if eval_date is None:
        row = conn.execute("SELECT MAX(trade_date) FROM fund_nav").fetchone()
        eval_date = row[0] if row and row[0] else date.today()

    # 拉 scanner 最新 in_window
    in_window: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT fund_code FROM fund_screening_results "
            "WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results) "
            "AND signal_tag = 'in_window'"
        ).fetchall()
        in_window = {r[0] for r in rows}
    except Exception:
        pass

    # M4 权重 + 宏观
    from src.index_funds.signals import load_m4_weights
    m4 = load_m4_weights(conn)
    exp_row = conn.execute(
        "SELECT target_exposure FROM market_exposure ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    equity_exposure = float(exp_row[0]) if exp_row and exp_row[0] is not None else None
    acc_row = conn.execute(
        "SELECT total_value FROM account_daily WHERE account_id='default' ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    account_total = float(acc_row[0]) if acc_row and acc_row[0] is not None else None

    out: list[HoldingAlert] = []
    for holding in _load_holding_funds(conn):
        try:
            out.extend(evaluate_holding_alerts(
                conn, holding,
                eval_date=eval_date, in_window_set=in_window,
                m4_weights=m4, equity_exposure=equity_exposure,
                account_total=account_total,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"monitor {holding['fund_code']} failed: {exc}")
    if persist and out:
        _persist(conn, out)
    return out


def _persist(conn: duckdb.DuckDBPyConnection, alerts: list[HoldingAlert]) -> None:
    df = pd.DataFrame([asdict(a) for a in alerts])
    eval_dates = df["eval_date"].dropna().unique().tolist()
    for d in eval_dates:
        conn.execute("DELETE FROM fund_holding_alerts WHERE eval_date = ?", [d])
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_alerts AS SELECT * FROM df")
    conn.execute(
        """
        INSERT INTO fund_holding_alerts (
            eval_date, fund_code, alert_type, alert_level, metric_name, metric_value,
            threshold, suggested_action, headline
        )
        SELECT
            eval_date, fund_code, alert_type, alert_level, metric_name, metric_value,
            threshold, suggested_action, headline
        FROM _tmp_alerts
        """
    )


def load_latest_alerts(conn: duckdb.DuckDBPyConnection, *, fund_code: str | None = None) -> list[dict[str, Any]]:
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='fund_holding_alerts'"
    ).fetchone():
        return []
    base = (
        "SELECT * FROM fund_holding_alerts "
        "WHERE eval_date = (SELECT MAX(eval_date) FROM fund_holding_alerts)"
    )
    if fund_code:
        df = conn.execute(base + " AND fund_code = ?", [fund_code]).fetchdf()
    else:
        df = conn.execute(base).fetchdf()
    return df.to_dict(orient="records") if not df.empty else []
