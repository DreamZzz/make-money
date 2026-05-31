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
DEFAULT_TOP_OVERSOLD = 10
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
    oversold_candidates: list[FundRecommendation]   # F2 增强:估值低 + 深度回撤,等趋势确立
    excluded_holdings: list[str]
    overlap_tracking: list[str]    # 持仓的 tracking_index 列表(用于过滤同标的)
    holding_categories: list[str]  # 持仓 etf_subcategory(用于多样性提示)
    total_candidates: int
    overall_advice: str


def _load_holdings(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    """读出当前持仓基金及 tracking_index/category/intent/current_value/scanner 评分。

    用于 overlap_tracking 智能比较(F4-v2):欠配或落后于候选时允许推荐。
    """
    intents = {item.fund_code: item.intent for item in get_watchlist()}
    rows = conn.execute(
        """
        SELECT s.fund_code, fi.tracking_index, fi.etf_subcategory,
               s.shares, n.nav,
               r.total_score, r.return_3m, r.return_6m, r.trend_score
        FROM index_fund_snapshots s
        LEFT JOIN fund_info fi ON fi.fund_code = s.fund_code
        LEFT JOIN (SELECT fund_code, nav FROM fund_nav QUALIFY ROW_NUMBER() OVER
                   (PARTITION BY fund_code ORDER BY trade_date DESC) = 1) n
            ON n.fund_code = s.fund_code
        LEFT JOIN (SELECT * FROM fund_screening_results
                   WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results)) r
            ON r.fund_code = s.fund_code
        WHERE s.shares > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.fund_code
            ORDER BY s.snapshot_date DESC, s.created_at DESC
        ) = 1
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for fc, ti, cat, shares, nav, total_score, r3m, r6m, trend in rows:
        out[fc] = {
            "tracking_index": ti, "category": cat,
            "intent": intents.get(fc, "active"),
            "current_value": float(shares * nav) if (shares and nav) else None,
            "scanner_total_score": float(total_score) if total_score is not None else None,
            "return_3m": float(r3m) if r3m is not None else None,
            "return_6m": float(r6m) if r6m is not None else None,
            "trend_score": float(trend) if trend is not None else None,
        }
    return out


# F4-v2 阈值
OVERLAP_UNDERWEIGHT_RATIO = 0.80    # 持仓 < target * 0.80 → 视为欠配
OVERLAP_SCORE_BEAT_DELTA = 5.0      # 候选 total_score 超过持仓 + N → 视为更强


def decide_overlap(
    *,
    candidate: dict[str, Any],
    holdings_same_tracking: list[dict[str, Any]],
    equity_exposure: float | None,
    account_total: float | None,
    m4_weights: dict[str, float] | None,
) -> tuple[bool, str | None]:
    """同 tracking_index 重叠时是否仍推荐。

    规则(任一满足):
    1. 所有持仓在该 tracking 都是 exited → 允许重入
    2. active 持仓在该 tracking 欠配(current_value < target_value × 0.80)→ 允许补仓
    3. 候选 total_score > 持仓中同 tracking 最高分 + DELTA → 候选明显更强,允许替代
    否则 → 拒绝。
    """
    if not holdings_same_tracking:
        return True, None

    # (1) 全 exited
    if all(h.get("intent") == "exited" for h in holdings_same_tracking):
        return True, "持仓已退出 (intent=exited),可重新进入"

    # (2) active 欠配
    active = [h for h in holdings_same_tracking if h.get("intent") == "active"]
    m4w = (m4_weights or {})
    underweighting: list[str] = []
    for h in active:
        cur = h.get("current_value")
        # target = account_total * equity_exposure * M4_weight (按 candidate.fund_code 取,因 M4 是 fund 维度)
        # 这里没有候选 fund_code 的 m4 权重一一映射,用 holding 自己的:若同 tracking,M4 同一指数
        w = None
        for code, weight in m4w.items():
            if code == candidate.get("fund_code"):
                w = weight
                break
        if w is None:
            # 没 M4 权重,跳过欠配检测
            continue
        if cur is None or equity_exposure is None or account_total is None:
            continue
        target = float(account_total) * float(equity_exposure) * float(w)
        if target <= 0:
            continue
        if cur < target * OVERLAP_UNDERWEIGHT_RATIO:
            gap = target - cur
            underweighting.append(
                f"持仓 {h.get('current_value', 0)/10000:.1f}万 / 目标 {target/10000:.1f}万 (缺 {gap/10000:.1f}万)"
            )
    if underweighting:
        return True, "欠配补仓: " + "; ".join(underweighting)

    # (3) 候选打分明显强过持仓
    cand_score = float(candidate.get("total_score") or 0)
    held_scores = [(h.get("scanner_total_score"), h) for h in holdings_same_tracking
                   if h.get("scanner_total_score") is not None]
    if held_scores:
        top_held = max(held_scores, key=lambda x: x[0] or 0)
        top_held_score, _h = top_held
        if cand_score > top_held_score + OVERLAP_SCORE_BEAT_DELTA:
            return True, (
                f"超额表现: 综合分 {cand_score:.0f} > 持仓最高 {top_held_score:.0f} "
                f"+{cand_score - top_held_score:.0f}"
            )

    return False, None


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
    holdings_by_tracking: dict[str, list[dict[str, Any]]],
    max_per_category: int,
    limit: int,
    exclude_intent_exited: set[str],
    equity_exposure: float | None,
    account_total: float | None,
    m4_weights: dict[str, float],
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
        overlap_thesis: str | None = None
        if tracking and tracking in holdings_by_tracking:
            allow, reason = decide_overlap(
                candidate=row,
                holdings_same_tracking=holdings_by_tracking[tracking],
                equity_exposure=equity_exposure,
                account_total=account_total,
                m4_weights=m4_weights,
            )
            if not allow:
                excluded.append(f"overlaps_tracking={tracking}")
            else:
                overlap_thesis = reason

        category = row.get("etf_subcategory") or "other"
        if cat_count[category] >= max_per_category:
            excluded.append(f"category_{category}_full")
        if excluded:
            continue
        base_thesis = str(row.get("thesis") or "")
        thesis = (base_thesis + "; " + overlap_thesis) if overlap_thesis else base_thesis
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
            thesis=thesis,
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
    top_oversold: int = DEFAULT_TOP_OVERSOLD,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    exclude_held: bool = True,
    exclude_same_tracking: bool = True,
) -> RecommendationsSnapshot:
    """主入口:产出推荐快照。"""
    holdings = _load_holdings(conn) if exclude_held else {}
    held_codes = set(holdings.keys())
    held_categories = [h.get("category") for h in holdings.values() if h.get("category")]
    exited = _exited_codes(conn)

    holdings_by_tracking: dict[str, list[dict[str, Any]]] = {}
    if exclude_same_tracking:
        for fc, h in holdings.items():
            ti = h.get("tracking_index")
            if not ti:
                continue
            holdings_by_tracking.setdefault(ti, []).append({"fund_code": fc, **h})

    # M4 权重 + 宏观,用于 overlap 欠配判断
    from src.index_funds.signals import load_m4_weights
    m4_weights = load_m4_weights(conn)
    exp = conn.execute(
        "SELECT target_exposure FROM market_exposure ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    equity_exposure = float(exp[0]) if exp and exp[0] is not None else None
    acc = conn.execute(
        "SELECT total_value FROM account_daily WHERE account_id='default' "
        "ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    account_total = float(acc[0]) if acc and acc[0] is not None else None

    in_window_raw = _load_candidates(conn, signal_tags=["in_window"])
    watch_raw = _load_candidates(conn, signal_tags=["watch_high_value"])
    oversold_raw = _load_candidates(conn, signal_tags=["oversold_candidate"])

    in_window = _filter_and_rank(
        in_window_raw,
        excluded_codes=held_codes, holdings_by_tracking=holdings_by_tracking,
        max_per_category=max_per_category, limit=top_in_window,
        exclude_intent_exited=exited,
        equity_exposure=equity_exposure, account_total=account_total,
        m4_weights=m4_weights,
    )
    watch = _filter_and_rank(
        watch_raw,
        excluded_codes=held_codes | {r.fund_code for r in in_window},
        holdings_by_tracking=holdings_by_tracking,
        max_per_category=max_per_category, limit=top_watch,
        exclude_intent_exited=exited,
        equity_exposure=equity_exposure, account_total=account_total,
        m4_weights=m4_weights,
    )
    oversold = _filter_and_rank(
        oversold_raw,
        excluded_codes=held_codes | {r.fund_code for r in in_window} | {r.fund_code for r in watch},
        holdings_by_tracking=holdings_by_tracking,
        max_per_category=max_per_category, limit=top_oversold,
        exclude_intent_exited=exited,
        equity_exposure=equity_exposure, account_total=account_total,
        m4_weights=m4_weights,
    )

    total_candidates = len(in_window_raw) + len(watch_raw) + len(oversold_raw)
    eval_date = None
    if in_window_raw or watch_raw or oversold_raw:
        rec = (in_window_raw + watch_raw + oversold_raw)[0]
        ed = rec.get("eval_date")
        eval_date = str(ed) if ed else None

    if in_window:
        advice = f"今日 {len(in_window)} 支基金进入加仓窗口期"
        if oversold:
            advice += f";另有 {len(oversold)} 支超跌候选等趋势确立"
    elif watch:
        advice = f"无窗口期候选,{len(watch)} 支高价值关注名单,等回调"
        if oversold:
            advice += f";{len(oversold)} 支超跌候选等趋势确立"
    elif oversold:
        advice = f"无窗口期/高价值候选,{len(oversold)} 支超跌候选等趋势确立"
    elif total_candidates == 0:
        advice = "扫描器无候选数据 (可能 nav 候选池未填充)"
    else:
        advice = "今日无 in_window/watch/oversold 候选,可关注 scanner 全表"

    return RecommendationsSnapshot(
        eval_date=eval_date,
        in_window=in_window,
        watch_high_value=watch,
        oversold_candidates=oversold,
        excluded_holdings=sorted(held_codes),
        overlap_tracking=sorted(holdings_by_tracking.keys()),
        holding_categories=sorted(set(held_categories)),
        total_candidates=total_candidates,
        overall_advice=advice,
    )
