from __future__ import annotations

import pytest

from src.config import DEFAULT_CONFIG, load_config


def test_load_config_merges_base_settings_without_environment(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        """
portfolio:
  initial_capital_cn: 123456
signals:
  min_confidence: 0.7
""",
        encoding="utf-8",
    )
    (config_dir / "settings.dev.yaml").write_text(
        """
signals:
  min_confidence: 0.4
""",
        encoding="utf-8",
    )

    cfg = load_config(env="", config_dir=config_dir)

    assert cfg["portfolio"]["initial_capital_cn"] == 123456
    assert cfg["signals"]["min_confidence"] == 0.7


def test_load_config_applies_environment_overlay_after_base_settings(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        """
portfolio:
  initial_capital_cn: 300000
  allocation:
    core_target_pct: 0.6
signals:
  min_confidence: 0.7
""",
        encoding="utf-8",
    )
    (config_dir / "settings.dev.yaml").write_text(
        """
portfolio:
  allocation:
    core_target_pct: 0.5
signals:
  min_confidence: 0.4
""",
        encoding="utf-8",
    )

    cfg = load_config(env="dev", config_dir=config_dir)

    assert cfg["portfolio"]["initial_capital_cn"] == 300000
    assert cfg["portfolio"]["allocation"]["core_target_pct"] == 0.5
    assert cfg["portfolio"]["allocation"]["satellite_target_pct"] == DEFAULT_CONFIG["portfolio"]["allocation"][
        "satellite_target_pct"
    ]
    assert cfg["signals"]["min_confidence"] == 0.4


def test_load_config_uses_mm_env_when_explicit_env_is_not_passed(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.prod.yaml").write_text(
        """
data:
  max_update_failures: 3
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MM_ENV", "prod")

    cfg = load_config(config_dir=config_dir)

    assert cfg["data"]["max_update_failures"] == 3


def test_load_config_rejects_unsafe_environment_names(tmp_path):
    with pytest.raises(ValueError, match="MM_ENV"):
        load_config(env="../prod", config_dir=tmp_path)
