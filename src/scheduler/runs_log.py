"""C2: scheduler_runs 结构化历史 — 持久化 + 同步 + 读取。

watchdog 每次进入终态(SUCCEEDED/FAILED/MISSED)时调用 `append_run()`,
- 优先写 DuckDB (短超时 try/except);
- 同步 append 到 `output/scheduler_runs.jsonl` 作为兜底 (无锁、原子追加)。

`sync_spool_to_db(conn)` 把 JSONL 中尚未落库的 run_id 一次性 upsert 进表,
适合在主流水线收尾时跑一次。

`load_runs(conn, limit=12)` 给 Dashboard 用。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from src.config import PROJECT_ROOT

SPOOL_PATH = Path(PROJECT_ROOT) / "output" / "scheduler_runs.jsonl"
DB_PATH = Path(PROJECT_ROOT) / "data" / "duckdb" / "market.db"

# 终态枚举
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_MISSED = "MISSED"
STATUS_DEGRADED = "DEGRADED"


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(sep=" ", timespec="seconds")


def build_run_id(job_key: str, scheduled_for: datetime | str) -> str:
    """形如 daily_close-2026-05-29-200000,同一日同 job 的同一计划时刻幂等。"""
    if isinstance(scheduled_for, str):
        try:
            scheduled_for = datetime.fromisoformat(scheduled_for.replace(" ", "T"))
        except ValueError:
            scheduled_for = datetime.now()
    return f"{job_key}-{scheduled_for.strftime('%Y-%m-%d-%H%M%S')}"


def append_run(
    *,
    run_id: str,
    job_key: str,
    job_label: str | None,
    scheduled_for: datetime | str | None,
    started_at: datetime | str | None,
    ended_at: datetime | str | None,
    status: str,
    exit_code: int | None,
    result: str | None,
    log_path: str | None = None,
    schedule_alignment: str | None = None,
    schedule_note: str | None = None,
    spool_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """落 spool + 尽力落 DB。返回写入的 row dict。"""
    duration: float | None = None
    s_at = _to_dt(started_at)
    e_at = _to_dt(ended_at)
    if s_at and e_at:
        duration = max((e_at - s_at).total_seconds(), 0.0)

    row = {
        "run_id": run_id,
        "job_key": job_key,
        "job_label": job_label,
        "scheduled_for": _iso(scheduled_for),
        "started_at": _iso(started_at),
        "ended_at": _iso(ended_at),
        "duration_seconds": duration,
        "status": status,
        "exit_code": exit_code,
        "result": result,
        "log_path": log_path,
        "source": "watchdog",
        "schedule_alignment": schedule_alignment,
        "schedule_note": schedule_note,
        "logged_at": _iso(datetime.now()),
    }
    _spool_append(row, spool_path or SPOOL_PATH)
    _try_write_db(row, db_path or DB_PATH)
    return row


def _to_dt(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None


def _spool_append(row: dict[str, Any], spool_path: Path) -> None:
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    with spool_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _try_write_db(row: dict[str, Any], db_path: Path) -> bool:
    """写 DB 失败不抛 — 兜底靠 spool。"""
    if not db_path.exists():
        return False
    try:
        conn = duckdb.connect(str(db_path), config={"access_mode": "READ_WRITE",
                                                    "lock_configuration": "NO_WAIT"})
    except Exception:
        return False
    try:
        upsert_row(conn, row)
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_row(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    """INSERT OR REPLACE 单行 — 测试和 sync 都用同一函数。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO scheduler_runs (
            run_id, job_key, job_label, scheduled_for, started_at, ended_at,
            duration_seconds, status, exit_code, result, log_path, source,
            schedule_alignment, schedule_note
        ) VALUES (?, ?, ?,
                  CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP),
                  ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["run_id"], row["job_key"], row.get("job_label"),
            row.get("scheduled_for"), row.get("started_at"), row.get("ended_at"),
            row.get("duration_seconds"), row["status"], row.get("exit_code"),
            row.get("result"), row.get("log_path"),
            row.get("source") or "watchdog",
            row.get("schedule_alignment"), row.get("schedule_note"),
        ],
    )


def sync_spool_to_db(
    conn: duckdb.DuckDBPyConnection,
    *,
    spool_path: Path | None = None,
) -> int:
    """把 spool 里所有行 upsert 进表。返回成功 upsert 的行数。"""
    spool_path = spool_path or SPOOL_PATH
    if not spool_path.exists():
        return 0
    n = 0
    with spool_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("run_id") or not row.get("job_key") or not row.get("status"):
                continue
            try:
                upsert_row(conn, row)
                n += 1
            except Exception:
                continue
    return n


def load_runs(
    conn: duckdb.DuckDBPyConnection,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """给 Dashboard 用 — 按 scheduled_for/started_at 倒序返回。"""
    rows = conn.execute(
        """
        SELECT run_id, job_key, job_label, scheduled_for, started_at, ended_at,
               duration_seconds, status, exit_code, result, log_path,
               schedule_alignment, schedule_note
        FROM scheduler_runs
        ORDER BY COALESCE(started_at, scheduled_for, created_at) DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    cols = ["run_id", "job_key", "job_label", "scheduled_for", "started_at",
            "ended_at", "duration_seconds", "status", "exit_code", "result",
            "log_path", "schedule_alignment", "schedule_note"]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def latest_per_job(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    """每个 job_key 最新一条 run,用于驱动 scheduler_jobs 的 last_* 字段。"""
    rows = conn.execute(
        """
        SELECT run_id, job_key, job_label, scheduled_for, started_at, ended_at,
               duration_seconds, status, exit_code, result, log_path,
               schedule_alignment, schedule_note
        FROM scheduler_runs
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY job_key
            ORDER BY COALESCE(started_at, scheduled_for, created_at) DESC
        ) = 1
        """
    ).fetchall()
    cols = ["run_id", "job_key", "job_label", "scheduled_for", "started_at",
            "ended_at", "duration_seconds", "status", "exit_code", "result",
            "log_path", "schedule_alignment", "schedule_note"]
    return {r[1]: dict(zip(cols, r, strict=False)) for r in rows}


def truncate_spool(spool_path: Path | None = None) -> None:
    """清空 spool — sync 成功后调用。"""
    p = spool_path or SPOOL_PATH
    if p.exists():
        os.replace(p, p.with_suffix(p.suffix + ".rotated"))
