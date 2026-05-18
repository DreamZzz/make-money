"""Probe free market/research sources without changing production data paths."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

import pandas as pd

from src.data_pipeline.fetchers import free_sources


@dataclass(frozen=True)
class ProbeTarget:
    source: str
    market: str
    operation: str
    symbol: str


Fetcher = Callable[[ProbeTarget], pd.DataFrame]


def probe_free_sources(
    targets: list[ProbeTarget],
    fetchers: dict[str, Fetcher] | None = None,
    run_id: str | None = None,
) -> dict:
    """Run probe targets and return a serializable result object."""
    run_id = run_id or f"FREE-SOURCE-PROBE-{uuid4().hex[:12]}"
    fetchers = fetchers or default_fetchers()
    results = []
    for target in targets:
        fetcher = fetchers.get(target.source)
        if fetcher is None:
            results.append(_result(target, "source_error", 0, f"no fetcher for {target.source}"))
            continue
        try:
            df = fetcher(target)
            status = free_sources.source_status(df)
            results.append(_result(target, status, len(df), free_sources.source_error(df)))
        except Exception as exc:
            results.append(_result(target, "source_error", 0, str(exc)))

    summary = {
        "sources": len({item["source"] for item in results}),
        "attempted": len(results),
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "empty": sum(1 for item in results if item["status"] == "empty"),
        "source_error": sum(1 for item in results if item["status"] == "source_error"),
    }
    return {"run_id": run_id, "summary": summary, "results": results}


def _result(target: ProbeTarget, status: str, rows: int, error: str) -> dict:
    return {
        "source": target.source,
        "market": target.market,
        "operation": target.operation,
        "symbol": target.symbol,
        "status": status,
        "rows": int(rows),
        "error": error or "",
    }


def build_probe_health_rows(probe: dict) -> list[dict]:
    """Convert probe results into rows accepted by record_data_source_health."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for result in probe.get("results", []):
        key = (result["source"], result["market"], result["operation"])
        grouped[key].append(result)

    rows = []
    for (source, market, operation), items in sorted(grouped.items()):
        attempted = len(items)
        updated = sum(1 for item in items if item["status"] == "ok")
        no_data = sum(1 for item in items if item["status"] == "empty")
        source_error = sum(1 for item in items if item["status"] == "source_error")
        rows.append({
            "run_id": probe["run_id"],
            "source": source,
            "market": market,
            "operation": f"{operation}_probe",
            "status": _health_status(attempted, updated, no_data, source_error),
            "attempted": attempted,
            "updated": updated,
            "no_data": no_data,
            "source_error": source_error,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": source_error,
            "message": f"free-source probe: {updated}/{attempted} ok, {source_error} source errors",
            "stats_json": {"results": items},
        })
    return rows


def _health_status(attempted: int, updated: int, no_data: int, source_error: int) -> str:
    if attempted <= 0:
        return "SKIPPED"
    if source_error and updated == 0:
        return "FAILED"
    if source_error or no_data or updated < attempted:
        return "DEGRADED"
    return "OK"


def default_fetchers(
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict[str, Fetcher]:
    start = pd.to_datetime(start_date or pd.Timestamp.today().date()).strftime("%Y%m%d")
    end = pd.to_datetime(end_date or pd.Timestamp.today().date()).strftime("%Y%m%d")
    return {
        "tencent": lambda target: free_sources.fetch_tencent_cn_daily(target.symbol, start, end),
        "mootdx": lambda target: free_sources.fetch_mootdx_cn_daily(target.symbol, start, end),
        "eastmoney_report": lambda target: free_sources.fetch_eastmoney_research_reports(target.symbol),
        "ths_concept": lambda _target: free_sources.fetch_ths_concept_summary(),
    }


def make_default_targets(symbols: list[str], sources: list[str] | None = None) -> list[ProbeTarget]:
    sources = sources or ["tencent", "mootdx", "eastmoney_report", "ths_concept"]
    targets: list[ProbeTarget] = []
    for source in sources:
        if source == "ths_concept":
            targets.append(ProbeTarget(source=source, market="CN", operation="theme", symbol="__market__"))
            continue
        operation = "research_report" if source == "eastmoney_report" else "daily"
        for symbol in symbols:
            targets.append(ProbeTarget(source=source, market="CN", operation=operation, symbol=str(symbol).zfill(6)))
    return targets
