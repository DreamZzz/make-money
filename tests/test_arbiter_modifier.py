"""R3: earnings context modifier 测试。"""
from __future__ import annotations

import uuid
from datetime import date

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.financials.arbiter_modifier import (
    apply_earnings_modifier,
    load_recent_earnings_context,
)


def _seed_alert(conn, symbol, sentiment, impact, event_date=date(2026, 6, 1)):
    conn.execute(
        "INSERT INTO earnings_alerts (alert_id, symbol, report_period, event_type, "
        "event_date, sentiment, impact_score, headline) "
        "VALUES (?, ?, DATE '2026-03-31', 'EXPRESS', ?, ?, ?, 'x')",
        [f"A-{symbol}-{uuid.uuid4().hex[:6]}", symbol, event_date, sentiment, impact],
    )


def test_load_context_returns_latest_per_symbol():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_alert(conn, "X", "POSITIVE", 0.5, event_date=date(2026, 5, 20))
    _seed_alert(conn, "X", "NEGATIVE", -0.4, event_date=date(2026, 6, 1))
    _seed_alert(conn, "Y", "NEUTRAL", 0.0)
    ctx = load_recent_earnings_context(conn, as_of=date(2026, 6, 2), lookback_days=30)
    assert ctx["X"]["sentiment"] == "NEGATIVE"  # 最新
    assert ctx["Y"]["sentiment"] == "NEUTRAL"


def test_load_context_outside_lookback_window_excluded():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_alert(conn, "OLD", "POSITIVE", 0.5, event_date=date(2026, 4, 1))
    ctx = load_recent_earnings_context(conn, as_of=date(2026, 6, 1), lookback_days=30)
    assert "OLD" not in ctx


def test_modifier_boosts_buy_with_positive():
    decisions = pd.DataFrame([{
        "symbol": "X", "side": "BUY", "decision": "ACCEPTED",
        "priority_score": 0.5, "decision_reason": "consensus",
    }])
    ctx = {"X": {"sentiment": "POSITIVE", "impact_score": 0.6,
                  "event_date": date(2026, 6, 1), "event_type": "EXPRESS",
                  "headline": "x"}}
    out = apply_earnings_modifier(decisions, ctx, {"weight": 0.15})
    assert out.loc[0, "priority_score"] > 0.5  # +0.09
    assert "earnings:POSITIVE" in out.loc[0, "decision_reason"]


def test_modifier_inverts_sign_for_sell_with_negative():
    """SELL 信号 + NEGATIVE earnings → 利空对 SELL 加分。"""
    decisions = pd.DataFrame([{
        "symbol": "X", "side": "SELL", "decision": "ACCEPTED",
        "priority_score": 0.4, "decision_reason": "rule",
    }])
    ctx = {"X": {"sentiment": "NEGATIVE", "impact_score": -0.6,
                  "event_date": date(2026, 6, 1), "event_type": "FORECAST",
                  "headline": "x"}}
    out = apply_earnings_modifier(decisions, ctx, {"weight": 0.15})
    # SELL 翻号: -0.15 * -0.6 = +0.09
    assert out.loc[0, "priority_score"] > 0.4
    assert "earnings:NEGATIVE" in out.loc[0, "decision_reason"]


def test_modifier_buy_negative_block_rejects():
    decisions = pd.DataFrame([{
        "symbol": "X", "side": "BUY", "decision": "ACCEPTED",
        "priority_score": 0.5, "decision_reason": "consensus",
    }])
    ctx = {"X": {"sentiment": "NEGATIVE", "impact_score": -0.6,
                  "event_date": date(2026, 6, 1), "event_type": "EXPRESS",
                  "headline": "x"}}
    out = apply_earnings_modifier(decisions, ctx,
                                    {"weight": 0.15, "buy_negative_block": True})
    assert out.loc[0, "decision"] == "REJECTED"
    assert "拒收 BUY" in out.loc[0, "decision_reason"]


def test_modifier_no_context_returns_unchanged():
    decisions = pd.DataFrame([{
        "symbol": "X", "side": "BUY", "decision": "ACCEPTED",
        "priority_score": 0.5, "decision_reason": "consensus",
    }])
    out = apply_earnings_modifier(decisions, {}, {"weight": 0.15})
    assert out.loc[0, "priority_score"] == 0.5
    assert out.loc[0, "decision_reason"] == "consensus"


def test_modifier_empty_decisions_returns_empty():
    out = apply_earnings_modifier(pd.DataFrame(), {"X": {"sentiment": "POSITIVE",
                                                          "impact_score": 0.6}},
                                    {"weight": 0.15})
    assert out.empty
