import json

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.research import alpha_validation as av
from src.research.alpha_validation import (
    build_candidate_scores,
    load_research_price_panel,
    run_research_candidate_grid,
    run_research_candidate_validation,
    run_score_panel_validation,
    score_panel_to_predictions,
)


def _scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_date": "2026-01-01", "symbol": "000001", "score": 0.90},
            {"trade_date": "2026-01-01", "symbol": "000002", "score": 0.20},
            {"trade_date": "2026-01-02", "symbol": "000001", "score": 0.85},
            {"trade_date": "2026-01-02", "symbol": "000002", "score": 0.30},
        ],
    )


def _prices() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    opens = {
        "000001": [10.0, 10.0, 11.0, 12.0, 13.0, 14.0],
        "000002": [10.0, 10.0, 9.8, 9.6, 9.4, 9.2],
    }
    for symbol, values in opens.items():
        for idx, trade_date in enumerate(dates):
            open_price = values[idx]
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "open": open_price,
                    "close": open_price,
                    "pre_close": values[idx - 1] if idx else open_price,
                },
            )
    return pd.DataFrame(rows)


def test_score_panel_to_predictions_maps_required_columns():
    pred = score_panel_to_predictions(_scores())

    assert pred.columns.tolist() == ["datetime", "instrument", "score"]
    assert pred["datetime"].tolist() == [
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-02"),
        pd.Timestamp("2026-01-02"),
    ]
    assert pred["instrument"].tolist() == ["000001", "000002", "000001", "000002"]


def test_run_score_panel_validation_outputs_gate_metrics():
    benchmark = pd.Series(
        [0.005, 0.005, 0.005, 0.005, 0.005],
        index=pd.date_range("2026-01-02", periods=5, freq="D"),
    )
    reference = pd.Series(
        [0.004, 0.006, 0.004, 0.006, 0.004],
        index=pd.date_range("2026-01-02", periods=5, freq="D"),
    )

    result = run_score_panel_validation(
        strategy_name="industry_relative_momentum",
        scores=_scores(),
        prices=_prices(),
        benchmark_returns=benchmark,
        reference_returns=reference,
        top_n=1,
        buffer_n=2,
        holding_days=1,
        rebalance_freq="daily",
        factor_coverage=0.95,
    )

    assert result["strategy_name"] == "industry_relative_momentum"
    assert result["decision_scope"] == "research_only"
    assert result["score_rows"] == 4
    assert result["return_periods"] > 0
    assert result["buffer_n"] == 2
    assert result["metrics"]["annual_return"] is not None
    assert set(result["alpha_gate_metrics"]) == {
        "information_ratio",
        "correlation_alpha158",
        "correlation_benchmark",
        "max_drawdown",
        "annual_turnover",
        "factor_coverage",
    }
    assert isinstance(result["alpha_gate_passed"], bool)
    assert isinstance(result["alpha_gate_failed_reasons"], list)


def test_run_score_panel_validation_handles_empty_scores_as_gate_failure():
    result = run_score_panel_validation(
        strategy_name="empty_candidate",
        scores=pd.DataFrame(columns=["trade_date", "symbol", "score"]),
        prices=_prices(),
    )

    assert result["return_periods"] == 0
    assert result["alpha_gate_passed"] is False
    assert any("information_ratio" in reason for reason in result["alpha_gate_failed_reasons"])


def test_run_score_panel_validation_supports_quarterly_rebalance():
    dates = pd.date_range("2026-01-01", periods=180, freq="D")
    prices = pd.concat(
        [
            pd.DataFrame(
                {
                    "trade_date": dates,
                    "symbol": symbol,
                    "open": [10.0 + idx * step for idx in range(len(dates))],
                    "close": [10.0 + idx * step for idx in range(len(dates))],
                    "pre_close": [10.0 + max(idx - 1, 0) * step for idx in range(len(dates))],
                }
            )
            for symbol, step in [("000001", 0.03), ("000002", -0.005)]
        ],
        ignore_index=True,
    )
    scores = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-31", periods=5, freq="ME"),
            "symbol": ["000001"] * 5,
            "score": [0.9] * 5,
        }
    )

    result = run_score_panel_validation(
        strategy_name="quarterly_candidate",
        scores=scores,
        prices=prices,
        top_n=1,
        holding_days=20,
        rebalance_freq="quarterly",
    )

    assert result["rebalance_freq"] == "quarterly"
    assert result["return_periods"] >= 1
    assert result["metrics"]["turnover"] is not None


