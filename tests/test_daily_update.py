from scripts import daily_update


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
