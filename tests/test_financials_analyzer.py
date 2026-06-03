"""R2: analyzer + calendar 测试。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.financials.analyzer import (
    EarningsAlert,
    analyze_express,
    analyze_forecast,
    backfill_post_event_returns,
    compute_sentiment,
    persist_alerts,
)


def test_compute_sentiment_strong_positive():
    s, score = compute_sentiment(50.0, surprise=20.0, industry_rank=0.85, cf_quality=1.2)
    assert s == "POSITIVE"
    assert score > 0.6


def test_compute_sentiment_strong_negative():
    s, score = compute_sentiment(-40.0, surprise=-30.0, industry_rank=0.1, cf_quality=0.2)
    assert s == "NEGATIVE"
    assert score < -0.6


def test_compute_sentiment_neutral_baseline():
    s, score = compute_sentiment(2.0)
    assert s == "NEUTRAL"
    assert -0.3 <= score <= 0.3


def test_compute_sentiment_none_returns_neutral():
    s, score = compute_sentiment(None)
    assert s == "NEUTRAL"
    assert score == 0.0


def test_analyze_forecast_basic_positive():
    row = pd.Series({
        "symbol": "600519", "report_period": date(2026, 6, 30),
        "event_date": date(2026, 6, 2),
        "forecast_text": "预增",
        "np_change_min": 25.0, "np_change_max": 45.0,
    })
    alert = analyze_forecast(row, industry_rank=0.8)
    assert alert.event_type == "FORECAST"
    assert alert.np_change_mid == 35.0
    assert alert.sentiment == "POSITIVE"
    assert alert.impact_score > 0.3
    assert "净利同比中值 +35%" in alert.headline
    assert "POSITIVE" in alert.headline


def test_analyze_forecast_loss_text_triggers_risk_tag():
    row = pd.Series({
        "symbol": "002000", "report_period": date(2026, 6, 30),
        "event_date": date(2026, 6, 2),
        "forecast_text": "首亏",
        "np_change_min": -60.0, "np_change_max": -40.0,
    })
    alert = analyze_forecast(row)
    assert alert.sentiment == "NEGATIVE"
    assert "loss_forecast" in alert.risk_tags
    assert "severe_np_decline" in alert.risk_tags


def test_analyze_express_uses_surprise_when_forecast_provided():
    row = pd.Series({
        "symbol": "000001", "report_period": date(2026, 3, 31),
        "event_date": date(2026, 4, 28),
        "revenue_yoy": 12.5, "np_yoy": 30.0,
    })
    # 之前预告净利 +10%,实际 +30% → surprise +20pp
    alert = analyze_express(row, forecast_np_mid=10.0)
    assert alert.surprise_pct == 20.0
    assert alert.sentiment == "POSITIVE"
    assert "vs 预告 +20%" in alert.headline


def test_persist_alerts_inserts_and_dedups():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    alerts = [
        EarningsAlert(alert_id="A1", symbol="600519", report_period=date(2026, 6, 30),
                       event_type="FORECAST", event_date=date(2026, 6, 2),
                       sentiment="POSITIVE", impact_score=0.6, headline="x"),
        EarningsAlert(alert_id="A2", symbol="000001", report_period=date(2026, 3, 31),
                       event_type="EXPRESS", event_date=date(2026, 4, 28),
                       sentiment="NEUTRAL", impact_score=0.0, headline="y"),
    ]
    n = persist_alerts(conn, alerts)
    assert n == 2
    rows = conn.execute("SELECT COUNT(*) FROM earnings_alerts").fetchone()
    assert rows[0] == 2


def test_backfill_post_event_returns():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    # seed 一个 alert + 6 天 daily_price (event_date 后第 1 / 5 天)
    event = date(2026, 5, 20)
    conn.execute(
        "INSERT INTO earnings_alerts (alert_id, symbol, report_period, event_type, "
        "event_date, sentiment, impact_score, headline) "
        "VALUES ('A1','X', DATE '2026-03-31', 'EXPRESS', ?, 'POSITIVE', 0.5, 'x')",
        [event],
    )
    # base = 100 (event 当日)
    conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('X', ?, 100)", [event])
    # seed 6 个交易日 (event+1 起),价格逐渐上升
    for i, p in enumerate([102, 103, 104, 105, 106, 108], start=1):
        conn.execute("INSERT INTO daily_price (symbol, trade_date, close) VALUES ('X', ?, ?)",
                     [event + timedelta(days=i), p])
    n = backfill_post_event_returns(conn)
    assert n == 1
    row = conn.execute(
        "SELECT post_event_return_1d, post_event_return_5d FROM earnings_alerts WHERE alert_id='A1'"
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None
    assert row[1] > row[0]  # 5 日累计 > 1 日