def test_run_score_panel_validation_can_limit_monthly_replacements():
    dates = pd.date_range("2026-01-01", periods=140, freq="D")
    symbols = ["A", "B", "C", "D", "E", "F"]
    prices = pd.concat(
        [
            pd.DataFrame(
                {
                    "trade_date": dates,
                    "symbol": symbol,
                    "open": [10.0 + idx * 0.01 for idx in range(len(dates))],
                    "close": [10.0 + idx * 0.01 for idx in range(len(dates))],
                    "pre_close": [10.0 + max(idx - 1, 0) * 0.01 for idx in range(len(dates))],
                }
            )
            for symbol in symbols
        ],
        ignore_index=True,
    )
    score_rows = []
    monthly_leaders = [
        ("2026-01-31", ["A", "B"]),
        ("2026-02-28", ["C", "D"]),
        ("2026-03-31", ["E", "F"]),
    ]
    for trade_date, leaders in monthly_leaders:
        for symbol in symbols:
            score_rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "score": 0.9 if symbol in leaders else 0.1,
                }
            )
    scores = pd.DataFrame(score_rows)

    unconstrained = run_score_panel_validation(
        strategy_name="reversal",
        scores=scores,
        prices=prices,
        top_n=2,
        holding_days=5,
        rebalance_freq="monthly",
    )
    constrained = run_score_panel_validation(
        strategy_name="reversal",
        scores=scores,
        prices=prices,
        top_n=2,
        holding_days=5,
        rebalance_freq="monthly",
        max_replacements_per_rebalance=1,
    )

    assert constrained["metrics"]["turnover"] < unconstrained["metrics"]["turnover"]
    assert constrained["max_replacements_per_rebalance"] == 1


def test_build_candidate_scores_dispatches_industry_relative_momentum():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 4),
            "symbol": ["A"] * 3 + ["B"] * 3 + ["C"] * 3 + ["D"] * 3,
            "close": [10, 11, 12, 10, 10.5, 11, 10, 10.1, 10.2, 10, 10.1, 10.1],
            "industry": ["tech"] * 6 + ["utility"] * 6,
            "open": [10, 11, 12, 10, 10.5, 11, 10, 10.1, 10.2, 10, 10.1, 10.1],
        }
    )

    scores = build_candidate_scores(
        "industry_relative_momentum",
        prices,
        lookback=2,
        min_industry_members=2,
    )

    assert not scores.empty
    assert {"trade_date", "symbol", "score"}.issubset(scores.columns)


