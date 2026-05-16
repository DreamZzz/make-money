"""Retail account risk profile helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskProfileSettings:
    name: str
    label: str
    max_stock_positions: int
    max_single_position_pct: float
    overweight_single_position_pct: float
    min_rebalance_buy_confidence: float
    min_rebalance_buy_rank_score: float
    estimated_minutes_per_operation: int


DEFAULT_RISK_PROFILES: dict[str, dict[str, Any]] = {
    "small": {
        "label": "小资金档",
        "max_stock_positions": 5,
        "max_single_position_pct": 0.20,
        "overweight_single_position_pct": 0.25,
        "min_rebalance_buy_confidence": 0.75,
        "min_rebalance_buy_rank_score": 0.50,
        "estimated_minutes_per_operation": 4,
    },
    "medium": {
        "label": "中等资金档",
        "max_stock_positions": 10,
        "max_single_position_pct": 0.10,
        "overweight_single_position_pct": 0.15,
        "min_rebalance_buy_confidence": 0.75,
        "min_rebalance_buy_rank_score": 0.50,
        "estimated_minutes_per_operation": 3,
    },
    "large": {
        "label": "大资金档",
        "max_stock_positions": 15,
        "max_single_position_pct": 0.10,
        "overweight_single_position_pct": 0.15,
        "min_rebalance_buy_confidence": 0.75,
        "min_rebalance_buy_rank_score": 0.50,
        "estimated_minutes_per_operation": 3,
    },
}


def resolve_risk_profile(config: dict[str, Any], total_value: float | None = None) -> RiskProfileSettings:
    """Resolve explicit or account-size based retail risk profile."""
    portfolio = config.get("portfolio", {}) if isinstance(config, dict) else {}
    requested = str(portfolio.get("risk_profile", "auto") or "auto").strip().lower()
    profile_name = _auto_profile(total_value, portfolio) if requested == "auto" else requested
    profiles = _merged_profiles(portfolio.get("risk_profiles", {}))
    if profile_name not in profiles:
        raise ValueError(f"Unknown portfolio risk_profile: {profile_name}")
    raw = profiles[profile_name]
    return RiskProfileSettings(
        name=profile_name,
        label=str(raw.get("label") or profile_name),
        max_stock_positions=max(int(raw.get("max_stock_positions", 10)), 1),
        max_single_position_pct=max(float(raw.get("max_single_position_pct", 0.10)), 0.0),
        overweight_single_position_pct=max(float(raw.get("overweight_single_position_pct", 0.15)), 0.0),
        min_rebalance_buy_confidence=max(float(raw.get("min_rebalance_buy_confidence", 0.75)), 0.0),
        min_rebalance_buy_rank_score=max(float(raw.get("min_rebalance_buy_rank_score", 0.50)), 0.0),
        estimated_minutes_per_operation=max(int(raw.get("estimated_minutes_per_operation", 3)), 1),
    )


def apply_risk_profile_to_portfolio_config(
    config: dict[str, Any],
    total_value: float | None = None,
) -> tuple[dict[str, Any], RiskProfileSettings]:
    """Return portfolio config with the selected risk profile applied."""
    portfolio = dict((config or {}).get("portfolio", {}))
    profile = resolve_risk_profile(config, total_value=total_value)
    portfolio.update({
        "max_stock_positions": profile.max_stock_positions,
        "max_single_position_pct": profile.max_single_position_pct,
        "overweight_single_position_pct": max(
            profile.overweight_single_position_pct,
            profile.max_single_position_pct,
        ),
        "min_rebalance_buy_confidence": profile.min_rebalance_buy_confidence,
        "min_rebalance_buy_rank_score": profile.min_rebalance_buy_rank_score,
        "estimated_minutes_per_operation": profile.estimated_minutes_per_operation,
        "resolved_risk_profile": profile.name,
    })
    return portfolio, profile


def _auto_profile(total_value: float | None, portfolio: dict[str, Any]) -> str:
    try:
        value = float(total_value if total_value is not None else portfolio.get("initial_capital_cn", 300000))
    except (TypeError, ValueError):
        value = 300000.0
    if value <= 100000:
        return "small"
    if value <= 500000:
        return "medium"
    return "large"


def _merged_profiles(overrides: Any) -> dict[str, dict[str, Any]]:
    profiles = {name: values.copy() for name, values in DEFAULT_RISK_PROFILES.items()}
    if isinstance(overrides, dict):
        for name, values in overrides.items():
            if isinstance(values, dict):
                key = str(name).strip().lower()
                profiles.setdefault(key, {}).update(values)
    return profiles
