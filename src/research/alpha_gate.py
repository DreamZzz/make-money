"""Promotion gate for research-only alpha candidates."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlphaGateThresholds:
    min_information_ratio: float = 0.30
    max_correlation_alpha158: float = 0.50
    max_correlation_benchmark: float = 0.70
    min_max_drawdown: float = -0.25
    max_annual_turnover: float = 1.00
    min_factor_coverage: float = 0.80


@dataclass(frozen=True)
class AlphaGateResult:
    passed: bool
    failed_reasons: list[str]
    metrics: dict[str, float | None]


def _metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_value(value: float | None) -> str:
    return f"{0.0 if value is None else value:.2f}"


def evaluate_alpha_gate(
    metrics: dict[str, Any],
    thresholds: AlphaGateThresholds | None = None,
) -> AlphaGateResult:
    thresholds = thresholds or AlphaGateThresholds()
    values = {
        "information_ratio": _metric(metrics, "information_ratio"),
        "correlation_alpha158": _metric(metrics, "correlation_alpha158"),
        "correlation_benchmark": _metric(metrics, "correlation_benchmark"),
        "max_drawdown": _metric(metrics, "max_drawdown"),
        "annual_turnover": _metric(metrics, "annual_turnover"),
        "factor_coverage": _metric(metrics, "factor_coverage"),
    }
    failed: list[str] = []

    information_ratio = values["information_ratio"]
    if information_ratio is None or information_ratio < thresholds.min_information_ratio:
        failed.append(
            "information_ratio "
            f"{_format_value(information_ratio)} < {thresholds.min_information_ratio:.2f}"
        )

    correlation_alpha158 = values["correlation_alpha158"]
    if (
        correlation_alpha158 is None
        or correlation_alpha158 > thresholds.max_correlation_alpha158
    ):
        failed.append(
            "correlation_alpha158 "
            f"{_format_value(correlation_alpha158)} > "
            f"{thresholds.max_correlation_alpha158:.2f}"
        )

    correlation_benchmark = values["correlation_benchmark"]
    if (
        correlation_benchmark is None
        or correlation_benchmark > thresholds.max_correlation_benchmark
    ):
        failed.append(
            "correlation_benchmark "
            f"{_format_value(correlation_benchmark)} > "
            f"{thresholds.max_correlation_benchmark:.2f}"
        )

    max_drawdown = values["max_drawdown"]
    if max_drawdown is None or max_drawdown < thresholds.min_max_drawdown:
        failed.append(
            "max_drawdown "
            f"{_format_value(max_drawdown)} < {thresholds.min_max_drawdown:.2f}"
        )

    annual_turnover = values["annual_turnover"]
    if annual_turnover is None or annual_turnover > thresholds.max_annual_turnover:
        failed.append(
            "annual_turnover "
            f"{_format_value(annual_turnover)} > {thresholds.max_annual_turnover:.2f}"
        )

    factor_coverage = values["factor_coverage"]
    if factor_coverage is None or factor_coverage < thresholds.min_factor_coverage:
        failed.append(
            "factor_coverage "
            f"{_format_value(factor_coverage)} < {thresholds.min_factor_coverage:.2f}"
        )

    return AlphaGateResult(passed=not failed, failed_reasons=failed, metrics=values)
