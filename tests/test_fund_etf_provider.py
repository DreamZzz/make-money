"""F1: ETF 分类与持久化测试(不依赖网络)。"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from src.data_pipeline.fund_etf_provider import (
    EtfRow,
    classify,
    persist_nav_batch,
    persist_universe,
)
from src.data_pipeline.loader import init_db


@pytest.mark.parametrize("name, expect_sub, expect_idx", [
    ("沪深300ETF华泰柏瑞", "broad", "000300"),
    ("中证500ETF易方达", "broad", "000905"),
    ("创业板ETF", "broad", "399006"),
    ("科创50ETF", "broad", "000688"),
    ("纳斯达克ETF", "qdii", "NDX"),
    ("恒生科技ETF", "qdii", "HSTECH"),
    ("黄金ETF华安", "commodity", ""),
    ("白银ETF", "commodity", ""),
    ("银华日利ETF", "money", ""),       # 货币 - 排除
    ("华宝添益ETF", "money", ""),
    ("国债ETF", "bond", ""),
    ("医药ETF", "sector", ""),
    ("半导体ETF", "sector", ""),
    ("新能源ETF", "sector", ""),
    ("汽车ETF", "sector", ""),
    ("某神秘 ETF", "other", ""),
])
def test_classify(name, expect_sub, expect_idx):
    sub, idx = classify(name)
    assert sub == expect_sub
    assert idx == expect_idx


def test_persist_universe_preserves_manual_funds():
    """已有 data_source='manual' 的 3 支配置不应被 etf_scan 覆盖。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    # 模拟 5-7 用户配的 3 支(data_source NULL = manual)
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('012963','招商稳健平衡混合','OPEN','000300','CN','CNY',TRUE)"
    )
    conn.execute(
        "INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency, enabled) "
        "VALUES ('510300','华泰柏瑞300','ETF','000300','CN','CNY',TRUE)"
    )
    # 扫描到 510300 (与 manual 重名) + 159001 (新)
    etfs = [
        EtfRow(fund_code="510300", name="覆盖名", scale_yi=1376.9, iopv=None,
               subcategory="broad", tracking_index="000300"),
        EtfRow(fund_code="159001", name="货币ETF应排除前已过滤", scale_yi=100,
               iopv=None, subcategory="broad", tracking_index="000300"),
    ]
    persist_universe(conn, etfs)
    # 510300 不应被覆盖(manual 保留),159001 应插入
    rows = conn.execute(
        "SELECT fund_code, name, data_source FROM fund_info ORDER BY fund_code"
    ).fetchall()
    by_code = {r[0]: r for r in rows}
    assert by_code["510300"][1] == "华泰柏瑞300"   # 原 manual 名保留
    assert by_code["159001"][1] == "货币ETF应排除前已过滤"
    assert by_code["159001"][2] == "etf_scan"
    assert by_code["012963"][1] == "招商稳健平衡混合"   # 完全未动


def test_persist_nav_batch_upserts():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    df = pd.DataFrame([
        {"fund_code": "510300", "trade_date": "2026-05-28", "nav": 4.93},
        {"fund_code": "510300", "trade_date": "2026-05-29", "nav": 4.92},
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    n = persist_nav_batch(conn, df)
    assert n == 2
    rows = conn.execute(
        "SELECT trade_date, nav FROM fund_nav WHERE fund_code='510300' ORDER BY trade_date"
    ).fetchall()
    assert len(rows) == 2
    # 重复 INSERT replace
    df2 = df.copy()
    df2["nav"] = [9.99, 9.88]
    persist_nav_batch(conn, df2)
    rows = conn.execute(
        "SELECT nav FROM fund_nav WHERE fund_code='510300' ORDER BY trade_date"
    ).fetchall()
    assert rows == [(9.99,), (9.88,)]
