from datetime import date

import duckdb
import pandas as pd
import pytest

from src.data_pipeline.fetchers.akshare_fetcher import normalize_cn_financial_abstract
from src.data_pipeline.financials_backfill import backfill_cn_financials, select_cn_financial_symbols
from src.data_pipeline.loader import init_db, upsert_financials


def test_normalize_cn_financial_abstract_maps_akshare_wide_table_to_schema_rows():
    raw = pd.DataFrame([
        {"选项": "常用指标", "指标": "营业总收入", "20251231": "13,144,200,000", "20250930": "10"},
        {"选项": "常用指标", "指标": "归母净利润", "20251231": "4,263,300,000", "20250930": "2"},
        {"选项": "常用指标", "指标": "股东权益合计(净资产)", "20251231": "52000000000", "20250930": "40"},
        {"选项": "常用指标", "指标": "经营现金流量净额", "20251231": "9000000000", "20250930": "6"},
        {"选项": "常用指标", "指标": "基本每股收益", "20251231": "2.20", "20250930": "1.00"},
        {"选项": "常用指标", "指标": "每股净资产", "20251231": "21.8", "20250930": "20"},
        {"选项": "常用指标", "指标": "净资产收益率(ROE)", "20251231": "8.5", "20250930": "7.1"},
        {"选项": "常用指标", "指标": "总资产报酬率(ROA)", "20251231": "0.8", "20250930": "0.7"},
        {"选项": "常用指标", "指标": "毛利率", "20251231": "32.5%", "20250930": "31%"},
        {"选项": "常用指标", "指标": "销售净利率", "20251231": "12.5", "20250930": "11"},
        {"选项": "常用指标", "指标": "资产负债率", "20251231": "91.0", "20250930": "90"},
    ])

    out = normalize_cn_financial_abstract("1", raw)

    assert out.columns.tolist() == [
        "symbol",
        "report_date",
        "revenue",
        "net_profit",
        "total_assets",
        "total_equity",
        "operating_cf",
        "roe",
        "roa",
        "gross_margin",
        "net_margin",
        "debt_ratio",
        "eps",
        "bvps",
    ]
    latest = out[out["report_date"] == date(2025, 12, 31)].iloc[0]
    assert latest["symbol"] == "000001"
    assert latest["revenue"] == pytest.approx(131.442)
    assert latest["net_profit"] == pytest.approx(42.633)
    assert latest["total_equity"] == pytest.approx(520.0)
    assert latest["total_assets"] == pytest.approx(5777.777777777777)
    assert latest["operating_cf"] == pytest.approx(90.0)
    assert latest["roe"] == pytest.approx(8.5)
    assert latest["gross_margin"] == pytest.approx(32.5)
    assert latest["eps"] == pytest.approx(2.2)
    assert latest["bvps"] == pytest.approx(21.8)


def test_upsert_financials_accepts_normalized_columns_without_updated_at():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    df = pd.DataFrame([{
        "symbol": "000001",
        "report_date": date(2025, 12, 31),
        "revenue": 100.0,
        "net_profit": 10.0,
        "total_assets": 500.0,
        "total_equity": 200.0,
        "operating_cf": 12.0,
        "roe": 8.0,
        "roa": 3.0,
        "gross_margin": 30.0,
        "net_margin": 10.0,
        "debt_ratio": 60.0,
        "eps": 1.0,
        "bvps": 5.0,
    }])

    inserted = upsert_financials(conn, df)
    row = conn.execute("""
        SELECT symbol, report_date, revenue, roe, updated_at IS NOT NULL
        FROM financials
        WHERE symbol = '000001'
    """).fetchone()

    assert inserted == 1
    assert row == ("000001", date(2025, 12, 31), 100.0, 8.0, True)
    conn.close()


def test_select_cn_financial_symbols_can_limit_to_symbols_with_price_history():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', 'A'), ('000002', 'CN', 'B'), ('00700', 'HK', '腾讯')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, close)
        VALUES ('000002', DATE '2026-05-15', 10)
    """)

    assert select_cn_financial_symbols(conn) == ["000001", "000002"]
    assert select_cn_financial_symbols(conn, priced_only=True) == ["000002"]
    conn.close()


def test_backfill_cn_financials_fetches_selected_symbols_and_skips_existing_without_force():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', 'A'), ('000002', 'CN', 'B'), ('00700', 'HK', '腾讯')
    """)
    upsert_financials(conn, pd.DataFrame([{
        "symbol": "000001",
        "report_date": date(2025, 12, 31),
        "revenue": 100.0,
        "net_profit": 10.0,
        "total_assets": None,
        "total_equity": None,
        "operating_cf": None,
        "roe": 8.0,
        "roa": None,
        "gross_margin": None,
        "net_margin": 10.0,
        "debt_ratio": 60.0,
        "eps": None,
        "bvps": None,
    }]))
    calls: list[str] = []

    def fake_fetch(symbol: str) -> pd.DataFrame:
        calls.append(symbol)
        return pd.DataFrame([{
            "symbol": symbol,
            "report_date": date(2025, 12, 31),
            "revenue": 200.0,
            "net_profit": 20.0,
            "total_assets": 500.0,
            "total_equity": 300.0,
            "operating_cf": 30.0,
            "roe": 9.0,
            "roa": 4.0,
            "gross_margin": 35.0,
            "net_margin": 11.0,
            "debt_ratio": 40.0,
            "eps": 1.2,
            "bvps": 6.0,
        }])

    result = backfill_cn_financials(conn, fetch_financials=fake_fetch)

    assert calls == ["000002"]
    assert result["selected_symbols"] == 2
    assert result["skipped_existing"] == 1
    assert result["attempted"] == 1
    assert result["inserted_rows"] == 1
    assert conn.execute("SELECT COUNT(*) FROM financials").fetchone()[0] == 2
    conn.close()
