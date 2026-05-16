import json

import duckdb
import pandas as pd
import pytest

from src.dashboard.qlib_report_service import (
    add_rolling_ic_columns,
    get_metric_glossary,
    get_metric_highlight_standards,
    load_experiment_report,
    parse_json_dict,
)
from src.data_pipeline.loader import init_db


def _conn():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    return conn


def test_qlib_report_flattens_experiment_metrics_and_benchmarks():
    conn = _conn()
    metrics = {
        "annual_return": 0.12,
        "sharpe_ratio": 0.8,
        "max_drawdown": -0.18,
        "turnover": 12.5,
        "ic_mean": 0.015,
        "icir": 0.35,
        "rank_ic_mean": 0.02,
        "rank_ic_positive_rate": 0.58,
        "primary_benchmark": "MIXED_EQUAL",
        "excess_return": 0.04,
        "benchmark_suite": {
            "000300": {"benchmark_return": 0.05, "excess_return": 0.07, "info_ratio": 0.4},
            "MIXED_EQUAL": {"benchmark_return": 0.08, "excess_return": 0.04, "info_ratio": 0.3},
        },
    }
    config = {"candidate": {"batch_id": "B1", "candidate_id": "lgb_balanced", "model_variant": "balanced"}}
    conn.execute(
        """
        INSERT INTO qlib_experiments (
            experiment_id, model_name, model_version, mode, status,
            test_start, test_end, metrics_json, config_snapshot,
            started_at, ended_at
        )
        VALUES (
            'E1', 'alpha158', 'v1', 'walk_forward', 'SUCCEEDED',
            DATE '2024-01-01', DATE '2026-05-11', ?, ?,
            TIMESTAMP '2026-05-11 21:00:00', TIMESTAMP '2026-05-11 21:10:00'
        )
        """,
        [json.dumps(metrics), json.dumps(config)],
    )

    report = load_experiment_report(conn)
    experiments = report["experiments"]
    benchmarks = report["benchmarks"]

    assert experiments.iloc[0]["candidate_id"] == "lgb_balanced"
    assert experiments.iloc[0]["annual_return"] == 0.12
    assert experiments.iloc[0]["duration_seconds"] == 600
    assert experiments.iloc[0]["verdict"] == "可重点关注"
    assert set(benchmarks["benchmark_name"]) == {"000300", "MIXED_EQUAL"}
    assert report["summary"]["experiment_count"] == 1
    assert report["summary"]["succeeded_count"] == 1
    conn.close()


def test_add_rolling_ic_columns_creates_decay_windows():
    daily = pd.DataFrame({
        "metric_date": pd.date_range("2024-01-01", periods=65),
        "rank_ic": [0.01] * 30 + [0.03] * 35,
        "ic": [0.02] * 65,
    })

    out = add_rolling_ic_columns(daily)

    assert {"rank_ic_ma30", "rank_ic_ma60", "rank_ic_ma180", "ic_ma30", "ic_ma60", "ic_ma180"}.issubset(out.columns)
    assert out.iloc[-1]["rank_ic_ma30"] == pytest.approx(0.03)
    assert out.iloc[-1]["rank_ic_ma60"] > out.iloc[29]["rank_ic_ma60"]


def test_qlib_report_selects_production_friendly_grid_best():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO qlib_experiments (experiment_id, model_name, mode, status, metrics_json)
        VALUES ('E1', 'alpha158', 'walk_forward', 'SUCCEEDED', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO qlib_grid_results (
            grid_id, source_experiment_id, model_name, mode, top_n, holding_days,
            rebalance_freq, buffer_n, benchmark_name, annual_return, sharpe_ratio,
            max_drawdown, turnover, benchmark_return, excess_return
        )
        VALUES
          ('G1', 'E1', 'alpha158', 'walk_forward', 20, 1,
           'daily', 30, 'MIXED_EQUAL', 0.30, 0.70, -0.36, 190, 0.23, 0.07),
          ('G2', 'E1', 'alpha158', 'walk_forward', 50, 9,
           'monthly', 75, 'MIXED_EQUAL', 0.10, 0.43, -0.20, 11, 0.03, 0.07)
        """
    )

    report = load_experiment_report(conn)
    grid_best = report["grid_best"]

    assert len(grid_best) == 1
    assert grid_best.iloc[0]["top_n"] == 50
    assert grid_best.iloc[0]["rebalance_freq"] == "monthly"
    assert report["summary"]["best_grid"]["holding_days"] == 9
    conn.close()


def test_qlib_report_prefers_small_account_grid_best_when_available():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO qlib_experiments (experiment_id, model_name, mode, status, metrics_json)
        VALUES ('E1', 'alpha158', 'walk_forward', 'SUCCEEDED', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO qlib_grid_results (
            grid_id, source_experiment_id, model_name, mode, top_n, holding_days,
            rebalance_freq, buffer_n, benchmark_name, constraint_profile,
            account_capital, avg_selected_count, avg_cash_drag, max_actual_position_pct,
            annual_return, sharpe_ratio, max_drawdown, turnover, benchmark_return, excess_return
        )
        VALUES
          ('G_THEORY', 'E1', 'alpha158', 'walk_forward', 100, 10,
           'monthly', 150, 'MIXED_EQUAL', 'theoretical_equal_weight',
           NULL, NULL, NULL, NULL, 0.30, 0.90, -0.24, 8, 0.05, 0.25),
          ('G_300K', 'E1', 'alpha158', 'walk_forward', 50, 10,
           'monthly', 75, 'MIXED_EQUAL', 'small_account_300k',
           300000, 31.5, 0.09, 0.058, 0.16, 0.62, -0.18, 10, 0.05, 0.11)
        """
    )

    report = load_experiment_report(conn)
    grid_best = report["grid_best"]

    assert len(grid_best) == 1
    row = grid_best.iloc[0]
    assert row["constraint_profile"] == "small_account_300k"
    assert row["account_capital"] == 300000
    assert row["avg_selected_count"] == pytest.approx(31.5)
    conn.close()


