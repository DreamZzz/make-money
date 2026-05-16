from datetime import date

import pandas as pd

from scripts.fetch_index_membership_baostock import (
    baostock_result_to_frame,
    build_monthly_snapshot_dates,
    normalize_baostock_snapshot,
    snapshots_to_membership_ranges,
)


class FakeBaoStockResult:
    def __init__(self, rows, fields=None, error_code="0", error_msg="success"):
        self.rows = list(rows)
        self.fields = fields or ["updateDate", "code", "code_name"]
        self.error_code = error_code
        self.error_msg = error_msg
        self._idx = -1

    def next(self):
        self._idx += 1
        return self._idx < len(self.rows)

    def get_row_data(self):
        return self.rows[self._idx]


def test_build_monthly_snapshot_dates_includes_month_ends_and_final_date():
    dates = build_monthly_snapshot_dates("2020-01-15", "2020-03-10")

    assert dates == [
        date(2020, 1, 31),
        date(2020, 2, 29),
        date(2020, 3, 10),
    ]


def test_normalize_baostock_snapshot_strips_exchange_prefix_and_keeps_snapshot_date():
    raw = pd.DataFrame({
        "code": ["sh.600000", "sz.000001", "bj.430047"],
        "code_name": ["浦发银行", "平安银行", "测试北交"],
    })

    out = normalize_baostock_snapshot("000300", date(2020, 1, 31), raw, source="baostock_test")

    assert out.to_records(index=False).tolist() == [
        ("000300", "600000", date(2020, 1, 31), "baostock_test"),
        ("000300", "000001", date(2020, 1, 31), "baostock_test"),
        ("000300", "430047", date(2020, 1, 31), "baostock_test"),
    ]


def test_snapshots_to_membership_ranges_closes_removed_members_and_reopens_later():
    snapshots = pd.DataFrame({
        "index_code": ["000300", "000300", "000300", "000300"],
        "symbol": ["000001", "000002", "000002", "000001"],
        "snapshot_date": [
            date(2020, 1, 31),
            date(2020, 1, 31),
            date(2020, 2, 29),
            date(2020, 3, 31),
        ],
        "source": ["baostock_test"] * 4,
    })

    out = snapshots_to_membership_ranges(snapshots)

    assert out.to_records(index=False).tolist() == [
        ("000300", "000001", date(2020, 1, 31), date(2020, 2, 28), "baostock_test"),
        ("000300", "000001", date(2020, 3, 31), None, "baostock_test"),
        ("000300", "000002", date(2020, 1, 31), date(2020, 3, 30), "baostock_test"),
    ]


def test_baostock_result_to_frame_handles_iterator_style_result():
    result = FakeBaoStockResult([
        ["2020-01-31", "sh.600000", "浦发银行"],
        ["2020-01-31", "sz.000001", "平安银行"],
    ])

    df = baostock_result_to_frame(result)

    assert df.to_dict("records") == [
        {"updateDate": "2020-01-31", "code": "sh.600000", "code_name": "浦发银行"},
        {"updateDate": "2020-01-31", "code": "sz.000001", "code_name": "平安银行"},
    ]
