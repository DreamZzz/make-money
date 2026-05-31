"""F2: 基金扫描器六维评分测试。"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb

from src.data_pipeline.loader import init_db
from src.funds.scanner import (
    _classify_signal,
    _score_liquidity,
    _score_macro_fit,
    _score_trend,
    _score_valuation,
    evaluate_fund,
    load_latest_screening,
    scan_funds,
)


def _seed_macro(conn, stage="强势上升", trade_date="2026-05-29"):
    conn.execute(
        "INSERT INTO market_state (trade_date, benchmark, stage, stage_score, heat_score, pe_pct_10y) "
        "VALUES (?, '000300', ?, 100, 50.6, 0.83)",
        [trade_date, stage],
    )


def _seed_fund(conn, fund_code, *, sub="broad", tracking="000300", scale_yi=300.0):
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled, scale_yi, etf_subcategory) "
        "VALUES (?, ?, 'ETF', ?, 'CN', 'CNY', TRUE, ?, ?)",
        [fund_code, f"测试 {fund_code}", tracking, scale_yi, sub],
    )


def _seed_nav_series(conn, fund_code, *, days=300, start_nav=1.0, drift=0.0003):
    """简单线性增长的 nav 序列,带噪音。"""
    import random
    random.seed(hash(fund_code) % 1000)
    base = date(2026, 5, 29) - timedelta(days=days)
    nav = start_nav
    rows = []
    for i in range(days):
        d = base + timedelta(days=i)
        nav *= 1 + drift + random.uniform(-0.01, 0.01)
        rows.append((fund_code, d, nav))
    conn.executemany("INSERT INTO fund_nav (fund_code, trade_date, nav) VALUES (?, ?, ?)", rows)


def test_score_valuation():
    assert _score_valuation(0.0) == 100.0    # 最便宜满分
    assert _score_valuation(1.0) == 0.0      # 最贵 0 分
    assert _score_valuation(0.5) == 50.0
    assert _score_valuation(None) is None


def test_score_trend_long_above_short():
    # close 100, ma120 90, ma250 80 → 站稳所有线 + 多头排列
    s = _score_trend(100, 90, 80)
    assert s == 100.0  # 50 + 20 + 20 + 10 = 100, clamp


def test_score_trend_broken():
    # close 80 < ma120 90 < ma250 100 → 全跌穿
    s = _score_trend(80, 90, 100)
    assert s == 10.0  # 50 - 20 - 20 = 10, ma120 < ma250 不加


def test_score_macro_fit_equity_follows_stage():
    assert _score_macro_fit("broad", {"stage": "强势上升"}) == 100.0
    assert _score_macro_fit("qdii", {"stage": "震荡"}) == 60.0
    assert _score_macro_fit("sector", {"stage": "危机"}) == 0.0


def test_score_macro_fit_commodity_inverse():
    # 危机期商品反向加分
    assert _score_macro_fit("commodity", {"stage": "危机"}) == 50.0  # 0 + 50
    assert _score_macro_fit("commodity", {"stage": "强势上升"}) == 80.0  # 100 - 20


def test_score_liquidity_curve():
    assert _score_liquidity(10) == 30.0       # 极小规模差分
    assert _score_liquidity(200) > 90
    assert _score_liquidity(2000) == 70.0      # 超大规模略减
    assert _score_liquidity(None) is None


def test_classify_signal_in_window_when_trend_healthy_and_mid_valuation():
    tag, _ = _classify_signal(trend=75, valuation=60, price_pct=0.35, macro=60, total=70)
    assert tag == "in_window"


def test_classify_signal_avoid_when_trend_broken_or_overvalued():
    tag1, _ = _classify_signal(trend=30, valuation=80, price_pct=0.5, macro=60, total=55)
    assert tag1 == "avoid"
    tag2, _ = _classify_signal(trend=70, valuation=20, price_pct=0.95, macro=60, total=60)
    assert tag2 == "avoid"


def test_classify_signal_watch_when_high_total_but_overvalued():
    """估值偏贵但综合高 → 观察。"""
    tag, headline = _classify_signal(trend=75, valuation=30, price_pct=0.75, macro=60, total=72)
    assert tag == "watch_high_value"
    assert "估值" in headline


def test_classify_signal_insufficient():
    tag, _ = _classify_signal(None, None, None, None, None)
    assert tag == "insufficient_data"


def test_evaluate_fund_end_to_end_with_real_seed_data():
    """端到端:seed 一支正常 nav 增长的基金,看产出分类合理。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_macro(conn)
    _seed_fund(conn, "510300", sub="broad", tracking="000300", scale_yi=1376.0)
    _seed_nav_series(conn, "510300", days=300, start_nav=4.0, drift=0.0002)
    from src.funds.scanner import _load_macro
    macro = _load_macro(conn)
    r = evaluate_fund(conn, "510300", eval_date=date(2026, 5, 29), macro=macro)
    assert r.fund_code == "510300"
    assert r.total_score is not None
    assert r.trend_score is not None
    assert r.valuation_score is not None
    assert r.signal_tag in {"in_window", "watch_high_value", "avoid", "neutral"}
    assert r.nav_history_days == 300
    conn.close()


