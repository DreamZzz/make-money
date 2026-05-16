from __future__ import annotations

import pandas as pd

from src.dashboard.weekly_report_service import build_weekly_operation_summary


def test_weekly_operation_summary_counts_cash_need_and_manual_time():
    plan = pd.DataFrame({
        "action": ["买入", "清仓", "候选"],
        "executable": [True, True, False],
        "order_value": [10_000.0, -4_000.0, 0.0],
        "estimated_fee": [15.0, 6.0, 0.0],
        "funding_gap": [0.0, 0.0, 8_000.0],
    })

    summary = build_weekly_operation_summary(plan, minutes_per_operation=4)

    assert summary["operation_count"] == 2
    assert summary["required_cash"] == 10_015.0
    assert summary["released_cash"] == 3_994.0
    assert summary["candidate_count"] == 1
    assert summary["one_lot_funding_gap"] == 8_000.0
    assert summary["estimated_minutes"] == 8
