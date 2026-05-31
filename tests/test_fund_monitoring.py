"""F3: 持仓基金严格告警测试。"""
from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb

from src.data_pipeline.loader import init_db
from src.funds.monitoring import (
    load_latest_alerts,
    monitor_holdings,
)


def _seed_nav(conn, fund_code, navs):
    """navs 是从早到晚的列表。"""
    base = date(2026, 5, 29) - timedelta(days=len(navs) - 1)
    rows = [(fund_code, base + timedelta(days=i), nav) for i, nav in enumerate(navs)]
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?, ?, ?)", rows)


def _seed_snapshot(conn, fund_code, *, shares=1000.0, cost=10000.0, note="",
                   snap_date=date(2026, 5, 29)):
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [f"S-{fund_code}", snap_date, fund_code, shares, cost, note],
    )


def _patch_watchlist(monkeypatch, items):
    monkeypatch.setattr("src.funds.monitoring.get_watchlist", lambda: items)


def _make_item(fund_code, *, category="equity_index", intent="active"):
    from src.index_funds.config import FundWatchItem
    return FundWatchItem(
        fund_code=fund_code, name=fund_code, fund_type="OPEN",
        tracking_index="000300", tracking_index_name="x",
        market="CN", currency="CNY", target_weight=0.33,
        category=category, intent=intent,
    )


def test_stop_loss_when_holding_return_below_minus_5pct(monkeypatch):
    """gross holding_return -10% → stop_loss critical."""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X")])
    _seed_nav(conn, "X", [1.0] * 100)  # 平稳
    note = json.dumps({"holding_return_pct": -0.10})
    _seed_snapshot(conn, "X", shares=1000, cost=10000, note=note)
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    types = {a.alert_type for a in alerts}
    assert "stop_loss" in types
    sl = next(a for a in alerts if a.alert_type == "stop_loss")
    assert sl.alert_level == "critical"
    assert sl.suggested_action == "exit_stop_loss"


def test_ma60_break_when_nav_below_ma(monkeypatch):
    """nav 从高位崩盘 → 跌穿 MA60。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X")])
    # 前 70 天 nav 1.5,最近 30 天暴跌到 1.0
    navs = [1.5] * 70 + [1.0] * 30
    _seed_nav(conn, "X", navs)
    _seed_snapshot(conn, "X", shares=1000, cost=1100, note="")  # 收益 -9% 也触发 stop_loss
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    types = {a.alert_type for a in alerts}
    assert "ma60_break" in types


def test_drawdown_10d_triggers_warning(monkeypatch):
    """近 10 日内出现 > 8% 回撤 → drawdown_10d。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X")])
    # 前 90 天 1.0,然后顶到 1.2,最近 5 天跌到 1.0(回撤 -17%)
    navs = [1.0] * 90 + [1.2] * 5 + [1.0] * 5
    _seed_nav(conn, "X", navs)
    _seed_snapshot(conn, "X", shares=1000, cost=1000, note="")  # 持平,不触发 stop_loss
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    types = {a.alert_type for a in alerts}
    assert "drawdown_10d" in types
    dd = next(a for a in alerts if a.alert_type == "drawdown_10d")
    assert dd.alert_level == "warning"


def test_exited_fund_produces_no_alerts(monkeypatch):
    """exited 状态不应产生任何告警。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X", intent="exited")])
    _seed_nav(conn, "X", [1.5] * 70 + [0.8] * 30)  # 大跌
    _seed_snapshot(conn, "X", shares=1000, cost=1500, note="")
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    assert alerts == []


def test_balanced_fund_skips_equity_only_alerts(monkeypatch):
    """balanced 类不参与 ma60_break / drawdown_10d / trend_weak / target_drift。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X", category="balanced")])
    _seed_nav(conn, "X", [1.5] * 70 + [1.0] * 30)
    _seed_snapshot(conn, "X", shares=1000, cost=1100, note=json.dumps({"holding_return_pct": -0.09}))
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    types = {a.alert_type for a in alerts}
    # stop_loss 仍触发(对所有类别),但 ma60/drawdown/trend_weak 应跳过
    assert "stop_loss" in types
    assert "ma60_break" not in types
    assert "drawdown_10d" not in types
    assert "trend_weak" not in types


def test_add_window_alert_when_scanner_flags_in_window(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X")])
    _seed_nav(conn, "X", [1.0] * 100)
    _seed_snapshot(conn, "X", shares=1000, cost=1000, note="")
    # seed scanner result
    conn.execute(
        "INSERT INTO fund_screening_results (eval_date, fund_code, signal_tag) "
        "VALUES (DATE '2026-05-29', 'X', 'in_window')"
    )
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    types = {a.alert_type for a in alerts}
    assert "add_window_open" in types


def test_persist_loads_back(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("X")])
    _seed_nav(conn, "X", [1.5] * 70 + [1.0] * 30)
    _seed_snapshot(conn, "X", shares=1000, cost=1100, note="")
    monitor_holdings(conn, eval_date=date(2026, 5, 29), persist=True)
    rows = load_latest_alerts(conn, fund_code="X")
    assert len(rows) > 0


def test_g2_alternative_available_when_same_tracking_beats_held(monkeypatch):
    """同 tracking 有 +5 以上综合分的候选 → alternative_available info。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("HELD")])
    _seed_nav(conn, "HELD", [1.0] * 100)
    _seed_snapshot(conn, "HELD", shares=1000, cost=1000, note="")
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('HELD', '我持有的', 'ETF', '000300', 'CN', 'CNY', TRUE)"
    )
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('BETTER', '更强 ETF', 'ETF', '000300', 'CN', 'CNY', TRUE)"
    )
    conn.execute(
        "INSERT INTO fund_screening_results (eval_date, fund_code, fund_name, tracking_index, total_score, signal_tag) "
        "VALUES (DATE '2026-05-29', 'HELD', '我持有的', '000300', 50, 'avoid')"
    )
    conn.execute(
        "INSERT INTO fund_screening_results (eval_date, fund_code, fund_name, tracking_index, total_score, signal_tag) "
        "VALUES (DATE '2026-05-29', 'BETTER', '更强 ETF', '000300', 75, 'in_window')"
    )
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    alts = [a for a in alerts if a.alert_type == "alternative_available"]
    assert len(alts) == 1
    assert alts[0].alert_level == "info"
    assert alts[0].suggested_action == "consider_switch"
    assert "BETTER" in alts[0].headline
    assert "75" in alts[0].headline


def test_g2_alternative_skipped_when_delta_small(monkeypatch):
    """候选只比持仓高 +3,< DELTA 5 → 不告警。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, [_make_item("HELD")])
    _seed_nav(conn, "HELD", [1.0] * 100)
    _seed_snapshot(conn, "HELD", shares=1000, cost=1000, note="")
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('HELD', 'H', 'ETF', '000300', 'CN', 'CNY', TRUE)"
    )
    conn.execute(
        "INSERT INTO fund_screening_results (eval_date, fund_code, tracking_index, total_score, signal_tag) "
        "VALUES (DATE '2026-05-29', 'HELD', '000300', 70, 'avoid')"
    )
    conn.execute(
        "INSERT INTO fund_screening_results (eval_date, fund_code, tracking_index, total_score, signal_tag) "
        "VALUES (DATE '2026-05-29', 'WEAKBETTER', '000300', 73, 'in_window')"
    )
    alerts = monitor_holdings(conn, eval_date=date(2026, 5, 29))
    alts = [a for a in alerts if a.alert_type == "alternative_available"]
    assert alts == []
