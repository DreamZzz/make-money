from datetime import date

import pandas as pd

from src.backtest.survivorship_impact import (
    filter_predictions_by_point_in_time_universe,
    filter_predictions_by_static_universe,
    format_survivorship_report,
    summarize_prediction_filter,
)


def _predictions() -> pd.DataFrame:
    return pd.DataFrame({
        "datetime": pd.to_datetime([
            "2021-01-15",
            "2021-01-15",
            "2021-03-15",
            "2021-03-15",
            "2021-03-15",
        ]),
        "instrument": ["000001", "000002", "000001", "000002", "000003"],
        "score": [0.7, 0.9, 0.8, 0.4, 0.6],
    })


def _membership() -> pd.DataFrame:
    return pd.DataFrame({
        "index_code": ["000300", "000300", "000905"],
        "symbol": ["000001", "000002", "000003"],
        "start_date": [date(2020, 1, 1), date(2021, 1, 1), date(2021, 2, 1)],
        "end_date": [None, date(2021, 2, 28), None],
        "source": ["sample", "sample", "sample"],
    })


def test_static_universe_uses_active_members_at_as_of_date():
    filtered = filter_predictions_by_static_universe(
        _predictions(),
        _membership(),
        index_codes=("000300", "000905"),
        as_of=date(2021, 3, 31),
    )

    assert filtered["instrument"].tolist() == ["000001", "000001", "000003"]


def test_point_in_time_universe_respects_prediction_date_ranges():
    filtered = filter_predictions_by_point_in_time_universe(
        _predictions(),
        _membership(),
        index_codes=("000300", "000905"),
    )

    # 按列分别取值比较，避免 to_records().tolist() 在某些 numpy 版本下把
    # datetime64[ns] 转成 int 纳秒导致的脆弱断言。
    rows = list(zip(
        [pd.Timestamp(ts).date().isoformat() for ts in filtered["datetime"].tolist()],
        filtered["instrument"].tolist(),
        strict=True,
    ))
    assert rows == [
        ("2021-01-15", "000001"),
        ("2021-01-15", "000002"),
        ("2021-03-15", "000001"),
        ("2021-03-15", "000003"),
    ]


def test_summarize_prediction_filter_reports_candidate_loss():
    summary = summarize_prediction_filter(
        original=_predictions(),
        static_filtered=filter_predictions_by_static_universe(
            _predictions(),
            _membership(),
            index_codes=("000300", "000905"),
            as_of=date(2021, 3, 31),
        ),
        pit_filtered=filter_predictions_by_point_in_time_universe(
            _predictions(),
            _membership(),
            index_codes=("000300", "000905"),
        ),
    )

    assert summary["original_rows"] == 5
    assert summary["static_rows"] == 3
    assert summary["point_in_time_rows"] == 4
    assert summary["static_avg_candidates_per_date"] == 1.5
    assert summary["point_in_time_avg_candidates_per_date"] == 2.0


def test_report_mentions_monthly_snapshot_limitation_and_bias_direction():
    report = format_survivorship_report(
        experiment={
            "experiment_id": "EXP-1",
            "model_version": "alpha158-test",
            "start": "2021-01-01",
            "end": "2021-12-31",
            "top_n": 15,
            "holding_days": 15,
            "rebalance_freq": "monthly",
            "buffer_n": 23,
        },
        filter_summary={"original_rows": 100, "static_rows": 80, "point_in_time_rows": 70},
        static_metrics={"annual_return": 0.12, "excess_return": 0.04, "sharpe_ratio": 1.1, "max_drawdown": -0.08},
        pit_metrics={"annual_return": 0.09, "excess_return": 0.01, "sharpe_ratio": 0.8, "max_drawdown": -0.1},
    )

    assert "Baostock 月度快照" in report
    assert "静态池乐观偏差" in report
    assert "3.00 pp" in report
