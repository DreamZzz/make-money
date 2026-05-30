"""F4: 基金推荐引擎。

把 scanner 结果(fund_screening_results)按用户需求过滤排序成两类推荐:
- in_window:  今日可加仓窗口(Top N) - 趋势 + 中位估值 + 宏观契合
- watch_high_value: 高价值关注名单(Top M) - 综合分高但等回调

过滤规则:
- 排除当前持仓(避免推荐你已经持有的)
- 排除已退出 (intent=exited)
- 类别多样性: 每个 etf_subcategory 最多 N 支
- 相关性近似过滤: 同一 tracking_index 算重叠
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import duckdb

from src.index_funds.config import get_watchlist

DEFAULT_TOP_IN_WINDOW = 5
DEFAULT_TOP_WATCH = 10
DEFAULT_MAX_PER_CATEGORY = 2


@dataclass
class FundRecommendation:
    fund_code: str
    fund_name: str | None
    etf_subcategory: str | None
    tracking_index: str | None
    scale_yi: float | None
    total_score: float
    signal_tag: str
    price_pct: float | None
    trend_score: float | None
    macro_score: float | None
    return_6m: float | None
    thesis: str
    rank: int = 0
    excluded_reasons: list[str] = field(default_factory=list)


@dataclass
class RecommendationsSnapshot:
    eval_date: str | None
    in_window: list[FundRecommendation]
    watch_high_value: list[FundRecommendation]
    excluded_holdings: list[str]
    overlap_tracking: list[str]    # 持仓的 tracking_index 列表(用于过滤同标的)
    holding_categories: list[str]  # 持仓 etf_subcategory(用于多样性提示)
    total_candidates: int
    overall_advice: str


def _load_holdings(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    """读出当前持仓基金及其 tracking_index/category(用于过滤推荐)。"""
    rows = conn.execute(
        """
        SELECT s.fund_code, fi.tracking_index, fi.etf_subcategory
        FROM index_fund_snapshots s
        LEFT JOIN fund_info fi ON fi.fund_code = s.fund_code
        WHERE s.shares > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.fund_code
            ORDER BY s.snapshot_date DESC, s.created_at DESC
        ) = 1
        """
    ).fetchall()
    return {fc: {"tracking_index": ti, "category": cat} for fc, ti, cat in rows}


def _load_candidates(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_tags: list[str],
) -> list[dict[str, Any]]:
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='fund_screening_results'"
    ).fetchone():
        return []
    placeholders = ", ".join("?" for _ in signal_tags)
    df = conn.execute(
        f"""
        SELECT * FROM fund_screening_results
        WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results)
          AND signal_tag IN ({placeholders})
        ORDER BY total_score DESC NULLS LAST
        """,
        signal_tags,
    ).fetchdf()
    return df.to_dict(orient="records") if not df.empty else []


def _filter_and_rank(
    candidates: list[dict[str, Any]],
    *,
    excluded_codes: set[str],
    excluded_tracking: set[str],
    max_per_category: int,
    limit: int,
    exclude_intent_exited: set[str],
) -> list[FundRecommendation]:
    out: list[FundRecommendation] = []
    cat_count: Counter[str] = Counter()
    for row in candidates:
        code = row.get("fund_code")
        if not code:
            continue
        excluded: list[str] = []
        if code in excluded_codes:
            excluded.append("already_held")
        if code in exclude_intent_exited:
            excluded.append("intent_exited")
        tracking = row.get("tracking_index") or ""
        if tracking and tracking in excluded_tracking:
            excluded.append(f"overlaps_tracking={tracking}")
        category = row.get("etf_subcategory") or "other"
        if cat_count[category] >= max_per_category:
            excluded.append(f"category_{category}_full")
        if excluded:
            continue
        out.append(FundRecommendation(
            fund_code=code,
            fund_name=row.get("fund_name"),
            etf_subcategory=category,
            tracking_index=tracking or None,
            scale_yi=row.get("scale_yi"),
            total_score=float(row.get("total_score") or 0),
            signal_tag=str(row.get("signal_tag") or ""),
            price_pct=row.get("price_pct"),
            trend_score=row.get("trend_score"),
            macro_score=row.get("macro_score"),
            return_6m=row.get("return_6m"),
            thesis=str(row.get("thesis") or ""),
            rank=len(out) + 1,
        ))
        cat_count[category] += 1
        if len(out) >= limit:
            break
    return out


def _exited_codes(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {item.fund_code for item in get_watchlist() if item.intent == "exited"}


def build_recommendations(
    conn: duckdb.DuckDBPyConnection,
    *,
    top_in_window: int = DEFAULT_TOP_IN_WINDOW,
    top_watch: int = DEFAULT_TOP_WATCH,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    exclude_held: bool = True,
    exclude_same_tracking: bool = True,
) -> RecommendationsSnapshot:
    """主入口:产出推荐快照。"""
    holdings = _load_holdings(conn) if exclude_held else {}
    held_codes = set(holdings.keys())
    held_tracking = {h["tracking_index"] for h in holdings.values()
                     if h.get("tracking_index") and exclude_same_tracking}
    held_categories = [h.get("category") for h in holdings.values() if h.get("category")]
    exited = _exited_codes(conn)

    in_window_raw = _load_candidates(conn, signal_tags=["in_window"])
    watch_raw = _load_candidates(conn, signal_tags=["watch_high_value"])

    in_window = _filter_and_rank(
        in_window_raw,
        excluded_codes=held_codes, excluded_tracking=held_tracking,
        max_per_category=max_per_category, limit=top_in_window,
        exclude_intent_exited=exited,
    )
    watch = _filter_and_rank(
        watch_raw,
        excluded_codes=held_codes | {r.fund_code for r in in_window},
        excluded_tracking=held_tracking,
        max_per_category=max_per_category, limit=top_watch,
        exclude_intent_exited=exited,
    )

    total_candidates = len(in_window_raw) + len(watch_raw)
    eval_date = None
    if in_window_raw or watch_raw:
        rec = (in_window_raw + watch_raw)[0]
        ed = rec.get("eval_date")
        eval_date = str(ed) if ed else None

    if in_window:
        advice = f"今日 {len(in_window)} 支基金进入加仓窗口期"
    elif watch:
        advice = f"无窗口期候选,{len(watch)} 支高价值关注名单,等回调"
    elif total_candidates == 0:
        advice = "扫描器无候选数据 (可能 nav 候选池未填充)"
    else:
        advice = "今日无 in_window/watch 候选,可关注 scanner 全表"

    return RecommendationsSnapshot(
        eval_date=eval_date,
        in_window=in_window,
        watch_high_value=watch,
        excluded_holdings=sorted(held_codes),
        overlap_tracking=sorted(t for t in held_tracking if t),
        holding_categories=sorted(set(held_categories)),
        total_candidates=total_candidates,
        overall_advice=advice,
    )
