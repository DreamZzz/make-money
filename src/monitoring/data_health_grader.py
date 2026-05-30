"""C1: data_source_health 分级显示

把 data_source_health 的 OK/DEGRADED/FAILED 三档原始状态,按"今天是否可决策"
转译成业务可消费的三档:

- decidable / 可决策:  本域至少一个源把今日数据拉全
- backup_active / 备源接管: 本域可决策,但主源失败靠备源顶上 —— 应提示运维
- failed / 失败: 本域无任何源把数据拉全 → 阻断今日决策

"域"按 (market, operation) 分组(例如 CN+daily_update, HK+daily_update,
CN+field_coverage_target_universe)。

只看"最近一次 run_id 内每源最新的一条",避免历史 row 干扰。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any

import duckdb

# 每域内某个源被视为"健康"的最低 updated/attempted 比率(允许少量 yfinance 拉空)
HEALTHY_UPDATE_RATIO = 0.90
# 半健康(prerequisite for "partial",可决策但有水分):
PARTIAL_UPDATE_RATIO = 0.50

# 标记某个域为"关键域"(没有它就不能下单),overall 状态由这些域决定
CRITICAL_DOMAINS: set[tuple[str, str]] = {
    ("CN", "daily_update"),
}

# 每域内主源(若主源失败靠备源顶 → backup_active)
PRIMARY_SOURCES: dict[tuple[str, str], str] = {
    ("CN", "daily_update"): "akshare",
    ("HK", "daily_update"): "yfinance",
}


def _classify_source(row: dict) -> str:
    """单个源的 effective 状态: ok / partial / failed / unknown。"""
    attempted = int(row.get("attempted") or 0)
    updated = int(row.get("updated") or 0)
    status = str(row.get("status") or "").upper()
    if status == "FAILED":
        return "failed"
    if attempted == 0:
        # 没尝试 — 可能是 free_sources 这种不抓 daily 的辅助源,标 unknown 不算失败
        return "unknown"
    ratio = updated / attempted
    if ratio >= HEALTHY_UPDATE_RATIO:
        return "ok"
    if ratio >= PARTIAL_UPDATE_RATIO:
        return "partial"
    return "failed"


def _grade_domain(domain_key: tuple[str, str], rows: list[dict]) -> dict[str, Any]:
    """对一个 (market, operation) 域,把所有源汇总成域级状态。"""
    market, operation = domain_key
    classified = [(row, _classify_source(row)) for row in rows]
    ok_sources = [r["source"] for r, c in classified if c == "ok"]
    partial_sources = [r["source"] for r, c in classified if c == "partial"]
    failed_sources = [r["source"] for r, c in classified if c == "failed"]
    primary = PRIMARY_SOURCES.get(domain_key)

    if ok_sources:
        primary_ok = primary is None or primary in ok_sources
        if primary_ok:
            effective = "decidable"
            headline = f"{market}·{operation}: {len(ok_sources)} 个源健康({','.join(ok_sources)})"
        else:
            effective = "backup_active"
            headline = (f"{market}·{operation}: 主源 {primary} 失败,"
                        f"由 {','.join(ok_sources)} 备源接管")
    elif partial_sources:
        effective = "degraded"
        headline = f"{market}·{operation}: 仅 {','.join(partial_sources)} 半量更新,数据有缺口"
    else:
        effective = "failed"
        headline = f"{market}·{operation}: 全部源失败({','.join(failed_sources) or 'no data'})"

    return {
        "market": market,
        "operation": operation,
        "effective_status": effective,
        "headline": headline,
        "primary_source": primary,
        "primary_status": _classify_source(next((r for r in rows if r["source"] == primary), {})) if primary else None,
        "ok_sources": ok_sources,
        "partial_sources": partial_sources,
        "failed_sources": failed_sources,
        "is_critical": domain_key in CRITICAL_DOMAINS,
        "sources": [
            {**r, "effective_source_status": c, "update_ratio": round(
                (int(r.get("updated") or 0) / int(r["attempted"])) if int(r.get("attempted") or 0) > 0 else 0.0, 4)}
            for r, c in classified
        ],
    }


def _aggregate_overall(domains: list[dict]) -> dict[str, Any]:
    critical = [d for d in domains if d["is_critical"]]
    if not critical:
        return {
            "today_decidable": True,
            "status": "decidable",
            "headline": "无关键域注册,默认可决策",
            "blockers": [],
        }
    statuses = {d["effective_status"] for d in critical}
    blockers = [d for d in critical if d["effective_status"] in {"failed", "degraded"}]
    backup = [d for d in critical if d["effective_status"] == "backup_active"]
    if "failed" in statuses or "degraded" in statuses:
        return {
            "today_decidable": False,
            "status": "failed",
            "headline": f"{len(blockers)} 个关键域不可决策,需先恢复数据源",
            "blockers": [d["headline"] for d in blockers],
        }
    if backup:
        return {
            "today_decidable": True,
            "status": "backup_active",
            "headline": f"{len(backup)} 个关键域靠备源决策,需关注主源",
            "blockers": [d["headline"] for d in backup],
        }
    return {
        "today_decidable": True,
        "status": "decidable",
        "headline": "全部关键域主源健康,今日可决策",
        "blockers": [],
    }


def grade_data_source_health(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of: _date | None = None,
) -> dict[str, Any]:
    """对今日(默认最新一天)data_source_health 做分级。

    返回 {"as_of": ..., "overall": {...}, "domains": [...]}
    """
    row = conn.execute("SELECT MAX(DATE(COALESCE(ended_at,started_at))) FROM data_source_health").fetchone()
    latest = row[0] if row else None
    as_of = as_of or latest
    if as_of is None:
        return {
            "as_of": None,
            "overall": {"today_decidable": False, "status": "no_data",
                        "headline": "data_source_health 无任何记录", "blockers": []},
            "domains": [],
        }
    rows = conn.execute(
        """
        SELECT source, market, operation, status, attempted, updated,
               no_data, source_error, rate_limited, circuit_skip, failed,
               started_at, ended_at, message
        FROM data_source_health
        WHERE DATE(COALESCE(ended_at, started_at)) = ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY source, market, operation
            ORDER BY COALESCE(ended_at, started_at) DESC
        ) = 1
        ORDER BY market, operation, source
        """,
        [as_of],
    ).fetchall()
    cols = ["source", "market", "operation", "status", "attempted", "updated",
            "no_data", "source_error", "rate_limited", "circuit_skip", "failed",
            "started_at", "ended_at", "message"]
    by_domain: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        item = dict(zip(cols, r, strict=False))
        key = (str(item["market"]), str(item["operation"]))
        by_domain.setdefault(key, []).append(item)
    domains = [_grade_domain(key, items) for key, items in sorted(by_domain.items())]
    overall = _aggregate_overall(domains)
    return {
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
        "overall": overall,
        "domains": domains,
    }
