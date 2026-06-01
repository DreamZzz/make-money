"""F4-v3: net_action 合成层测试。"""
from __future__ import annotations

from src.funds.net_action import (
    ADD_TO_TARGET,
    ADD_WINDOW_OPEN,
    CONSIDER_SWITCH,
    EXIT_NOW,
    HOLD_AS_PLANNED,
    HOLD_WAIT_TREND,
    REDUCE_TO_TARGET,
    derive_net_action,
)


def _alert(alert_type, level="warning", metric_value=None, headline=""):
    return {"alert_type": alert_type, "alert_level": level,
            "metric_value": metric_value, "headline": headline}


def test_no_alerts_returns_hold_as_planned():
    r = derive_net_action([])
    assert r.net_action == HOLD_AS_PLANNED


def test_critical_stop_loss_returns_exit_now():
    r = derive_net_action([_alert("stop_loss", level="critical",
                                  headline="持仓收益 -10%")])
    assert r.net_action == EXIT_NOW
    assert "止损" in r.headline
    assert "stop_loss" in r.primary_alert_types


def test_critical_overrides_other_warnings():
    r = derive_net_action([
        _alert("stop_loss", level="critical"),
        _alert("target_drift", level="warning", metric_value=-0.5),
        _alert("alternative_available", level="info"),
    ])
    assert r.net_action == EXIT_NOW


def test_ma60_break_blocks_underweight_add():
    """真实 013308 场景:ma60_break + target_drift -53% + alternative
    应合成 HOLD_WAIT_TREND,headline 提及"等趋势回头再补"和"替代品"。"""
    r = derive_net_action([
        _alert("ma60_break", level="warning"),
        _alert("trend_weak", level="info"),
        _alert("target_drift", level="warning", metric_value=-0.53),
        _alert("alternative_available", level="info"),
    ])
    assert r.net_action == HOLD_WAIT_TREND
    assert "等趋势" in r.headline
    assert "M4 欠配" in r.headline or "趋势回头" in r.headline
    assert "替代" in r.headline
    # 参与合成的告警类型记录
    assert "ma60_break" in r.primary_alert_types
    assert "target_drift" in r.primary_alert_types
    assert "alternative_available" in r.primary_alert_types


def test_drawdown_alone_triggers_hold_wait_trend():
    r = derive_net_action([_alert("drawdown_10d", level="warning", metric_value=-0.10)])
    assert r.net_action == HOLD_WAIT_TREND
    assert "回撤" in r.headline


def test_short_term_healthy_plus_underweight_returns_add_to_target():
    r = derive_net_action([
        _alert("target_drift", level="warning", metric_value=-0.30),
    ])
    assert r.net_action == ADD_TO_TARGET
    assert "可按 M4 目标补" in r.headline


def test_add_to_target_mentions_alternative_when_available():
    r = derive_net_action([
        _alert("target_drift", level="warning", metric_value=-0.30),
        _alert("alternative_available", level="info"),
    ])
    assert r.net_action == ADD_TO_TARGET
    assert "alternative" in r.headline or "替代品" in r.headline


def test_short_term_healthy_plus_overweight_returns_reduce_to_target():
    r = derive_net_action([
        _alert("target_drift", level="warning", metric_value=0.30),
    ])
    assert r.net_action == REDUCE_TO_TARGET


def test_only_alternative_returns_consider_switch():
    r = derive_net_action([_alert("alternative_available", level="info")])
    assert r.net_action == CONSIDER_SWITCH
    assert "替代" in r.headline


def test_add_window_open_alone_returns_add_window_open():
    r = derive_net_action([_alert("add_window_open", level="info")])
    assert r.net_action == ADD_WINDOW_OPEN


def test_trend_weak_alone_does_not_block():
    """仅 trend_weak(MA120 跌穿) 但 MA60 未破时,不进入 HOLD_WAIT_TREND。

    设计:trend_weak 是 info 级别,自身不算"短期 unhealthy"的足够条件;
    需要 ma60_break 或 drawdown_10d 才走 wait 路径。"""
    r = derive_net_action([_alert("trend_weak", level="info")])
    assert r.net_action == HOLD_AS_PLANNED
