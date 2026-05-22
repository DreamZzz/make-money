import sys

from src.dashboard import job_manager as jm


def _install_jobs(monkeypatch, tmp_path, jobs):
    monkeypatch.setattr(jm, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(jm, "JOB_DEFINITIONS", jobs)


def _job(key, steps, kind="single"):
    return jm.JobDefinition(
        key=key,
        label=key,
        desc="test job",
        kind=kind,
        steps=tuple(steps),
    )


def test_job_manager_single_success_persists_and_restores(monkeypatch, tmp_path):
    jobs = {
        "ok": _job("ok", [
            jm.JobStep("hello", "hello", [sys.executable, "-c", "print('hello job')"]),
        ])
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("ok")
    restored = jm.poll_run(run.run_id)

    assert run.status == jm.SUCCEEDED
    assert restored is not None
    assert restored.status == jm.SUCCEEDED
    assert restored.exit_code == 0
    assert restored.steps[0]["status"] == jm.SUCCEEDED
    assert "hello job" in jm.tail_log(run.run_id)
    assert jm.latest_run("ok").run_id == run.run_id


def test_job_manager_cli_run_creates_run_id_when_omitted(monkeypatch, tmp_path):
    jobs = {
        "ok": _job("ok", [
            jm.JobStep("hello", "hello", [sys.executable, "-c", "print('hello cli')"]),
        ])
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    exit_code = jm.main(["run", "--job-key", "ok"])

    latest = jm.latest_run("ok")
    assert exit_code == 0
    assert latest is not None
    assert latest.status == jm.SUCCEEDED
    assert latest.run_id.startswith("JOB-OK-")
    assert "hello cli" in jm.tail_log(latest.run_id)


def test_job_manager_workflow_stops_after_failed_step(monkeypatch, tmp_path):
    jobs = {
        "flow": _job("flow", [
            jm.JobStep("first", "first", [sys.executable, "-c", "print('first')"]),
            jm.JobStep("fail", "fail", [sys.executable, "-c", "import sys; print('failed'); sys.exit(7)"]),
            jm.JobStep("after", "after", [sys.executable, "-c", "print('after')"]),
        ], kind="workflow")
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("flow")
    statuses = [step["status"] for step in run.steps]

    assert run.status == jm.FAILED
    assert run.exit_code == 7
    assert statuses == [jm.SUCCEEDED, jm.FAILED, jm.SKIPPED]
    log_text = jm.tail_log(run.run_id, lines=20)
    assert "failed" in log_text
    assert "after" not in log_text


def test_job_manager_persists_step_diagnostics_for_failure(monkeypatch, tmp_path):
    jobs = {
        "flow": _job("flow", [
            jm.JobStep("ok", "ok", [sys.executable, "-c", "print('ok')"]),
            jm.JobStep(
                "fail",
                "fail",
                [
                    sys.executable,
                    "-c",
                    "import sys; print('stdout-before'); sys.stderr.write('stderr-boom\\n'); sys.exit(7)",
                ],
            ),
        ], kind="workflow")
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("flow")
    failed_step = run.steps[1]

    assert run.status == jm.FAILED
    assert failed_step["status"] == jm.FAILED
    assert failed_step["exit_code"] == 7
    assert failed_step["cmd_text"].endswith("sys.exit(7)")
    assert failed_step["duration_seconds"] >= 0
    assert "stdout-before" in failed_step["log_excerpt"]
    assert "stderr-boom" in failed_step["log_excerpt"]


def test_job_manager_latest_failure_diagnostic_identifies_failed_step(monkeypatch, tmp_path):
    jobs = {
        "flow": _job("flow", [
            jm.JobStep("ok", "ok", [sys.executable, "-c", "print('ok')"]),
            jm.JobStep("fail", "fail", [sys.executable, "-c", "import sys; print('fatal detail'); sys.exit(3)"]),
            jm.JobStep("after", "after", [sys.executable, "-c", "print('after')"]),
        ], kind="workflow")
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("flow")
    diagnostic = jm.latest_failure_diagnostic(run)

    assert diagnostic is not None
    assert diagnostic["run_id"] == run.run_id
    assert diagnostic["step_key"] == "fail"
    assert diagnostic["step_label"] == "fail"
    assert diagnostic["exit_code"] == 3
    assert diagnostic["cmd_text"].endswith("sys.exit(3)")
    assert "fatal detail" in diagnostic["log_excerpt"]


def test_job_manager_degraded_step_continues_and_marks_workflow(monkeypatch, tmp_path):
    jobs = {
        "flow": _job("flow", [
            jm.JobStep("warn", "warn", [sys.executable, "-c", "import sys; print('warn'); sys.exit(2)"], degraded_exit_codes=(2,)),
            jm.JobStep("after", "after", [sys.executable, "-c", "print('after')"]),
        ], kind="workflow")
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("flow")

    assert run.status == jm.DEGRADED
    assert run.exit_code == 0
    assert [step["status"] for step in run.steps] == [jm.DEGRADED, jm.SUCCEEDED]
    log_text = jm.tail_log(run.run_id, lines=40)
    assert "warn" in log_text
    assert "after" in log_text


def test_job_manager_tail_log_limits_lines(monkeypatch, tmp_path):
    jobs = {
        "lines": _job("lines", [
            jm.JobStep(
                "emit",
                "emit",
                [sys.executable, "-c", "for i in range(5): print(f'line-{i}')"],
            ),
        ])
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    run = jm.run_job("lines")
    with open(run.log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"line-{i}" for i in range(5)))

    assert jm.tail_log("missing") == ""
    assert jm.tail_log(run.run_id, lines=2).splitlines() == ["line-3", "line-4"]
    assert "line-0" in jm.tail_log(run.run_id, lines=0)


def test_job_manager_latest_run_without_session_state(monkeypatch, tmp_path):
    jobs = {
        "a": _job("a", [jm.JobStep("a", "a", [sys.executable, "-c", "print('a')"])]),
        "b": _job("b", [jm.JobStep("b", "b", [sys.executable, "-c", "print('b')"])]),
    }
    _install_jobs(monkeypatch, tmp_path, jobs)

    first = jm.run_job("a")
    second = jm.run_job("b")

    assert jm.poll_run(first.run_id).status == jm.SUCCEEDED
    assert jm.latest_run().run_id == second.run_id
    assert jm.latest_run("a").run_id == first.run_id


def test_resolve_qlib_python_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("QLIB_PYTHON", "/custom/qlib-python")
    assert jm._resolve_qlib_python("/default", candidates=["/other"]) == "/custom/qlib-python"


def test_resolve_qlib_python_prefers_project_venv_by_default(monkeypatch):
    monkeypatch.delenv("QLIB_PYTHON", raising=False)
    project_venv = str(jm.PROJECT_ROOT / ".venv-qlib" / "bin" / "python")
    monkeypatch.setattr(jm, "_python_can_import", lambda python, module: python == project_venv and module == "qlib")
    monkeypatch.setattr(jm, "_python_is_project_compatible", lambda python: python == project_venv)

    result = jm._resolve_qlib_python("/opt/python3.12")

    assert result == project_venv


def test_resolve_qlib_python_finds_candidate_that_imports_qlib(monkeypatch):
    monkeypatch.delenv("QLIB_PYTHON", raising=False)
    monkeypatch.setattr(jm, "_python_can_import", lambda python, module: python == "/usr/bin/python3" and module == "qlib")
    monkeypatch.setattr(jm, "_python_is_project_compatible", lambda python: python == "/usr/bin/python3")
    result = jm._resolve_qlib_python("/opt/python3.12", candidates=["/opt/python3.12", "/usr/bin/python3"])
    assert result == "/usr/bin/python3"


def test_resolve_qlib_python_rejects_qlib_python_that_cannot_run_project(monkeypatch):
    monkeypatch.delenv("QLIB_PYTHON", raising=False)
    monkeypatch.setattr(jm, "_python_can_import", lambda python, module: python == "/usr/bin/python3" and module == "qlib")
    monkeypatch.setattr(jm, "_python_is_project_compatible", lambda python: python != "/usr/bin/python3")

    result = jm._resolve_qlib_python("/opt/python3.12", candidates=["/usr/bin/python3", "/opt/python3.12"])

    assert result == "/opt/python3.12"


def test_qlib_job_steps_use_qlib_capable_python_when_available():
    qlib_steps = {
        "qlib_status", "qlib_prepare", "qlib_fixed", "qlib_walk",
        "qlib_grid", "qlib_candidates", "qlib_predict",
    }
    for key in qlib_steps:
        assert jm.SINGLE_STEPS[key].cmd[0] == jm.QLIB_PYTHON


def test_daily_close_workflow_plans_allocation_without_paper_trade():
    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close_workflow"].steps]

    assert "allocation_plan" in step_keys
    assert "signal_arbiter" in step_keys
    assert "paper_trade" not in step_keys
    assert "model_prediction_gate" in step_keys
    assert step_keys.index("qlib_predict") < step_keys.index("model_prediction_gate")
    assert step_keys.index("model_prediction_gate") < step_keys.index("signal_arbiter")
    assert step_keys.index("signal_arbiter") < step_keys.index("allocation_plan")
    assert step_keys.index("qlib_rule_pk_ab") < step_keys.index("allocation_plan")
    assert step_keys.index("allocation_plan") < step_keys.index("recalculate_nav")
    assert jm.SINGLE_STEPS["model_prediction_gate"].cmd == [
        jm.PYTHON,
        "-m",
        "src.monitoring.model_monitor",
        "assert-prediction-ready",
    ]
    assert jm.SINGLE_STEPS["signal_arbiter"].cmd == [jm.PYTHON, "-m", "src.signals.arbiter"]
    assert jm.SINGLE_STEPS["allocation_plan"].cmd[:3] == [jm.PYTHON, "-m", "src.portfolio.allocator"]


def test_open_trade_workflow_is_the_only_workflow_that_executes_paper_trade():
    open_step_keys = [step.key for step in jm.JOB_DEFINITIONS["open_trade_workflow"].steps]

    assert "paper_trade" in open_step_keys
    assert open_step_keys.index("open_target_update") < open_step_keys.index("paper_trade")
    assert open_step_keys.index("paper_trade") < open_step_keys.index("recalculate_nav")


def test_daily_close_workflow_refreshes_holding_fundamentals_before_signals():
    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close_workflow"].steps]

    assert "fundamentals_coverage" in step_keys
    assert step_keys.index("update") < step_keys.index("fundamentals_coverage")
    assert step_keys.index("fundamentals_coverage") < step_keys.index("generate_signals")
    assert jm.SINGLE_STEPS["fundamentals_coverage"].cmd == [
        jm.PYTHON,
        "-m",
        "src.portfolio.fundamentals_coverage",
        "update",
    ]


def test_daily_close_job_includes_field_coverage_step_after_fundamentals():
    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close_workflow"].steps]

    assert "fundamentals_coverage" in step_keys
    assert "field_coverage" in step_keys
    assert step_keys.index("fundamentals_coverage") < step_keys.index("field_coverage")
    assert step_keys.index("field_coverage") < step_keys.index("index_funds_update")
    assert jm.SINGLE_STEPS["field_coverage"].cmd == [
        jm.PYTHON,
        "-m",
        "src.data_pipeline.field_coverage_backfill",
        "--scopes",
        "current_holdings,signal_candidates,target_universe",
        "--skip-industry-fetch",
        "--record-health",
    ]


def test_daily_close_workflow_updates_signal_outcomes_after_nav_and_performance():
    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close_workflow"].steps]

    assert "signal_outcomes" in step_keys
    assert step_keys.index("recalculate_nav") < step_keys.index("signal_outcomes")
    assert step_keys.index("performance_review") < step_keys.index("signal_outcomes")
    assert jm.SINGLE_STEPS["signal_outcomes"].cmd == [
        jm.PYTHON,
        "-m",
        "src.signals.outcome_tracker",
        "update",
    ]


def test_daily_close_workflow_runs_model_monitor_after_signal_outcomes():
    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close_workflow"].steps]

    assert "model_monitor" in step_keys
    assert step_keys.index("signal_outcomes") < step_keys.index("model_monitor")
    assert jm.SINGLE_STEPS["model_monitor"].cmd == [
        jm.PYTHON,
        "-m",
        "src.monitoring.model_monitor",
        "update",
    ]