def test_build_candidate_scores_rejects_unknown_candidate():
    try:
        build_candidate_scores("unknown_candidate", _prices())
    except ValueError as exc:
        assert "Unsupported research alpha candidate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_research_price_panel_joins_industry_for_cn_symbols():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name, industry)
        VALUES ('000001', 'CN', 'A', '银行'), ('000002', 'CN', 'B', '科技')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, close, pre_close)
        VALUES
            ('000001', DATE '2026-01-01', 10, 10, 10),
            ('000002', DATE '2026-01-01', 20, 20, 20)
    """)

    panel = load_research_price_panel(conn, start="2026-01-01", end="2026-01-31")

    assert panel[["symbol", "industry", "open", "close"]].to_dict("records") == [
        {"symbol": "000001", "industry": "银行", "open": 10.0, "close": 10.0},
        {"symbol": "000002", "industry": "科技", "open": 20.0, "close": 20.0},
    ]
    conn.close()


def test_run_research_candidate_validation_uses_shared_pipeline(monkeypatch):
    calls = {}
    prices = _prices()
    scores = _scores()

    def fake_load(conn, *, start, end, country):
        calls["load"] = (conn, start, end, country)
        return prices

    def fake_build(candidate, loaded_prices, **kwargs):
        calls["build"] = (candidate, loaded_prices, kwargs)
        return scores

    def fake_validate(**kwargs):
        calls["validate"] = kwargs
        return {"strategy_name": kwargs["strategy_name"], "alpha_gate_passed": False}

    monkeypatch.setattr(av, "load_research_price_panel", fake_load)
    monkeypatch.setattr(av, "build_candidate_scores", fake_build)
    monkeypatch.setattr(av, "run_score_panel_validation", fake_validate)

    result = run_research_candidate_validation(
        object(),
        candidate="low_vol",
        start="2026-01-01",
        end="2026-01-31",
        benchmark_returns=pd.Series(dtype=float),
        reference_returns=pd.Series(dtype=float),
        score_kwargs={"lookback": 60},
        buffer_n=50,
    )

    assert result == {"strategy_name": "low_vol", "alpha_gate_passed": False}
    assert calls["load"][1:] == ("2026-01-01", "2026-01-31", "CN")
    assert calls["build"] == ("low_vol", prices, {"lookback": 60})
    assert calls["validate"]["scores"] is scores
    assert calls["validate"]["prices"] is prices
    assert calls["validate"]["strategy_name"] == "low_vol"
    assert calls["validate"]["top_n"] == 20
    assert calls["validate"]["buffer_n"] == 50


def test_run_research_candidate_grid_ranks_gate_pass_then_ir(monkeypatch):
    calls = []
    build_calls = []
    scores = _scores()
    prices = _prices()

    monkeypatch.setattr(av, "load_research_price_panel", lambda *args, **kwargs: prices)

    def fake_build(candidate, loaded_prices, **kwargs):
        build_calls.append((candidate, kwargs))
        return scores

    def fake_validate(**kwargs):
        calls.append(kwargs)
        lookback = kwargs["strategy_name"].split("_")[-1]
        top_n = kwargs["top_n"]
        passed = lookback == "60" and top_n == 20
        return {
            "strategy_name": kwargs["strategy_name"],
            "alpha_gate_passed": passed,
            "alpha_gate_failed_reasons": [] if passed else ["turnover 2.00 > 1.00"],
            "alpha_gate_metrics": {"information_ratio": 0.4 if passed else 0.8},
            "metrics": {"info_ratio": 0.4 if passed else 0.8},
            "top_n": top_n,
            "buffer_n": kwargs["buffer_n"],
            "holding_days": kwargs["holding_days"],
            "rebalance_freq": kwargs["rebalance_freq"],
        }

    monkeypatch.setattr(av, "build_candidate_scores", fake_build)
    monkeypatch.setattr(av, "run_score_panel_validation", fake_validate)

    results = run_research_candidate_grid(
        object(),
        candidate="cross_reversal",
        start="2026-01-01",
        end="2026-12-31",
        lookbacks=[20, 60],
        top_ns=[20, 50],
        smooth_days_list=[1],
        size_neutral_options=[False],
        beta_neutral_options=[False],
        buffer_ns=[None],
        holding_days_list=[20],
        rebalance_freqs=["monthly"],
    )

    assert len(calls) == 4
    assert len(build_calls) == 2
    assert results[0]["alpha_gate_passed"] is True
    assert results[0]["score_kwargs"] == {"lookback": 60, "smooth_days": 1}
    assert results[0]["top_n"] == 20


def test_run_research_candidate_grid_deduplicates_beta_disabled_baselines(monkeypatch):
    calls = []
    monkeypatch.setattr(av, "load_research_price_panel", lambda *args, **kwargs: _prices())
    monkeypatch.setattr(av, "build_candidate_scores", lambda *args, **kwargs: _scores())

    def fake_validate(**kwargs):
        calls.append(kwargs["strategy_name"])
        return {
            "strategy_name": kwargs["strategy_name"],
            "alpha_gate_passed": False,
            "alpha_gate_metrics": {"information_ratio": 0.1},
            "metrics": {"info_ratio": 0.1},
        }

    monkeypatch.setattr(av, "run_score_panel_validation", fake_validate)

    results = run_research_candidate_grid(
        object(),
        candidate="cross_reversal",
        start="2026-01-01",
        end="2026-12-31",
        lookbacks=[60],
        smooth_days_list=[1],
        size_neutral_options=[False],
        beta_neutral_options=[False, True],
        beta_lookbacks=[60, 120],
        top_ns=[20],
        buffer_ns=[500],
        max_replacements_list=[1],
        holding_days_list=[20],
        rebalance_freqs=["monthly"],
    )

    assert calls == [
        "cross_reversal_60",
        "cross_reversal_60_beta60",
        "cross_reversal_60_beta120",
    ]
    assert len(results) == 3


def test_validate_research_alpha_cli_prints_json(monkeypatch, capsys):
    from scripts import validate_research_alpha as cli

    class FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(cli, "get_connection", lambda read_only=True: FakeConn())
    monkeypatch.setattr(
        cli,
        "_load_benchmark_suite",
        lambda conn: {"MIXED_EQUAL": pd.Series([0.01], index=[pd.Timestamp("2026-01-31")])},
    )
    monkeypatch.setattr(
        cli,
        "load_latest_alpha158_portfolio_returns",
        lambda conn, start, end: (
            pd.Series([0.01], index=[pd.Timestamp("2026-01-31")]),
            "EXP-1",
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_research_candidate_validation",
        lambda **kwargs: {
            "strategy_name": kwargs["candidate"],
            "score_kwargs": kwargs["score_kwargs"],
            "alpha_gate_passed": True,
            "alpha_gate_failed_reasons": [],
            "metrics": {"info_ratio": 0.4},
        },
    )

    assert cli.main(
        [
            "--candidate",
            "low_vol",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
            "--lookback",
            "90",
            "--smooth-days",
            "5",
            "--size-neutral",
            "--beta-neutral",
            "--beta-lookback",
            "90",
            "--rebalance-freq",
            "quarterly",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy_name"] == "low_vol"
    assert payload["score_kwargs"] == {
        "lookback": 90,
        "smooth_days": 5,
        "size_neutral": True,
        "beta_neutral": True,
        "beta_lookback": 90,
    }
    assert payload["alpha_gate_passed"] is True
    assert payload["reference_experiment_id"] == "EXP-1"


def test_validate_research_alpha_cli_grid_mode_prints_ranked_results(monkeypatch, capsys):
    from scripts import validate_research_alpha as cli

    class FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(cli, "get_connection", lambda read_only=True: FakeConn())
    monkeypatch.setattr(cli, "_load_benchmark_suite", lambda conn: {"MIXED_EQUAL": pd.Series(dtype=float)})
    monkeypatch.setattr(
        cli,
        "load_latest_alpha158_portfolio_returns",
        lambda conn, start, end: (pd.Series(dtype=float), "EXP-2"),
    )
    monkeypatch.setattr(
        cli,
        "run_research_candidate_grid",
        lambda **kwargs: [
            {
                "strategy_name": kwargs["candidate"],
                "alpha_gate_passed": False,
                "score_kwargs": {"lookback": 60},
                "metrics": {"info_ratio": 0.2},
            }
        ],
    )

    assert cli.main(
        [
            "--candidate",
            "cross_reversal",
            "--start",
            "2026-01-01",
            "--end",
            "2026-12-31",
            "--grid",
            "--lookbacks",
            "60",
            "--top-ns",
            "20",
            "--smooth-days-list",
            "1,5",
            "--buffer-ns",
            "none,160",
            "--rebalance-freqs",
            "monthly,quarterly",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate"] == "cross_reversal"
    assert payload["reference_experiment_id"] == "EXP-2"
    assert payload["results"][0]["score_kwargs"] == {"lookback": 60}
