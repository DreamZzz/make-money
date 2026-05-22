import json
import math

from scripts.run_alpha_tournament import main
from src.research.alpha_gate import AlphaGateThresholds, evaluate_alpha_gate


def test_alpha_gate_passes_when_all_metrics_clear_thresholds():
    result = evaluate_alpha_gate(
        {
            "information_ratio": 0.36,
            "correlation_alpha158": 0.42,
            "correlation_benchmark": 0.61,
            "max_drawdown": -0.18,
            "annual_turnover": 0.82,
            "factor_coverage": 0.84,
        },
        thresholds=AlphaGateThresholds(),
    )
    assert result.passed is True
    assert result.failed_reasons == []


def test_alpha_gate_fails_with_named_reasons():
    result = evaluate_alpha_gate(
        {
            "information_ratio": 0.12,
            "correlation_alpha158": 0.69,
            "correlation_benchmark": 0.90,
            "max_drawdown": -0.41,
            "annual_turnover": 1.77,
            "factor_coverage": 0.50,
        },
        thresholds=AlphaGateThresholds(),
    )
    assert result.passed is False
    assert result.failed_reasons == [
        "information_ratio 0.12 < 0.30",
        "correlation_alpha158 0.69 > 0.50",
        "correlation_benchmark 0.90 > 0.70",
        "max_drawdown -0.41 < -0.25",
        "annual_turnover 1.77 > 1.00",
        "factor_coverage 0.50 < 0.80",
    ]


def test_alpha_gate_rejects_nan_and_infinite_metrics():
    result = evaluate_alpha_gate(
        {
            "information_ratio": math.nan,
            "correlation_alpha158": math.inf,
            "correlation_benchmark": math.nan,
            "max_drawdown": -math.inf,
            "annual_turnover": math.inf,
            "factor_coverage": math.nan,
        },
        thresholds=AlphaGateThresholds(),
    )

    assert result.passed is False
    assert result.failed_reasons == [
        "information_ratio 0.00 < 0.30",
        "correlation_alpha158 0.00 > 0.50",
        "correlation_benchmark 0.00 > 0.70",
        "max_drawdown 0.00 < -0.25",
        "annual_turnover 0.00 > 1.00",
        "factor_coverage 0.00 < 0.80",
    ]


def test_alpha_tournament_reports_gate_result(tmp_path, capsys):
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "information_ratio": 0.36,
                "correlation_alpha158": 0.42,
                "correlation_benchmark": 0.61,
                "max_drawdown": -0.18,
                "annual_turnover": 0.82,
                "factor_coverage": 0.84,
            }
        )
    )

    assert main(["--metrics-json", str(metrics_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["failed_reasons"] == []
