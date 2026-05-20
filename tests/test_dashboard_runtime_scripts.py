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
    assert "src.signals.arbiter" in script
    assert "src.portfolio.nav_calculator" in script
    assert "src.monitoring.model_monitor update" in script
    assert script.index("predict-latest --model production") < script.index("src.signals.arbiter")
    assert script.index("src.signals.arbiter") < script.index("src.portfolio.allocator plan")
    assert script.index("src.signals.outcome_tracker update") < script.index("src.monitoring.model_monitor update")


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


def test_scheduler_watchdog_install_script_replaces_calendar_agents():
    script = Path("scripts/install_scheduler_watchdog.sh").read_text()

    assert "com.quant.scheduler-watchdog" in script
    assert "StartInterval" in script
    assert "scripts/scheduler_watchdog.py" in script
    assert "com.quant.daily-update" in script
    assert "com.quant.open-paper-trade" in script


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
