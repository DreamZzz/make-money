from __future__ import annotations

from src.config import DEFAULT_CONFIG
from src.portfolio.risk_profile import apply_risk_profile_to_portfolio_config, resolve_risk_profile


def test_resolve_risk_profile_auto_uses_account_size_bands():
    assert resolve_risk_profile(DEFAULT_CONFIG, total_value=50_000).name == "small"
    assert resolve_risk_profile(DEFAULT_CONFIG, total_value=300_000).name == "medium"
    assert resolve_risk_profile(DEFAULT_CONFIG, total_value=800_000).name == "large"


def test_apply_small_risk_profile_relaxes_single_name_and_limits_stock_count():
    cfg = {
        **DEFAULT_CONFIG,
        "portfolio": {
            **DEFAULT_CONFIG["portfolio"],
            "risk_profile": "small",
        },
    }

    portfolio_cfg, profile = apply_risk_profile_to_portfolio_config(cfg, total_value=50_000)

    assert profile.name == "small"
    assert portfolio_cfg["max_stock_positions"] == 5
    assert portfolio_cfg["max_single_position_pct"] > DEFAULT_CONFIG["portfolio"]["max_single_position_pct"]
    assert portfolio_cfg["overweight_single_position_pct"] >= portfolio_cfg["max_single_position_pct"]

