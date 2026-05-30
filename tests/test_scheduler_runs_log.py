"""C2: scheduler_runs spool + DB writer + reader 测试。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from src.data_pipeline.loader import init_db
from src.scheduler.runs_log import (
    append_run,
    build_run_id,
    latest_per_job,
    load_runs,
    sync_spool_to_db,
    upsert_row,
)


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def test_build_run_id_is_stable_per_scheduled_for():
    s = datetime(2026, 5, 29, 20, 0, 0)
    assert build_run_id("daily_close", s) == "daily_close-2026-05-29-200000"


def test_upsert_row_inserts_and_replaces(conn):
    base = {
        "run_id": "daily_close-2026-05-29-200000",
        "job_key": "daily_close",
        "job_label": "收盘闭环",
        "scheduled_for": "2026-05-29 20:00:00",
        "started_at": "2026-05-29 20:00:30",
        "ended_at": "2026-05-29 20:08:12",
        "duration_seconds": 462.0,
        "status": "SUCCEEDED",
        "exit_code": 0,
        "result": "执行完成",
        "log_path": "output/cron.log",
        "source": "watchdog",
        "schedule_alignment": "准点",
        "schedule_note": None,
    }
    upsert_row(conn, base)
    assert conn.execute("SELECT COUNT(*) FROM scheduler_runs").fetchone()[0] == 1

    base["status"] = "FAILED"
    base["exit_code"] = 1
    upsert_row(conn, base)
    rows = conn.execute("SELECT status, exit_code FROM scheduler_runs").fetchall()
    assert rows == [("FAILED", 1)]


def test_append_run_writes_spool_only_when_db_missing(tmp_path: Path):
    """db_path 不存在时, append_run 不应抛 — spool 仍 append。"""
    spool = tmp_path / "scheduler_runs.jsonl"
    row = append_run(
        run_id="daily_close-2026-05-29-200000",
        job_key="daily_close",
        job_label="收盘闭环",
        scheduled_for=datetime(2026, 5, 29, 20, 0),
        started_at=datetime(2026, 5, 29, 20, 0, 30),
        ended_at=datetime(2026, 5, 29, 20, 5, 0),
        status="SUCCEEDED",
        exit_code=0,
        result="OK",
        spool_path=spool,
        db_path=tmp_path / "missing.db",
    )
    assert row["duration_seconds"] == pytest.approx(270.0, abs=1)
    assert spool.exists()
    line = spool.read_text().strip()
    parsed = json.loads(line)
    assert parsed["run_id"] == "daily_close-2026-05-29-200000"
    assert parsed["status"] == "SUCCEEDED"


def test_sync_spool_to_db_upserts_all_lines(tmp_path: Path, conn):
    spool = tmp_path / "scheduler_runs.jsonl"
    rows = [
        {"run_id": "a", "job_key": "daily_close", "job_label": "收盘", "status": "SUCCEEDED",
         "scheduled_for": "2026-05-28 20:00:00", "started_at": "2026-05-28 20:01:00",
         "ended_at": "2026-05-28 20:05:00", "duration_seconds": 240.0,
         "exit_code": 0, "result": "ok", "log_path": None, "source": "watchdog",
         "schedule_alignment": None, "schedule_note": None},
        {"run_id": "b", "job_key": "open_paper_trade", "job_label": "开盘", "status": "FAILED",
         "scheduled_for": "2026-05-29 09:40:00", "started_at": "2026-05-29 09:41:00",
         "ended_at": "2026-05-29 09:42:00", "duration_seconds": 60.0,
         "exit_code": 2, "result": "bad", "log_path": None, "source": "watchdog",
         "schedule_alignment": None, "schedule_note": None},
        "not json — should be skipped",
    ]
    spool.write_text(
        "\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n",
        encoding="utf-8",
    )
    n = sync_spool_to_db(conn, spool_path=spool)
    assert n == 2
    statuses = {r[0]: r[1] for r in conn.execute(
        "SELECT run_id, status FROM scheduler_runs ORDER BY run_id"
    ).fetchall()}
    assert statuses == {"a": "SUCCEEDED", "b": "FAILED"}


def test_load_runs_and_latest_per_job(conn):
    for rid, jk, ts, status in [
        ("dc-1", "daily_close", "2026-05-28 20:00:00", "SUCCEEDED"),
        ("dc-2", "daily_close", "2026-05-29 20:00:00", "FAILED"),
        ("op-1", "open_paper_trade", "2026-05-29 09:40:00", "SUCCEEDED"),
    ]:
        upsert_row(conn, {
            "run_id": rid, "job_key": jk, "job_label": jk,
            "scheduled_for": ts, "started_at": ts, "ended_at": ts,
            "duration_seconds": 1.0, "status": status, "exit_code": 0,
            "result": "x", "log_path": None, "source": "watchdog",
            "schedule_alignment": None, "schedule_note": None,
        })
    rows = load_runs(conn, limit=10)
    # 倒序: dc-2 是最新
    assert rows[0]["run_id"] == "dc-2"
    latest = latest_per_job(conn)
    assert latest["daily_close"]["run_id"] == "dc-2"
    assert latest["open_paper_trade"]["run_id"] == "op-1"


def test_service_history_prefers_scheduler_runs_table(conn):
    """service.py 的 _load_scheduled_job_history 应优先读表。"""
    from src.dashboard_v2.service import _load_scheduled_job_history

    upsert_row(conn, {
        "run_id": "dc-1", "job_key": "daily_close", "job_label": "收盘闭环",
        "scheduled_for": "2026-05-29 20:00:00", "started_at": "2026-05-29 20:00:30",
        "ended_at": "2026-05-29 20:08:00", "duration_seconds": 450.0,
        "status": "SUCCEEDED", "exit_code": 0, "result": "OK",
        "log_path": "output/cron.log", "source": "watchdog",
        "schedule_alignment": "准点", "schedule_note": None,
    })
    history = _load_scheduled_job_history(conn)
    assert history
    assert history[0]["job_key"] == "daily_close"
    assert history[0]["job_name"] == "收盘闭环"
    assert history[0]["scheduled_time"] == "20:00"
    assert history[0]["status"] == "SUCCEEDED"
    assert history[0]["status_label"]  # 非空
