import pandas as pd

from src.backtest.comparator import compare_results


def test_compare_results_excludes_research_only_runs_by_default():
    results = pd.DataFrame([
        {
            "strategy_name": "alpha158",
            "annual_return": 0.12,
            "sharpe_ratio": 1.1,
            "max_drawdown": -0.08,
            "turnover": 4.0,
            "excess_return": 0.03,
            "decision_scope": "decision",
        },
        {
            "strategy_name": "momentum_topn",
            "annual_return": 0.50,
            "sharpe_ratio": 3.0,
            "max_drawdown": -0.05,
            "turnover": 20.0,
            "excess_return": 0.20,
            "decision_scope": "research_only",
        },
    ])

    summary = compare_results(results)
    research_summary = compare_results(results, include_research=True)

    assert summary.index.tolist() == ["alpha158"]
    assert "momentum_topn" in research_summary.index
