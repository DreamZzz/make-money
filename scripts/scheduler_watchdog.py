#!/usr/bin/env python3
"""Interval watchdog for make-money scheduled workflows.

This script is meant to be invoked by launchd with StartInterval.  It avoids
StartCalendarInterval timezone/session drift by checking local due windows in
Python, persisting one-run-per-day state, and surfacing missed/failed runs for
Dashboard V2.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_PATH = OUTPUT_DIR / "scheduler_state.json"
PYTHON = os.environ.get("PYTHON") or sys.executable

# C2: 让 watchdog 进入终态时落 scheduler_runs(spool + DB best-effort)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    from src.scheduler.runs_log import append_run as _append_scheduler_run
    from src.scheduler.runs_log import build_run_id as _build_scheduler_run_id
except Exception:  # noqa: BLE001
    _append_scheduler_run = None
    _build_scheduler_run_id = None

STATUS_WAITING = "WAITING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_MISSED = "MISSED"


@dataclass(frozen=True)
class JobSpec:
    job_key: str
    label: str
    scheduled_time: time
    window_minutes: int
    weekdays: tuple[int, ...]
    command: tuple[str, ...]
    log_path: Path
    timeout_seconds: int


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    result: str


Runner = Callable[[JobSpec], RunResult]
PidChecker = Callable[[int], bool]


def default_jobs() -> list[JobSpec]:
    return [
        JobSpec(
            job_key="open_paper_trade",
            label="开盘纸交易",
            scheduled_time=time(9, 40),
            window_minutes=50,
            weekdays=(0, 1, 2, 3, 4),
            command=(PYTHON, str(PROJECT_ROOT / "scripts" / "open_paper_trade.py")),
            log_path=OUTPUT_DIR / "open_trade.log",
            timeout_seconds=1800,
        ),
        JobSpec(
            job_key="daily_close",
            label="收盘闭环",
            scheduled_time=time(20, 0),
            window_minutes=210,
            weekdays=(0, 1, 2, 3, 4),
            command=(PYTHON, str(PROJECT_ROOT / "scripts" / "daily_update.py")),
            log_path=OUTPUT_DIR / "cron.log",
            timeout_seconds=2400,
        ),
        # R5: 财报早盘轻拉(业绩预告/快报 + 日历刷新);非阻塞
        JobSpec(
            job_key="earnings_morning_fetch",
            label="财报早盘轻拉",
            scheduled_time=time(7, 30),
            window_minutes=90,
            weekdays=(0, 1, 2, 3, 4),
            command=(PYTHON, "-m", "src.financials.daily", "morning"),
            log_path=OUTPUT_DIR / "earnings_morning.log",
            timeout_seconds=600,
        ),
    ]


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": None, "jobs": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"version": 1, "updated_at": None, "jobs": {}}
    if not isinstance(data, dict):
        return {"version": 1, "updated_at": None, "jobs": {}}
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        data["jobs"] = {}
    data.setdefault("version", 1)
    data.setdefault("updated_at", None)
    return data


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp_path.replace(path)


def tick(
    *,
    now: datetime | None = None,
    state_path: Path = STATE_PATH,
    jobs: list[JobSpec] | None = None,
    runner: Runner | None = None,
    pid_checker: PidChecker | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    jobs = jobs or default_jobs()
    runner = runner or run_job
    pid_checker = pid_checker or pid_alive

    state = load_state(state_path)
    state["version"] = 1
    state.setdefault("jobs", {})

    for job in jobs:
        job_state = _coerce_job_state(state, job)
        _refresh_next_due(job_state, job, now)
        if _is_running_today(job_state, now) and pid_checker(int(job_state.get("pid") or 0)):
            job_state["result"] = job_state.get("result") or "运行中，跳过重复触发"
            job_state["updated_at"] = _iso(now)
            continue
        if _is_stale_running_today(job_state, now, pid_checker):
            _mark_failed(job_state, finished_at=now, exit_code=-1, result="上次运行状态残留，但进程已不存在")

        if _already_final_today(job_state, now):
            job_state["updated_at"] = _iso(now)
            continue
        if not _is_scheduled_day(job, now):
            _mark_waiting(job_state, job, now, "非交易日窗口，等待下一次检查")
            continue

        scheduled_at = _scheduled_at(job, now)
        window_end = scheduled_at + timedelta(minutes=job.window_minutes)
        if now < scheduled_at:
            _mark_waiting(job_state, job, now, "未到执行窗口")
            continue
        if now > window_end:
            _mark_missed(job_state, job, now, scheduled_at, window_end)
            _record_terminal_run(
                job, scheduled_at, None, now, STATUS_MISSED, None,
                job_state.get("result"),
            )
            continue

        _mark_running(job_state, now)
        save_state(state, state_path)
        result = runner(job)
        finished_at = datetime.now()
        if result.exit_code == 0:
            _mark_succeeded(job_state, run_date=now.date().isoformat(), finished_at=finished_at, result=result)
            _record_terminal_run(job, scheduled_at, now, finished_at, STATUS_SUCCEEDED, 0, result.result)
        else:
            _mark_failed(
                job_state,
                run_date=now.date().isoformat(),
                finished_at=finished_at,
                exit_code=result.exit_code,
                result=result.result,
            )
            _record_terminal_run(job, scheduled_at, now, finished_at, STATUS_FAILED, result.exit_code, result.result)

    state["updated_at"] = _latest_state_updated_at(state, now)
    save_state(state, state_path)
    return state


def run_job(job: JobSpec) -> RunResult:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    env = _prepare_env()
    try:
        with job.log_path.open("a") as log_file:
            completed = subprocess.run(
                list(job.command),
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=job.timeout_seconds,
                check=False,
                text=True,
            )
        return RunResult(exit_code=int(completed.returncode), result=f"退出码 {completed.returncode}")
    except subprocess.TimeoutExpired:
        return RunResult(exit_code=124, result=f"执行超时 {job.timeout_seconds} 秒")
    except Exception as exc:
        return RunResult(exit_code=1, result=f"执行异常: {exc}")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _prepare_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(key, None)
    env["no_proxy"] = "*"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHON"] = PYTHON
    path_parts = [
        str(Path(PYTHON).parent) if "/" in PYTHON else "",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    existing = env.get("PATH") or ""
    env["PATH"] = ":".join(part for part in [*path_parts, existing] if part)
    return env


def _coerce_job_state(state: dict[str, Any], job: JobSpec) -> dict[str, Any]:
    jobs = state.setdefault("jobs", {})
    current = jobs.get(job.job_key)
    if not isinstance(current, dict):
        current = {}
        jobs[job.job_key] = current
    current.setdefault("job_key", job.job_key)
    current.setdefault("label", job.label)
    current.setdefault("status", STATUS_WAITING)
    current["scheduled_time"] = job.scheduled_time.strftime("%H:%M")
    current["window_minutes"] = job.window_minutes
    current["log_path"] = str(job.log_path)
    return current


def _refresh_next_due(job_state: dict[str, Any], job: JobSpec, now: datetime) -> None:
    job_state["next_due_at"] = _iso(_next_scheduled_at(job, now))


def _mark_waiting(job_state: dict[str, Any], job: JobSpec, now: datetime, result: str) -> None:
    job_state.update({
        "status": STATUS_WAITING,
        "result": result,
        "exit_code": None,
        "pid": None,
        "updated_at": _iso(now),
        "next_due_at": _iso(_next_scheduled_at(job, now)),
    })


def _mark_running(job_state: dict[str, Any], now: datetime) -> None:
    job_state.update({
        "status": STATUS_RUNNING,
        "last_run_date": now.date().isoformat(),
        "started_at": _iso(now),
        "ended_at": None,
        "exit_code": None,
        "pid": os.getpid(),
        "result": "运行中",
        "updated_at": _iso(now),
    })


def _mark_succeeded(job_state: dict[str, Any], *, run_date: str, finished_at: datetime, result: RunResult) -> None:
    job_state.update({
        "status": STATUS_SUCCEEDED,
        "last_run_date": run_date,
        "ended_at": _iso(finished_at),
        "exit_code": 0,
        "pid": None,
        "result": result.result or "执行完成",
        "updated_at": _iso(finished_at),
    })


def _mark_failed(
    job_state: dict[str, Any],
    *,
    run_date: str | None = None,
    finished_at: datetime,
    exit_code: int,
    result: str,
) -> None:
    job_state.update({
        "status": STATUS_FAILED,
        "last_run_date": run_date or finished_at.date().isoformat(),
        "ended_at": _iso(finished_at),
        "exit_code": exit_code,
        "pid": None,
        "result": result,
        "updated_at": _iso(finished_at),
    })


def _latest_state_updated_at(state: dict[str, Any], fallback: datetime) -> str:
    latest = _iso(fallback)
    jobs = state.get("jobs")
    if not isinstance(jobs, dict):
        return latest
    for job_state in jobs.values():
        if not isinstance(job_state, dict):
            continue
        updated_at = job_state.get("updated_at")
        if isinstance(updated_at, str) and updated_at > latest:
            latest = updated_at
    return latest


def _mark_missed(
    job_state: dict[str, Any],
    job: JobSpec,
    now: datetime,
    scheduled_at: datetime,
    window_end: datetime,
) -> None:
    job_state.update({
        "status": STATUS_MISSED,
        "last_run_date": now.date().isoformat(),
        "started_at": None,
        "ended_at": None,
        "exit_code": None,
        "pid": None,
        "result": (
            "错过执行窗口："
            f"计划 {scheduled_at.strftime('%H:%M')}，"
            f"窗口至 {window_end.strftime('%H:%M')}，未补跑"
        ),
        "updated_at": _iso(now),
        "next_due_at": _iso(_next_scheduled_after(job, scheduled_at)),
    })


def _already_final_today(job_state: dict[str, Any], now: datetime) -> bool:
    if job_state.get("last_run_date") != now.date().isoformat():
        return False
    return str(job_state.get("status")) in {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_MISSED}


def _is_running_today(job_state: dict[str, Any], now: datetime) -> bool:
    return job_state.get("last_run_date") == now.date().isoformat() and job_state.get("status") == STATUS_RUNNING


def _is_stale_running_today(job_state: dict[str, Any], now: datetime, pid_checker: PidChecker) -> bool:
    if not _is_running_today(job_state, now):
        return False
    return not pid_checker(int(job_state.get("pid") or 0))


def _is_scheduled_day(job: JobSpec, now: datetime) -> bool:
    return now.weekday() in job.weekdays


def _scheduled_at(job: JobSpec, now: datetime) -> datetime:
    return datetime.combine(now.date(), job.scheduled_time)


def _next_scheduled_at(job: JobSpec, now: datetime) -> datetime:
    candidate = _scheduled_at(job, now)
    if now <= candidate and _is_scheduled_day(job, now):
        return candidate
    for offset in range(1, 8):
        day = now + timedelta(days=offset)
        if _is_scheduled_day(job, day):
            return datetime.combine(day.date(), job.scheduled_time)
    return candidate


def _next_scheduled_after(job: JobSpec, scheduled_at: datetime) -> datetime:
    for offset in range(1, 8):
        candidate = scheduled_at + timedelta(days=offset)
        if _is_scheduled_day(job, candidate):
            return datetime.combine(candidate.date(), job.scheduled_time)
    return scheduled_at


def _record_terminal_run(
    job: JobSpec,
    scheduled_at: datetime,
    started_at: datetime | None,
    ended_at: datetime,
    status: str,
    exit_code: int | None,
    result: str | None,
) -> None:
    """C2: watchdog 进入终态时落 scheduler_runs spool + DB(best-effort)。

    任何异常都吞掉 —— 历史归档不应阻塞 watchdog 主循环。
    """
    if _append_scheduler_run is None or _build_scheduler_run_id is None:
        return
    try:
        _append_scheduler_run(
            run_id=_build_scheduler_run_id(job.job_key, scheduled_at),
            job_key=job.job_key,
            job_label=job.label,
            scheduled_for=scheduled_at,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            exit_code=exit_code,
            result=result,
            log_path=str(job.log_path),
            schedule_alignment=_schedule_alignment(scheduled_at, started_at),
        )
    except Exception:  # noqa: BLE001
        pass


def _schedule_alignment(scheduled_at: datetime, started_at: datetime | None) -> str | None:
    if started_at is None:
        return None
    delta = (started_at - scheduled_at).total_seconds()
    if abs(delta) < 60:
        return "准点"
    if delta < 0:
        return f"早 {int(-delta // 60)} 分钟"
    return f"晚 {int(delta // 60)} 分钟"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the make-money interval scheduler watchdog.")
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Evaluate due state without running workflows.")
    args = parser.parse_args(argv)

    if args.dry_run:
        state = tick(
            state_path=args.state_path,
            runner=lambda job: RunResult(exit_code=0, result=f"dry-run: would run {job.job_key}"),
        )
    else:
        state = tick(state_path=args.state_path)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
