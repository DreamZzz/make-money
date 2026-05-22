import os
import subprocess
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_start_script_resolves_python_312_runtime():
    script = Path("scripts/start_dashboard.sh").read_text()

    assert 'PYTHON="${PYTHON:-python3}"' not in script
    assert "_resolve_python" in script
    assert "python3.12" in script
    assert "Python 3.12+" in script


def test_daily_close_restarts_dashboard_even_when_a_step_fails():
    script = Path("scripts/daily_close.sh").read_text()

    assert 'PYTHON="${PYTHON:-python3}"' not in script
    assert "_resolve_python" in script
    assert "_resolve_qlib_python" in script
    assert "python3.12" in script
    assert "trap _restart_dashboard EXIT" in script
    assert "src.portfolio.fundamentals_coverage update || true" in script
    assert "src.portfolio.paper_engine" not in script
    assert "纸交易只能由 scripts/open_paper_trade.py 在开盘窗口执行" in script
    assert "src.signals.arbiter" in script
    assert "src.portfolio.nav_calculator" in script
    assert "src.monitoring.model_monitor update" in script
    assert script.index("predict-latest --model production") < script.index("src.signals.arbiter")
    assert "src.monitoring.model_monitor assert-prediction-ready" in script
    assert script.index("predict-latest --model production") < script.index("src.monitoring.model_monitor assert-prediction-ready")
    assert script.index("src.monitoring.model_monitor assert-prediction-ready") < script.index("src.signals.arbiter")
    assert script.index("src.signals.arbiter") < script.index("src.portfolio.allocator plan")
    assert script.index("src.signals.outcome_tracker update") < script.index("src.monitoring.model_monitor update")


def test_daily_close_runs_field_coverage_after_fundamentals_coverage():
    script = Path("scripts/daily_close.sh").read_text()
    fundamentals = "src.portfolio.fundamentals_coverage update || true"
    field_coverage = (
        "src.data_pipeline.field_coverage_backfill "
        "--scopes current_holdings,signal_candidates,target_universe "
        "--skip-industry-fetch --record-health || true"
    )

    assert fundamentals in script
    assert field_coverage in script
    assert script.index(fundamentals) < script.index(field_coverage)
    assert script.index(field_coverage) < script.index("src.index_funds.pipeline update")


def test_daily_close_discovers_project_qlib_venv_before_global_python():
    script = Path("scripts/daily_close.sh").read_text()
    project_venv = '"$PROJECT/.venv-qlib/bin/python"'
    default_python = '"$1" python3.12'

    assert project_venv in script
    assert script.index(project_venv) < script.index(default_python)


def test_dashboard_v2_start_script_self_checks_and_reuses_running_services():
    script = Path("scripts/run_dashboard_v2.sh").read_text()

    assert "_resolve_python" in script
    assert "_resolve_node" in script
    assert "_ensure_frontend_dependencies" in script
    assert "_wait_for_url" in script
    assert "_port_owner" in script
    assert "DASHBOARD_V2_REUSE_EXISTING" in script
    assert "/api/v2/health" in script
    assert "tail -80" in script


def test_dashboard_v2_start_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", "scripts/run_dashboard_v2.sh"], check=True)


def test_open_trade_review_script_exists_and_queries_orders_and_blocked_signals():
    script = Path("scripts/review_open_trade_day.py").read_text()

    assert "开盘纸交易复盘" in script
    assert "paper_orders" in script
    assert "DEFERRED_BUDGET" in script
    assert "--date" in script


def test_run_backtest_prefers_project_qlib_venv():
    script = Path("scripts/run_backtest.sh").read_text()
    project_venv = '"$PROJECT_DIR/.venv-qlib/bin/python"'
    global_python = "python3.12"

    assert project_venv in script
    assert script.index(project_venv) < script.index(global_python)


def test_scheduler_watchdog_install_script_replaces_calendar_agents():
    script = Path("scripts/install_scheduler_watchdog.sh").read_text()

    assert "com.quant.scheduler-watchdog" in script
    assert "StartInterval" in script
    assert "scripts/scheduler_watchdog.py" in script
    assert "com.quant.daily-update" in script
    assert "com.quant.open-paper-trade" in script
    assert "QLIB_PYTHON" in script
    assert "$PROJECT/.venv-qlib/bin/python" in script


def test_scheduler_watchdog_install_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", "scripts/install_scheduler_watchdog.sh"], check=True)


def test_dashboard_v2_status_handles_stopped_ports():
    result = subprocess.run(
        ["bash", "scripts/run_dashboard_v2.sh", "--status"],
        check=True,
        capture_output=True,
        env={**os.environ, "API_PORT": "59998", "FRONTEND_PORT": "59999"},
        text=True,
    )

    assert "API        stopped  port=59998" in result.stdout
    assert "Frontend   stopped  port=59999" in result.stdout


def test_dashboard_app_renders_primary_pages_without_exceptions():
    app = AppTest.from_file("src/dashboard/app.py", default_timeout=15)
    app.run()
    assert not app.exception

    for page_path in [
        "views/strategy_compare.py",
        "views/portfolio.py",
        "views/index_funds.py",
        "views/qlib_analysis.py",
    ]:
        app.switch_page(page_path).run()
        assert not app.exception, page_path
