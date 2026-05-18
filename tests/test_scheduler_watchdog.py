from __future__ import annotations

from datetime import datetime, time


def _job(tmp_path, job_key: str = "daily_close", hour: int = 20, minute: int = 0):
    from scripts.scheduler_watchdog import JobSpec

    return JobSpec(
        job_key=job_key,
        label="收盘闭环" if job_key == "daily_close" else "开盘纸交易",
        scheduled_time=time(hour, minute),
        window_minutes=90,
        weekdays=(0, 1, 2, 3, 4),
        command=("echo", job_key),
        log_path=tmp_path / f"{job_key}.log",
        timeout_seconds=30,
    )


def test_watchdog_runs_due_job_once_per_trading_day(tmp_path) -> None:
    from scripts.scheduler_watchdog import RunResult, load_state, tick

    state_path = tmp_path / "scheduler_state.json"
    job = _job(tmp_path)
    calls: list[str] = []

    def runner(job_spec):
        calls.append(job_spec.job_key)
        return RunResult(exit_code=0, result="ok")

    first = tick(
        now=datetime(2026, 5, 18, 20, 1),
        state_path=state_path,
        jobs=[job],
        runner=runner,
        pid_checker=lambda _pid: False,
    )
    second = tick(
        now=datetime(2026, 5, 18, 20, 10),
        state_path=state_path,
        jobs=[job],
        runner=runner,
        pid_checker=lambda _pid: False,
    )

    assert calls == ["daily_close"]
    assert first["jobs"]["daily_close"]["status"] == "SUCCEEDED"
    assert second["jobs"]["daily_close"]["status"] == "SUCCEEDED"
    assert load_state(state_path)["jobs"]["daily_close"]["last_run_date"] == "2026-05-18"


def test_watchdog_marks_missed_job_after_due_window_without_late_run(tmp_path) -> None:
    from scripts.scheduler_watchdog import RunResult, tick

    state_path = tmp_path / "scheduler_state.json"
    job = _job(tmp_path, job_key="open_paper_trade", hour=9, minute=40)
    calls: list[str] = []

    def runner(job_spec):
        calls.append(job_spec.job_key)
        return RunResult(exit_code=0, result="ok")

    state = tick(
        now=datetime(2026, 5, 18, 11, 20),
        state_path=state_path,
        jobs=[job],
        runner=runner,
        pid_checker=lambda _pid: False,
    )

    assert calls == []
    assert state["jobs"]["open_paper_trade"]["status"] == "MISSED"
    assert state["jobs"]["open_paper_trade"]["last_run_date"] == "2026-05-18"
    assert "错过执行窗口" in state["jobs"]["open_paper_trade"]["result"]
    assert state["jobs"]["open_paper_trade"]["next_due_at"] == "2026-05-19T09:40:00"


def test_watchdog_does_not_duplicate_running_job(tmp_path) -> None:
    from scripts.scheduler_watchdog import load_state, save_state, tick

    state_path = tmp_path / "scheduler_state.json"
    job = _job(tmp_path)
    save_state(
        {
            "version": 1,
            "updated_at": "2026-05-18T20:00:30",
            "jobs": {
                "daily_close": {
                    "status": "RUNNING",
                    "last_run_date": "2026-05-18",
                    "pid": 12345,
                    "started_at": "2026-05-18T20:00:30",
                }
            },
        },
        state_path,
    )

    state = tick(
        now=datetime(2026, 5, 18, 20, 5),
        state_path=state_path,
        jobs=[job],
        runner=lambda job_spec: (_ for _ in ()).throw(AssertionError("should not run")),
        pid_checker=lambda pid: pid == 12345,
    )

    assert state["jobs"]["daily_close"]["status"] == "RUNNING"
    assert state["jobs"]["daily_close"]["pid"] == 12345
    assert load_state(state_path)["jobs"]["daily_close"]["status"] == "RUNNING"


def test_watchdog_records_failed_job_result(tmp_path) -> None:
    from scripts.scheduler_watchdog import RunResult, tick

    state_path = tmp_path / "scheduler_state.json"
    job = _job(tmp_path)

    state = tick(
        now=datetime(2026, 5, 18, 20, 1),
        state_path=state_path,
        jobs=[job],
        runner=lambda _job_spec: RunResult(exit_code=2, result="boom"),
        pid_checker=lambda _pid: False,
    )

    assert state["jobs"]["daily_close"]["status"] == "FAILED"
    assert state["jobs"]["daily_close"]["exit_code"] == 2
    assert state["jobs"]["daily_close"]["result"] == "boom"
