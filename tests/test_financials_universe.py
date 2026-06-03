"""R1: financials universe 测试。"""
from __future__ import annotations

import duckdb

from src.data_pipeline.loader import init_db
from src.financials.hstech_constituents import get_hstech_metadata, get_hstech_symbols
from src.financials.universe import load_earnings_universe, universe_size_breakdown


def test_hstech_symbols_unique_and_non_empty():
    symbols = get_hstech_symbols()
    assert len(symbols) > 20  # 至少 20+ 个去重后
    assert len(symbols) == len(set(symbols))  # 已去重
    assert all(".HK" in s for s in symbols)  # 港股格式


def test_hstech_metadata_dict_shape():
    meta = get_hstech_metadata()
    assert "0700.HK" in meta
    assert meta["0700.HK"]["name"] == "腾讯控股"
    assert "科技" in meta["0700.HK"]["industry"]


def test_load_earnings_universe_includes_hstech_and_csi():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    # seed CSI300 一支 + CSI500 一支
    for code, sym in [("000300", "600519"), ("000905", "000001")]:
        conn.execute(
            "INSERT INTO index_member_history (index_code, symbol, start_date) "
            "VALUES (?, ?, DATE '2024-01-01')",
            [code, sym],
        )
        conn.execute(
            "INSERT INTO stock_info (symbol, country, name) VALUES (?, 'CN', ?)",
            [sym, sym],
        )
    universe = load_earnings_universe(conn)
    assert "600519" in universe
    assert "000001" in universe
    assert "0700.HK" in universe  # HSTECH 硬编码
    bd = universe_size_breakdown(conn)
    assert bd["csi300_500"] == 2
    assert bd["hstech"] > 20
    assert bd["total"] == bd["csi300_500"] + bd["hstech"]


def test_load_earnings_universe_excludes_expired_members():
    """已退出指数 (end_date 在 31 天前) 不进 universe。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO index_member_history (index_code, symbol, start_date, end_date) "
        "VALUES ('000300', '999999', DATE '2024-01-01', DATE '2024-06-01')"
    )
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('999999','CN','x')")
    universe = load_earnings_universe(conn)
    assert "999999" not in universe
