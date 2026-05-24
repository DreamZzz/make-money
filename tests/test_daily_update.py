from scripts import daily_update
from src.data_pipeline.main import evaluate_update_health


def test_update_health_ok_when_all_fresh():
    # 非交易日/数据已最新：没有待更新标的，应判定 OK 不中止。
    health = evaluate_update_health({"cn_attempted": 0, "hk_attempted": 0}, {})
    assert health.ok is True
    assert health.level == "OK"


def test_update_health_degraded_not_failed_on_transient_source_errors():
    # 5-22 重试场景：覆盖率高（604+84 / 605+85），但有大量瞬时源错误/限流。
    # 旧逻辑会把 source_error 计入失败而中止；新逻辑应判定 DEGRADED 且不中止。
    stats = {
        "cn_attempted": 605,
        "cn_updated": 604,
        "cn_source_error": 593,
        "hk_attempted": 85,
        "hk_updated": 84,
        "hk_source_error": 1,
    }
    health = evaluate_update_health(stats, {"min_update_success_ratio": 0.5, "max_update_failures": 0})
    assert health.ok is True
    assert health.level == "DEGRADED"
    assert health.transient_errors == 594


def test_update_health_failed_when_coverage_collapses():
    # 5-22 首次尝试：全面限流，几乎 0 更新，应判定 FAILED。
    stats = {
        "cn_attempted": 600,
        "cn_updated": 0,
        "cn_source_error": 593,
        "hk_attempted": 85,
        "hk_updated": 0,
        "hk_source_error": 85,
    }
    health = evaluate_update_health(stats, {"min_update_success_ratio": 0.5})
    assert health.ok is False
    assert health.level == "FAILED"


def test_update_health_failed_on_genuine_exceptions_over_threshold():
    # 真实 per-symbol 异常（非瞬时）超过阈值应中止，即使覆盖率达标。
    stats = {
        "cn_attempted": 100,
        "cn_updated": 95,
        "cn_failed": 5,
    }
    health = evaluate_update_health(stats, {"min_update_success_ratio": 0.5, "max_update_failures": 3})
    assert health.ok is False
    assert "真实失败项 5" in health.reason


def test_daily_update_delegates_to_daily_close(monkeypatch, capsys):
    calls = {}

    class Result:
        returncode = 0
        stdout = "ok\npossibly delisted noise\n"
        stderr = "done\n"

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)

    assert daily_update.main(["alpha158"]) == 0

    assert calls["cmd"] == ["bash", str(daily_update.DAILY_CLOSE), "alpha158"]
    assert calls["kwargs"]["cwd"] == str(daily_update.PROJECT)
    assert calls["kwargs"]["timeout"] == 1800
    captured = capsys.readouterr().out
    assert "ok" in captured
    assert "done" in captured
    assert "possibly delisted" not in captured
