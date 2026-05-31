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


# ============ F4-v2 同跟踪指数智能比较测试 ============


def _seed_active_holding(conn, fund_code, *, tracking, sub, shares=1000, nav=1.0,
                         total_score=None, return_3m=None):
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, etf_subcategory, market, currency, enabled) "
        "VALUES (?, ?, 'ETF', ?, ?, 'CN', 'CNY', TRUE)",
        [fund_code, fund_code, tracking, sub],
    )
    conn.execute(
        "INSERT INTO index_fund_snapshots (snapshot_id, snapshot_date, fund_code, shares, cost_amount) "
        "VALUES (?, DATE '2026-05-29', ?, ?, 10000)",
        [f"S-{fund_code}", fund_code, shares],
    )
    conn.execute(
        "INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?, DATE '2026-05-29', ?)",
        [fund_code, nav],
    )
    if total_score is not None:
        conn.execute(
            "INSERT INTO fund_screening_results (eval_date, fund_code, total_score, return_3m) "
            "VALUES (DATE '2026-05-29', ?, ?, ?)",
            [fund_code, total_score, return_3m],
        )


def _seed_macro_account(conn, *, exposure=0.87, account_total=1_000_000):
    conn.execute("INSERT INTO market_exposure (trade_date, benchmark, target_exposure) "
                 "VALUES (DATE '2026-05-29', '000300', ?)", [exposure])
    conn.execute(
        "INSERT INTO account_daily (account_id, trade_date, cash, position_value, total_value, "
        "net_contribution, nav, daily_return, drawdown) "
        "VALUES ('default', DATE '2026-05-29', 0, 0, ?, ?, 1.0, 0, 0)",
        [account_total, account_total],
    )


def _seed_m4(conn, fund_code, weight):
    conn.execute("INSERT INTO index_allocation (trade_date, fund_code, weight) "
                 "VALUES (DATE '2026-05-29', ?, ?)", [fund_code, weight])


def test_v2_exited_holding_allows_overlap_tracking(monkeypatch):
    """持仓 exited → 同 tracking 的候选允许推荐,thesis 标"持仓已退出可重入"。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch, exited_codes=["GONE"])
    _seed_active_holding(conn, "GONE", tracking="000905", sub="broad")
    _seed_screening(conn, [
        ("NEW905", "新中证500", "broad", "000905", "in_window", 90),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert "NEW905" in codes
    rec = next(r for r in snap.in_window if r.fund_code == "NEW905")
    assert "持仓已退出" in rec.thesis or "可重新进入" in rec.thesis


def test_v2_active_underweight_allows_overlap_with_补仓_thesis(monkeypatch):
    """active 持仓在该 tracking 欠配 → 候选保留,thesis 含'欠配补仓'。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_macro_account(conn, exposure=0.87, account_total=1_000_000)
    # 持仓 1000 股 × 1.0 nav = ¥1000;account 100 万 × 87% × M4 0.30 = ¥261,000 → 严重欠配
    _seed_active_holding(conn, "HOLD905", tracking="000905", sub="broad",
                         shares=1000, nav=1.0)
    _seed_m4(conn, "CAND905", 0.30)  # 候选的 M4 权重
    _seed_screening(conn, [
        ("CAND905", "候选中证500", "broad", "000905", "in_window", 80),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert "CAND905" in codes
    rec = next(r for r in snap.in_window if r.fund_code == "CAND905")
    assert "欠配" in rec.thesis or "补仓" in rec.thesis


def test_v2_candidate_beats_holding_score_allows_overlap(monkeypatch):
    """候选 total_score 比持仓 + 5 分以上 → 允许,thesis 含'超额表现'。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    # 持仓有 scanner 分 60;候选 80;差 20 > 5
    _seed_active_holding(conn, "OLD300", tracking="000300", sub="broad",
                         shares=10, nav=1.0, total_score=60.0)
    _seed_screening(conn, [
        ("NEW300", "更强 300", "broad", "000300", "in_window", 80),
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert "NEW300" in codes
    rec = next(r for r in snap.in_window if r.fund_code == "NEW300")
    assert "超额" in rec.thesis or "综合分" in rec.thesis


def test_v2_active_well_supplied_and_no_score_advantage_still_excludes(monkeypatch):
    """active 持仓充足(无欠配)+ 候选分不显著高 → 仍排除。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _patch_watchlist(monkeypatch)
    _seed_macro_account(conn, exposure=0.87, account_total=10_000)
    # 持 5000 股 × nav 1.0 = ¥5000;account 1万 × 87% × M4 0.30 = ¥2610 → 已超配,不欠
    _seed_active_holding(conn, "HOLD300", tracking="000300", sub="broad",
                         shares=5000, nav=1.0, total_score=78.0)
    _seed_m4(conn, "CAND300", 0.30)
    _seed_screening(conn, [
        ("CAND300", "候选 300", "broad", "000300", "in_window", 80),  # 仅高 2,< DELTA 5
    ])
    snap = build_recommendations(conn, top_in_window=10)
    codes = [r.fund_code for r in snap.in_window]
    assert "CAND300" not in codes


def test_v2_watching_marks_is_user_watching_and_thesis(monkeypatch):
    """intent=watching 的候选应被标 is_user_watching + thesis 末尾加"已在你的观察名单"。"""
    from src.index_funds.config import FundWatchItem
    items = [FundWatchItem(
        fund_code="WATCHED", name="观察", fund_type="ETF",
        tracking_index="000300", tracking_index_name="x",
        market="CN", currency="CNY", target_weight=0.0,
        category="equity_index", intent="watching",
    )]
    monkeypatch.setattr("src.funds.recommendations.get_watchlist", lambda: items)
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_screening(conn, [
        ("WATCHED", "观察 ETF", "broad", "ix1", "in_window", 90),
        ("OTHER", "其它", "qdii", "ix2", "in_window", 80),
    ])
    snap = build_recommendations(conn, top_in_window=10, max_per_category=10)
    watched = next(r for r in snap.in_window if r.fund_code == "WATCHED")
    other = next(r for r in snap.in_window if r.fund_code == "OTHER")
    assert watched.is_user_watching is True
    assert "已在你的观察名单" in watched.thesis
    assert other.is_user_watching is False
