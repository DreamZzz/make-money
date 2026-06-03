"""R1: 财报分析候选池。

CSI300 (000300) + CSI500 (000905) + HSTECH 恒生科技。
CSI1000 (000852) 暂未在 index_member_history,留待 R7 补;现阶段 CSI500 替代覆盖中盘。

返回的 universe 用于:
- earnings_calendar 拉取过滤
- earnings_alerts 事件流过滤
- value_quality 信号生成范围
"""
from __future__ import annotations

import duckdb

CN_INDEX_CODES = ("000300", "000905")    # CSI300 + CSI500


def load_earnings_universe(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """返回候选池所有 symbol(A 股 + 港股 HSTECH)。"""
    cn_symbols = _load_cn_universe(conn)
    hk_symbols = _load_hk_universe(conn)
    return sorted(set(cn_symbols) | set(hk_symbols))


def _load_cn_universe(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """从 index_member_history 取 CSI300 + CSI500 现役成分。"""
    placeholders = ", ".join("?" for _ in CN_INDEX_CODES)
    rows = conn.execute(
        f"""
        SELECT DISTINCT imh.symbol
        FROM index_member_history imh
        LEFT JOIN stock_info si ON si.symbol = imh.symbol
        WHERE imh.index_code IN ({placeholders})
          AND (imh.end_date IS NULL OR imh.end_date > CURRENT_DATE - INTERVAL '30 days')
          AND COALESCE(si.country, 'CN') = 'CN'
        """,
        list(CN_INDEX_CODES),
    ).fetchall()
    return [r[0] for r in rows]


def _load_hk_universe(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """HSTECH 30 个成分股(硬编码)。"""
    from src.financials.hstech_constituents import get_hstech_symbols
    return get_hstech_symbols()


def universe_size_breakdown(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """按子池返回数量,用于 Dashboard coverage 展示。"""
    cn = _load_cn_universe(conn)
    hk = _load_hk_universe(conn)
    return {
        "csi300_500": len(cn),
        "hstech": len(hk),
        "total": len(set(cn) | set(hk)),
    }
