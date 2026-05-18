import duckdb
import pandas as pd

from src.data_pipeline.free_source_probe import (
    ProbeTarget,
    build_probe_health_rows,
    probe_free_sources,
)
from src.data_pipeline.loader import init_db, record_data_source_health


def _frame(status: str, rows: int = 1) -> pd.DataFrame:
    df = pd.DataFrame({"value": list(range(rows))})
    df.attrs["source_status"] = status
    df.attrs["source_error"] = "boom" if status == "source_error" else ""
    return df


def test_probe_free_sources_summarizes_injected_fetchers():
    result = probe_free_sources(
        targets=[ProbeTarget(source="tencent", market="CN", operation="daily", symbol="000001")],
        fetchers={"tencent": lambda target: _frame("ok", rows=2)},
    )

    assert result["summary"] == {
        "sources": 1,
        "attempted": 1,
        "ok": 1,
        "empty": 0,
        "source_error": 0,
    }
    assert result["results"][0]["rows"] == 2
    assert result["results"][0]["status"] == "ok"


def test_build_probe_health_rows_groups_by_source_market_operation():
    probe = probe_free_sources(
        targets=[
            ProbeTarget(source="tencent", market="CN", operation="daily", symbol="000001"),
            ProbeTarget(source="tencent", market="CN", operation="daily", symbol="600519"),
            ProbeTarget(source="eastmoney_report", market="CN", operation="research_report", symbol="000001"),
        ],
        fetchers={
            "tencent": lambda target: _frame("ok" if target.symbol == "000001" else "empty"),
            "eastmoney_report": lambda target: _frame("source_error", rows=0),
        },
        run_id="PROBE-1",
    )

    rows = build_probe_health_rows(probe)

    assert rows == [
        {
            "run_id": "PROBE-1",
            "source": "eastmoney_report",
            "market": "CN",
            "operation": "research_report_probe",
            "status": "FAILED",
            "attempted": 1,
            "updated": 0,
            "no_data": 0,
            "source_error": 1,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": 1,
            "message": "free-source probe: 0/1 ok, 1 source errors",
            "stats_json": {
                "results": [{
                    "source": "eastmoney_report",
                    "market": "CN",
                    "operation": "research_report",
                    "symbol": "000001",
                    "status": "source_error",
                    "rows": 0,
                    "error": "boom",
                }],
            },
        },
        {
            "run_id": "PROBE-1",
            "source": "tencent",
            "market": "CN",
            "operation": "daily_probe",
            "status": "DEGRADED",
            "attempted": 2,
            "updated": 1,
            "no_data": 1,
            "source_error": 0,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": 0,
            "message": "free-source probe: 1/2 ok, 0 source errors",
            "stats_json": {
                "results": [
                    {
                        "source": "tencent",
                        "market": "CN",
                        "operation": "daily",
                        "symbol": "000001",
                        "status": "ok",
                        "rows": 1,
                        "error": "",
                    },
                    {
                        "source": "tencent",
                        "market": "CN",
                        "operation": "daily",
                        "symbol": "600519",
                        "status": "empty",
                        "rows": 1,
                        "error": "",
                    },
                ],
            },
        },
    ]


def test_probe_health_rows_can_be_recorded_to_duckdb():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    rows = build_probe_health_rows({
        "run_id": "PROBE-2",
        "results": [{
            "source": "tencent",
            "market": "CN",
            "operation": "daily",
            "symbol": "000001",
            "status": "ok",
            "rows": 2,
            "error": "",
        }],
    })

    inserted = record_data_source_health(conn, rows)

    saved = conn.execute("""
        SELECT run_id, source, market, operation, status, attempted, updated
        FROM data_source_health
    """).fetchall()
    assert inserted == 1
    assert saved == [("PROBE-2", "tencent", "CN", "daily_probe", "OK", 1, 1)]
    conn.close()
