"""F4-v3: 持仓基金告警合成层。

monitoring.py 的 6 类告警是单维度独立检测;放一起会显得矛盾(短期 ma60_break
说减、长期 target_drift 说加、alternative_available 说切换)。这里按优先级
合成一个"今日该怎么办"的 net_action,主页面显示一行,原始告警折叠到下面看。

优先级(从高到低):
  1. EXIT_NOW         — 触发 critical (stop_loss)
  2. HOLD_WAIT_TREND  — 短期趋势破(MA60 跌穿) 或近期深度回撤,不补不减
  3. ADD_TO_TARGET    — 短期健康 + M4 欠配,可按目标补;有 alternative 则补替代品
  4. REDUCE_TO_TARGET — 短期健康 + M4 超配,可按目标回撤
  5. CONSIDER_SWITCH  — 仅 alternative_available,无配置缺口
  6. ADD_WINDOW_OPEN  — scanner 把持仓本身标 in_window
  7. HOLD_AS_PLANNED  — 都不命中,按 M4 目标持有
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# net_action 枚举
EXIT_NOW = "EXIT_NOW"
HOLD_WAIT_TREND = "HOLD_WAIT_TREND"
ADD_TO_TARGET = "ADD_TO_TARGET"
REDUCE_TO_TARGET = "REDUCE_TO_TARGET"
CONSIDER_SWITCH = "CONSIDER_SWITCH"
ADD_WINDOW_OPEN = "ADD_WINDOW_OPEN"
HOLD_AS_PLANNED = "HOLD_AS_PLANNED"
NO_DATA = "NO_DATA"


@dataclass
class NetAction:
    net_action: str
    headline: str
    reasoning: str             # 触发了哪条规则
    primary_alert_types: list[str]   # 参与合成的告警类型


def _has(alerts: list[dict[str, Any]], alert_type: str) -> bool:
    return any(a.get("alert_type") == alert_type for a in alerts)


def _by_type(alerts: list[dict[str, Any]], alert_type: str) -> list[dict[str, Any]]:
    return [a for a in alerts if a.get("alert_type") == alert_type]


def _target_drift_direction(alerts: list[dict[str, Any]]) -> str | None:
    """target_drift 的 metric_value > 0 为超配,< 0 为欠配。"""
    rows = _by_type(alerts, "target_drift")
    if not rows:
        return None
    v = rows[0].get("metric_value")
    if v is None:
        return None
    return "over" if v > 0 else "under"


def derive_net_action(alerts: list[dict[str, Any]]) -> NetAction:
    """把一支基金的所有 alerts 合成一个 net_action。

    alerts 是 monitoring.HoldingAlert 的 asdict 列表(或 fund_holding_alerts 表行)。
    """
    if not alerts:
        return NetAction(
            net_action=HOLD_AS_PLANNED,
            headline="无告警,按计划持有",
            reasoning="无任何告警触发",
            primary_alert_types=[],
        )

    # 1. EXIT_NOW: critical 优先
    critical = [a for a in alerts if a.get("alert_level") == "critical"]
    if critical:
        types = sorted({a["alert_type"] for a in critical})
        first = critical[0]
        return NetAction(
            net_action=EXIT_NOW,
            headline=f"触发止损 ({first['alert_type']}),优先平仓",
            reasoning=first.get("headline") or "critical 级别告警",
            primary_alert_types=types,
        )

    # 短期/中期趋势状态
    ma60_broken = _has(alerts, "ma60_break")
    ma120_broken = _has(alerts, "trend_weak")
    dd_warn = _has(alerts, "drawdown_10d")
    short_term_unhealthy = ma60_broken or dd_warn

    drift_dir = _target_drift_direction(alerts)
    has_alternative = _has(alerts, "alternative_available")
    add_window_open = _has(alerts, "add_window_open")

    # 2. HOLD_WAIT_TREND: 短期破位/深度回撤,无论 M4 缺口与否
    if short_term_unhealthy:
        parts: list[str] = []
        used: list[str] = []
        if ma60_broken and ma120_broken:
            parts.append("短期跌穿 MA60 + 中期 MA120 转弱")
            used.extend(["ma60_break", "trend_weak"])
        elif ma60_broken:
            parts.append("短期跌穿 MA60")
            used.append("ma60_break")
        elif dd_warn:
            parts.append("近 10 日深度回撤")
            used.append("drawdown_10d")
        head = f"等趋势确认 ({parts[0]}),不补不减"
        reasoning_parts = ["短期/中期价格信号 unhealthy → 不补"]
        if drift_dir == "under":
            head += ";M4 欠配的事先放着,趋势回头再补"
            reasoning_parts.append("有 target_drift 欠配,但短期信号否决加仓")
            used.append("target_drift")
        if has_alternative:
            head += ";要补的话考虑替代品"
            reasoning_parts.append("有 alternative_available,改善标的选择")
            used.append("alternative_available")
        return NetAction(
            net_action=HOLD_WAIT_TREND,
            headline=head,
            reasoning="；".join(reasoning_parts),
            primary_alert_types=sorted(set(used)),
        )

    # 3. ADD_TO_TARGET: 短期健康 + M4 欠配
    if drift_dir == "under":
        head = "M4 欠配 + 短期趋势健康,可按 M4 目标补"
        used = ["target_drift"]
        reasoning = "target_drift < 0 + 短期信号 healthy"
        if has_alternative:
            head += " (优先用 alternative 替代品,综合分更高)"
            used.append("alternative_available")
            reasoning += "；同时有 alternative_available 提示标的层有更强选择"
        return NetAction(
            net_action=ADD_TO_TARGET,
            headline=head,
            reasoning=reasoning,
            primary_alert_types=sorted(used),
        )

    # 4. REDUCE_TO_TARGET: 短期健康 + M4 超配
    if drift_dir == "over":
        return NetAction(
            net_action=REDUCE_TO_TARGET,
            headline="M4 超配 + 短期趋势健康,可按 M4 目标回撤",
            reasoning="target_drift > 0 + 短期信号 healthy",
            primary_alert_types=["target_drift"],
        )

    # 5. CONSIDER_SWITCH: 仅 alternative
    if has_alternative:
        return NetAction(
            net_action=CONSIDER_SWITCH,
            headline="同跟踪指数有更强替代,下次定投考虑切换 (评估费率/账户类型/便利度)",
            reasoning="alternative_available 单独触发,无配置/趋势压力",
            primary_alert_types=["alternative_available"],
        )

    # 6. ADD_WINDOW_OPEN: scanner 把持仓自身标 in_window
    if add_window_open:
        return NetAction(
            net_action=ADD_WINDOW_OPEN,
            headline="scanner 把本基金标 in_window,趋势+估值+宏观三者达标,可考虑分批",
            reasoning="add_window_open 触发,无其它阻挡",
            primary_alert_types=["add_window_open"],
        )

    # 7. 默认 HOLD
    return NetAction(
        net_action=HOLD_AS_PLANNED,
        headline="无关键告警,按 M4 目标持有",
        reasoning="无 critical/趋势破/配置缺口/替代提示触发",
        primary_alert_types=[],
    )