def test_evaluate_fund_marks_insufficient_when_nav_short():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_macro(conn)
    _seed_fund(conn, "513100", sub="qdii", tracking="HSTECH", scale_yi=200)
    _seed_nav_series(conn, "513100", days=20)  # < MIN_NAV_DAYS
    from src.funds.scanner import _load_macro
    r = evaluate_fund(conn, "513100", eval_date=date(2026, 5, 29), macro=_load_macro(conn))
    assert r.signal_tag == "insufficient_data"
    assert "nav 仅 20 天" in r.thesis
    conn.close()


def test_scan_funds_persists_and_loads_by_signal_tag():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed_macro(conn)
    _seed_fund(conn, "510300", sub="broad", tracking="000300")
    _seed_nav_series(conn, "510300", days=300)
    _seed_fund(conn, "513100", sub="qdii", tracking="HSTECH")
    _seed_nav_series(conn, "513100", days=300, drift=0.001)  # 强势
    scan_funds(conn, eval_date=date(2026, 5, 29), persist=True)
    n_total = conn.execute("SELECT COUNT(*) FROM fund_screening_results").fetchone()[0]
    assert n_total == 2
    rows = load_latest_screening(conn)
    assert len(rows) == 2
    # 按 total_score 倒序
    assert rows[0]["total_score"] >= rows[1]["total_score"]
    conn.close()


def test_classify_signal_oversold_candidate_low_pct_and_deep_dd():
    """估值低位 + 深度回撤 → oversold_candidate(优先于 trend_broken avoid)。"""
    tag, headline = _classify_signal(
        trend=10, valuation=80, price_pct=0.10, macro=60, total=45,
        max_drawdown=-0.40,
    )
    assert tag == "oversold_candidate"
    assert "超跌" in headline
    assert "低位" in headline


def test_classify_signal_oversold_loses_to_too_expensive_first():
    """过贵优先级 > 超跌(不会矛盾,但保护规则顺序)。"""
    tag, _ = _classify_signal(
        trend=10, valuation=20, price_pct=0.95, macro=60, total=40,
        max_drawdown=-0.40,
    )
    assert tag == "avoid"


def test_classify_signal_low_pct_without_deep_dd_falls_through():
    """估值低但没有深度回撤(回撤 -10% 不算深) → 不算 oversold,趋势破回 avoid。"""
    tag, _ = _classify_signal(
        trend=10, valuation=80, price_pct=0.10, macro=60, total=45,
        max_drawdown=-0.10,
    )
    assert tag == "avoid"


def test_classify_signal_deep_dd_without_low_pct_falls_through():
    """回撤深但估值不在低位(35% 分位) → 不算 oversold,趋势破回 avoid。"""
    tag, _ = _classify_signal(
        trend=10, valuation=40, price_pct=0.35, macro=60, total=50,
        max_drawdown=-0.40,
    )
    assert tag == "avoid"
