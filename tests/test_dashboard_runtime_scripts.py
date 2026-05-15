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
    assert "python3.12" in script
    assert "trap _restart_dashboard EXIT" in script


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