def test_qlib_report_handles_empty_data():
    conn = _conn()
    report = load_experiment_report(conn)

    assert report["experiments"].empty
    assert report["benchmarks"].empty
    assert report["grid_best"].empty
    assert report["candidate_results"].empty
    assert report["summary"]["experiment_count"] == 0
    conn.close()


def test_qlib_report_json_parser_treats_nan_as_empty_dict():
    assert parse_json_dict(float("nan")) == {}
    assert parse_json_dict(None) == {}
    assert parse_json_dict('{"a": 1}') == {"a": 1}


def test_qlib_report_marks_final_selected_and_metric_winners():
    conn = _conn()
    rows = [
        (
            "E1",
            {
                "annual_return": 0.12,
                "sharpe_ratio": 0.7,
                "max_drawdown": -0.20,
                "turnover": 20,
                "ic_mean": 0.02,
                "icir": 0.35,
                "rank_ic_positive_rate": 0.60,
                "primary_benchmark": "MIXED_EQUAL",
                "excess_return": 0.06,
            },
        ),
        (
            "E2",
            {
                "annual_return": 0.18,
                "sharpe_ratio": 0.6,
                "max_drawdown": -0.30,
                "turnover": 9,
                "ic_mean": 0.01,
                "icir": 0.20,
                "rank_ic_positive_rate": 0.52,
                "primary_benchmark": "MIXED_EQUAL",
                "excess_return": 0.04,
            },
        ),
    ]
    for experiment_id, metrics in rows:
        conn.execute(
            """
            INSERT INTO qlib_experiments (
                experiment_id, model_name, model_version, mode, status, metrics_json
            )
            VALUES (?, 'alpha158', ?, 'walk_forward', 'SUCCEEDED', ?)
            """,
            [experiment_id, experiment_id.lower(), json.dumps(metrics)],
        )

    report = load_experiment_report(conn)
    experiments = report["experiments"].set_index("experiment_id")

    assert bool(experiments.loc["E1", "is_final_selected"])
    assert "主基准超额" in experiments.loc["E1", "winning_metrics"]
    assert "ICIR" in experiments.loc["E1", "winning_metrics"]
    assert "年化收益" in experiments.loc["E2", "winning_metrics"]
    assert "低换手" in experiments.loc["E2", "winning_metrics"]
    assert "最终选出" in experiments.loc["E1", "highlight_reason"]
    conn.close()


def test_qlib_report_marks_candidate_batch_selected_experiment():
    conn = _conn()
    metrics = {
        "annual_return": 0.13,
        "sharpe_ratio": 0.3,
        "max_drawdown": -0.4,
        "turnover": 190,
        "primary_benchmark": "MIXED_EQUAL",
        "excess_return": -0.09,
        "icir": 0.2,
    }
    config = {"candidate": {"batch_id": "B1", "candidate_id": "lgb_deep", "model_variant": "deep"}}
    conn.execute(
        """
        INSERT INTO qlib_experiments (
            experiment_id, model_name, model_version, mode, status, metrics_json, config_snapshot
        )
        VALUES ('E_DEEP', 'alpha158', 'v-deep', 'walk_forward', 'SUCCEEDED', ?, ?)
        """,
        [json.dumps(metrics), json.dumps(config)],
    )
    conn.execute(
        """
        INSERT INTO qlib_candidate_results (
            candidate_id, batch_id, experiment_id, model_name, model_family,
            model_variant, status, mode, best_benchmark, best_top_n,
            best_holding_days, best_rebalance_freq, turnover, excess_return,
            score
        )
        VALUES (
            'lgb_deep', 'B1', 'E_DEEP', 'alpha158', 'lgbm',
            'deep', 'SUCCEEDED', 'walk_forward', 'MIXED_EQUAL', 20,
            5, 'weekly', 44.5, 0.46, 0.52
        )
        """
    )

    report = load_experiment_report(conn)
    row = report["experiments"].set_index("experiment_id").loc["E_DEEP"]

    assert bool(row["is_candidate_selected"])
    assert "候选批跑最终选出" in row["highlight_reason"]
    assert report["summary"]["best_candidate"]["experiment_id"] == "E_DEEP"
    conn.close()


def test_qlib_report_exposes_highlight_standards_and_metric_glossary():
    standards = get_metric_highlight_standards()
    glossary = get_metric_glossary()

    assert any(item["metric"] == "excess_return" and item["winner_rule"] == "越高越好" for item in standards)
    assert any(item["metric"] == "turnover" and item["winner_rule"] == "越低越好" for item in standards)
    assert any(item["metric"] == "icir" and "稳定" in item["plain_explanation"] for item in glossary)
    assert any(item["metric"] == "max_drawdown" and "跌" in item["plain_explanation"] for item in glossary)
