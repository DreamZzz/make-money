"""F4: 推荐引擎测试。"""
from __future__ import annotations

import duckdb

from src.data_pipeline.loader import init_db
from src.funds.recommendations import build_recommendations


def _patch_watchlist(monkeypatch, exited_codes=()):
    from src.index_funds.config import FundWatchItem
    items = []
    for code in exited_codes:
        items.append(FundWatchItem(
            fund_code=code, name=code, fund_type="ETF",
            tracking_index="x", tracking_index_name="x",
            market="CN", currency="CNY", target_weight=0.0,
            category="equity_index", intent="exited",
        ))
    monkeypatch.setattr("src.funds.recommendations.get_watchlist", lambda: items)


def _seed_screening(conn, rows):
    """rows = list[(fund_code, name, sub, tracking, signal_tag, total_score)]"""
    for code, name, sub, tracking, tag, score in rows:
        conn.execute(
            "INSERT INTO fund_screening_results "
            "(eval_date, fund_code, fund_name, etf_subcategory, tracking_index, "
            " signal_tag, total_score, scale_yi, price_pct, trend_score, macro_score, return_6m, thesis) "
            "VALUES (DATE '2026-05-29', ?, ?, ?, ?, ?, ?, 100, 0.3, 80, 100, 0.05, '测试')",
            [code, name, sub, tracking, tag, score],
        )


def _seed_holding(conn, fund_code, *, tracking, sub):
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, etf_subcategory, market, currency, enabled) "
        "VALUES (?, ?, 'ETF', ?, ?, 'CN', 'CNY', TRUE)",
        [fund_code, fund_code, tracking, sub],
    )
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES (?, DATE '2026-05-29', ?, 1000, 10000)",
        [f"S-{fund_code}", fund_code],
    )


def test_in_window_returns_top_by_score(monkeypatch):
    """排序按 total_score 倒序。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_screening(conn, [
        ("A", "A", "broad", "000300", "in_window", 90),
        ("B", "B", "sector", "x1", "in_window", 75),
        ("C", "C", "qdii", "x2", "in_window", 60),
    ])
    snap = build_recommendations(conn, top_in_window=10, max_per_category=10)
    codes = [r.fund_code for r in snap.in_window]
    assert codes == ["A", "B", "C"]
    assert snap.in_window[0].rank == 1


def test_excludes_held_funds(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_holding(conn, "HELD", tracking="000300", sub="broad")
    _seed_screening(conn, [
        ("HELD", "持有的", "broad", "000300", "in_window", 95),
        ("X", "X", "qdii", "HSTECH", "in_window", 80),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert codes == ["X"]
    assert "HELD" in snap.excluded_holdings


def test_excludes_same_tracking_index(monkeypatch):
    """持仓 tracking=000300,推荐里所有 000300 跟踪的都排除。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_holding(conn, "MY300", tracking="000300", sub="broad")
    _seed_screening(conn, [
        ("OTHER300", "其它沪深300", "broad", "000300", "in_window", 90),
        ("X", "X", "qdii", "HSTECH", "in_window", 70),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert codes == ["X"]


def test_excludes_exited(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, exited_codes=["DEAD"])
    _seed_screening(conn, [
        ("DEAD", "已退出", "broad", "y1", "in_window", 95),
        ("X", "X", "qdii", "HSTECH", "in_window", 80),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert codes == ["X"]


def test_max_per_category_diversity(monkeypatch):
    """同 etf_subcategory 至多 N 支。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_screening(conn, [
        ("B1", "B1", "broad", "ix1", "in_window", 90),
        ("B2", "B2", "broad", "ix2", "in_window", 85),
        ("B3", "B3", "broad", "ix3", "in_window", 80),  # 应被截
        ("Q1", "Q1", "qdii", "qx1", "in_window", 70),
    ])
    snap = build_recommendations(conn, top_in_window=10, max_per_category=2)
    codes = [r.fund_code for r in snap.in_window]
    assert "B3" not in codes
    assert "B1" in codes and "B2" in codes
    assert "Q1" in codes


def test_watch_separate_from_in_window(monkeypatch):
    """in_window 与 watch 列表互不重叠;两类同存时各按 signal_tag 归属。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_screening(conn, [
        ("A", "A", "broad", "i1", "in_window", 95),
        ("B", "B", "qdii", "i2", "watch_high_value", 80),
        ("C", "C", "sector", "i3", "watch_high_value", 75),
    ])
    snap = build_recommendations(conn, top_in_window=10, top_watch=10, max_per_category=10)
    in_w = [r.fund_code for r in snap.in_window]
    watch = [r.fund_code for r in snap.watch_high_value]
    assert in_w == ["A"]
    assert watch == ["B", "C"]


def test_empty_candidates_returns_friendly_advice(monkeypatch):
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    snap = build_recommendations(conn)
    assert snap.in_window == []
    assert snap.watch_high_value == []
    assert "无候选" in snap.overall_advice or "未填充" in snap.overall_advice
