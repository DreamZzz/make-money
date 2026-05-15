"""File-backed Dashboard job runner for command workbench workflows."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.config import PROJECT_ROOT

JOBS_DIR = PROJECT_ROOT / "data" / "jobs"
RUNS_DIR = JOBS_DIR / "runs"


def _python_can_import(python: str, module: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _resolve_project_python() -> str:
    return os.environ.get("PYTHON") or sys.executable


def _resolve_qlib_python(default_python: str, candidates: Optional[list[str]] = None) -> str:
    explicit = os.environ.get("QLIB_PYTHON")
    if explicit:
        return explicit

    candidate_values = candidates or [
        default_python,
        "python3",
        "python3.12",
        "/usr/bin/python3",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/opt/python@3.12/bin/python3.12",
    ]
    seen: set[str] = set()
    for candidate in candidate_values:
        resolved = shutil.which(candidate) or candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if _python_can_import(resolved, "qlib"):
            return resolved
    return default_python


PYTHON = _resolve_project_python()
QLIB_PYTHON = _resolve_qlib_python(PYTHON)

PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
DEGRADED = "DEGRADED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
TERMINAL_STATUSES = {SUCCEEDED, DEGRADED, FAILED}
ACTIVE_STATUSES = {PENDING, RUNNING}


@dataclass(frozen=True)
class JobStep:
    key: str
    label: str
    cmd: list[str]
    desc: str = ""
    allow_failure: bool = False
    degraded_exit_codes: tuple[int, ...] = ()


@dataclass(frozen=True)
class JobDefinition:
    key: str
    label: str
    desc: str
    kind: str
    steps: tuple[JobStep, ...]
    category: str = "single"


@dataclass
class JobRun:
    data: dict[str, Any]

    @property
    def run_id(self) -> str:
        return self.data.get("run_id", "")

    @property
    def job_key(self) -> str:
        return self.data.get("job_key", "")

    @property
    def status(self) -> str:
        return self.data.get("status", PENDING)

    @property
    def exit_code(self) -> Optional[int]:
        return self.data.get("exit_code")

    @property
    def log_path(self) -> str:
        return self.data.get("log_path", "")

    @property
    def steps(self) -> list[dict[str, Any]]:
        return list(self.data.get("steps", []))

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


def _step(
    key: str,
    label: str,
    cmd: list[str],
    desc: str = "",
    allow_failure: bool = False,
    degraded_exit_codes: tuple[int, ...] = (),
) -> JobStep:
    return JobStep(
        key=key,
        label=label,
        cmd=cmd,
        desc=desc,
        allow_failure=allow_failure,
        degraded_exit_codes=degraded_exit_codes,
    )


SINGLE_STEPS: dict[str, JobStep] = {
    "update": _step("update", "更新行情数据", [PYTHON, "-m", "src.data_pipeline.main", "update"]),
    "index_funds_update": _step("index_funds_update", "更新指数基金数据", [PYTHON, "-m", "src.index_funds.pipeline", "update"]),
    "index_funds_signals": _step("index_funds_signals", "生成指数基金信号", [PYTHON, "-m", "src.index_funds.signals", "generate"]),
    "generate_signals": _step("generate_signals", "生成股票调仓信号", [PYTHON, "-m", "src.signals.generator"]),
    "qlib_predict": _step(
        "qlib_predict",
        "Qlib production 日常预测",
        [QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "predict-latest", "--model", "production"],
    ),
    "qlib_rule_pk_ab": _step(
        "qlib_rule_pk_ab",
        "记录规则/Qlib A-B影子样本",
        [PYTHON, "-m", "src.dashboard.qlib_rule_pk_service", "record-ab"],
    ),
    "allocation_plan": _step(
        "allocation_plan",
        "生成统一资金分配计划",
        [PYTHON, "-m", "src.portfolio.allocator", "plan"],
    ),
    "paper_trade": _step("paper_trade", "执行股票纸交易", [PYTHON, "-m", "src.portfolio.paper_engine"]),
    "recalculate_nav": _step("recalculate_nav", "重算资金净值", [PYTHON, "-m", "src.portfolio.nav_calculator"]),
    "performance_review": _step("performance_review", "生成阶段评估", [PYTHON, "-m", "src.portfolio.performance"]),
    "signal_outcomes": _step("signal_outcomes", "更新信号收益跟踪", [PYTHON, "-m", "src.signals.outcome_tracker", "update"]),
    "qlib_status": _step("qlib_status", "Qlib 状态检查", [QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "status"]),
    "qlib_prepare": _step(
        "qlib_prepare",
        "准备 Qlib 数据",
        [QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "prepare-data", "--market", "cn"],
    ),
    "qlib_fixed": _step(
        "qlib_fixed",
        "固定切分实验",
        [QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "run-experiment", "--mode", "fixed"],
    ),
    "qlib_walk": _step(
        "qlib_walk",
        "Walk-forward 实验",
        [QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "run-experiment", "--mode", "walk_forward"],
    ),
    "qlib_grid": _step(
        "qlib_grid",
        "Top-N 网格评估",
        [
            QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "evaluate-grid",
            "--experiment-id", "latest",
            "--top-n", "20,50,100",
            "--holding-days", "1-10",
            "--rebalance", "daily,monthly",
            "--buffer-mult", "1.5",
        ],
    ),
    "qlib_candidates": _step(
        "qlib_candidates",
        "Qlib 中换手候选批跑",
        [
            QLIB_PYTHON, "-m", "src.backtest.qlib_runner", "run-candidates",
            "--mode", "walk_forward",
            "--preset", "nightly",
            "--top-n", "20,30,50,80,100",
            "--holding-days", "2-10",
            "--rebalance", "daily,weekly,monthly",
            "--buffer-mult", "1.5",
            "--turnover-profile", "medium",
        ],
    ),
    "open_target_update": _step(
        "open_target_update",
        "开盘目标标的增量更新",
        [
            PYTHON,
            "-c",
            (
                "from scripts.open_paper_trade import _prepare_env, _update_target_symbols; "
                "_prepare_env(); raise SystemExit(_update_target_symbols())"
            ),
        ],
        degraded_exit_codes=(2,),
    ),
}


JOB_DEFINITIONS: dict[str, JobDefinition] = {
    "daily_close_workflow": JobDefinition(
        key="daily_close_workflow",
        label="日常收盘闭环",
        desc="行情、基金、信号、预测、纸交易、净值和阶段评估的一站式收盘链路。",
        kind="workflow",
        category="scenario",
        steps=(
            SINGLE_STEPS["update"],
            SINGLE_STEPS["index_funds_update"],
            SINGLE_STEPS["index_funds_signals"],
            SINGLE_STEPS["generate_signals"],
            SINGLE_STEPS["qlib_predict"],
            SINGLE_STEPS["qlib_rule_pk_ab"],
            SINGLE_STEPS["allocation_plan"],
            SINGLE_STEPS["paper_trade"],
            SINGLE_STEPS["recalculate_nav"],
            SINGLE_STEPS["performance_review"],
            SINGLE_STEPS["signal_outcomes"],
        ),
    ),
    "open_trade_workflow": JobDefinition(
        key="open_trade_workflow",
        label="开盘交易闭环",
        desc="仅更新持仓和待执行信号标的，随后执行纸交易并重算净值。",
        kind="workflow",
        category="scenario",
        steps=(
            SINGLE_STEPS["open_target_update"],
            SINGLE_STEPS["paper_trade"],
            SINGLE_STEPS["recalculate_nav"],
        ),
    ),
    "qlib_research_workflow": JobDefinition(
        key="qlib_research_workflow",
        label="Qlib 研究闭环",
        desc="状态检查、数据准备、fixed、walk-forward 和 Top-N 网格评估。",
        kind="workflow",
        category="scenario",
        steps=(
            SINGLE_STEPS["qlib_status"],
            SINGLE_STEPS["qlib_prepare"],
            SINGLE_STEPS["qlib_fixed"],
            SINGLE_STEPS["qlib_walk"],
            SINGLE_STEPS["qlib_grid"],
        ),
    ),
    "qlib_candidate_workflow": JobDefinition(
        key="qlib_candidate_workflow",
        label="Qlib 中换手候选批跑",
        desc="准备数据后批量运行模型/参数候选，按 30-50 年化换手目标落库记录最佳组合。",
        kind="workflow",
        category="scenario",
        steps=(
            SINGLE_STEPS["qlib_status"],
            SINGLE_STEPS["qlib_prepare"],
            SINGLE_STEPS["qlib_candidates"],
        ),
    ),
}

for single_key, single_step in SINGLE_STEPS.items():
    JOB_DEFINITIONS[single_key] = JobDefinition(
        key=single_key,
        label=single_step.label,
        desc=single_step.desc or "高级单步任务",
        kind="single",
        category="advanced",
        steps=(single_step,),
    )


def scenario_jobs() -> list[JobDefinition]:
    return [job for job in JOB_DEFINITIONS.values() if job.category == "scenario"]


def advanced_jobs() -> list[JobDefinition]:
    return [job for job in JOB_DEFINITIONS.values() if job.category == "advanced"]


def start_job(job_key: str) -> str:
    """Start a job runner process and return its run id."""
    active = active_run()
    if active is not None:
        raise RuntimeError(f"已有任务正在运行：{active.run_id}")

    run_id = _create_run_record(job_key)
    runner_cmd = [sys.executable, "-m", "src.dashboard.job_manager", "run", "--job-key", job_key, "--run-id", run_id]
    env = _command_env()
    proc = subprocess.Popen(
        runner_cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    data = _read_run_data(run_id) or {}
    data["runner_pid"] = proc.pid
    data["status"] = RUNNING
    data["started_at"] = data.get("started_at") or _now()
    _write_run_data(run_id, data)
    return run_id


def run_job(job_key: str, run_id: Optional[str] = None) -> JobRun:
    """Run a job synchronously. Used by the background runner and tests."""
    if run_id is None:
        run_id = _create_run_record(job_key)
    job = _get_job(job_key)
    data = _read_run_data(run_id) or _new_run_data(job, run_id)
    data.update({
        "status": RUNNING,
        "started_at": data.get("started_at") or _now(),
        "runner_pid": os.getpid(),
        "current_step": None,
    })
    _write_run_data(run_id, data)
    _append_log(run_id, f"=== {_now()} START {job.label} ({job.key}) ===\n")

    exit_code = 0
    degraded = False
    for index, step in enumerate(job.steps):
        data = _read_run_data(run_id) or data
        _mark_step(data, step.key, RUNNING, started_at=_now())
        data["current_step"] = step.key
        _write_run_data(run_id, data)
        _append_log(run_id, f"\n--- {index + 1}/{len(job.steps)} {step.label} ---\n")
        _append_log(run_id, f"$ {' '.join(step.cmd)}\n")

        retcode = _run_step(run_id, step)
        if retcode in step.degraded_exit_codes:
            status = DEGRADED
            degraded = True
        elif retcode == 0 or step.allow_failure:
            status = SUCCEEDED
        else:
            status = FAILED
        _append_log(run_id, f"--- {step.label} exit={retcode} ---\n")

        data = _read_run_data(run_id) or data
        _mark_step(data, step.key, status, ended_at=_now(), exit_code=retcode)
        _write_run_data(run_id, data)

        if retcode != 0 and status != DEGRADED and not step.allow_failure:
            exit_code = retcode
            data = _read_run_data(run_id) or data
            for remaining in job.steps[index + 1:]:
                _mark_step(data, remaining.key, SKIPPED, ended_at=_now())
            data.update({
                "status": FAILED,
                "exit_code": exit_code,
                "ended_at": _now(),
                "current_step": None,
            })
            _write_run_data(run_id, data)
            _append_log(run_id, f"=== {_now()} FAILED {job.label} exit={exit_code} ===\n")
            return JobRun(data)

    data = _read_run_data(run_id) or data
    data.update({
        "status": DEGRADED if degraded else SUCCEEDED,
        "exit_code": exit_code,
        "ended_at": _now(),
        "current_step": None,
    })
    _write_run_data(run_id, data)
    final_label = DEGRADED if degraded else SUCCEEDED
    _append_log(run_id, f"=== {_now()} {final_label} {job.label} ===\n")
    return JobRun(data)


def poll_run(run_id: Optional[str]) -> Optional[JobRun]:
    if not run_id:
        return None
    data = _read_run_data(run_id)
    if data is None:
        return None
    if data.get("status") in ACTIVE_STATUSES and not _pid_alive(data.get("runner_pid")):
        data["status"] = FAILED
        data["exit_code"] = data.get("exit_code") if data.get("exit_code") is not None else -1
        data["ended_at"] = data.get("ended_at") or _now()
        data["error"] = data.get("error") or "runner process is not alive"
        _write_run_data(run_id, data)
    return JobRun(data)


def active_run() -> Optional[JobRun]:
    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        run = poll_run(path.stem)
        if run and run.status in ACTIVE_STATUSES:
            return run
    return None


def latest_run(job_key: Optional[str] = None) -> Optional[JobRun]:
    candidates = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        run = poll_run(path.stem)
        if run and (job_key is None or run.job_key == job_key):
            return run
    return None


def tail_log(run_id: Optional[str], lines: int = 200) -> str:
    run = poll_run(run_id)
    if run is None or not run.log_path:
        return ""
    path = Path(run.log_path)
    if not path.exists():
        return ""
    text = path.read_text(errors="replace")
    if lines <= 0:
        return text
    return "\n".join(text.splitlines()[-lines:])


def _create_run_record(job_key: str) -> str:
    job = _get_job(job_key)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id(job_key)
    data = _new_run_data(job, run_id)
    _write_run_data(run_id, data)
    Path(data["log_path"]).touch()
    return run_id


def _new_run_data(job: JobDefinition, run_id: str) -> dict[str, Any]:
    log_path = RUNS_DIR / f"{run_id}.log"
    return {
        "run_id": run_id,
        "job_key": job.key,
        "job_label": job.label,
        "job_type": job.kind,
        "status": PENDING,
        "runner_pid": None,
        "started_at": None,
        "ended_at": None,
        "exit_code": None,
        "current_step": None,
        "log_path": str(log_path),
        "steps": [
            {
                "key": step.key,
                "label": step.label,
                "cmd": step.cmd,
                "status": PENDING,
                "started_at": None,
                "ended_at": None,
                "exit_code": None,
            }
            for step in job.steps
        ],
    }


def _read_run_data(run_id: str) -> Optional[dict[str, Any]]:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _write_run_data(run_id: str, data: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{run_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _append_log(run_id: str, text: str) -> None:
    data = _read_run_data(run_id)
    if not data:
        return
    path = Path(data["log_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def _run_step(run_id: str, step: JobStep) -> int:
    data = _read_run_data(run_id)
    if not data:
        return 1
    with Path(data["log_path"]).open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            step.cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=_command_env(),
        )
        return proc.wait()


def _mark_step(
    data: dict[str, Any],
    step_key: str,
    status: str,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> None:
    for step in data.get("steps", []):
        if step.get("key") != step_key:
            continue
        step["status"] = status
        if started_at is not None:
            step["started_at"] = started_at
        if ended_at is not None:
            step["ended_at"] = ended_at
        if exit_code is not None:
            step["exit_code"] = exit_code
        return


def _get_job(job_key: str) -> JobDefinition:
    try:
        return JOB_DEFINITIONS[job_key]
    except KeyError as exc:
        raise ValueError(f"Unknown job key: {job_key}") from exc


def _new_run_id(job_key: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"JOB-{job_key.upper()}-{stamp}-{suffix}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(key, None)
    env["no_proxy"] = "*"
    return env


def _pid_alive(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dashboard job manager")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--job-key", required=True)
    run_parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    if args.command == "run":
        run = run_job(args.job_key, args.run_id)
        return int(run.exit_code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
