"""R2: 财报披露日历构建。

每日刷新未来 30 天 + 历史 7 天的 earnings_calendar 表;
universe 内的标的才落表。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.financials.universe import load_earnings_universe


def _periods_to_check(today: date) -> list[str]:
    """根据当前日期生成需要查询的中文 period 列表。"""
    year = today.year
    periods = [f"{year}一季报", f"{year}中报", f"{year}三季报", f"{year}年报"]
    # 上一年年报可能在 4 月之前还在披露
    if today.month <= 5:
        periods.insert(0, f"{year - 1}年报")
    return periods


def build_earnings_calendar(
    conn: duckdb.DuckDBPyConnection,
    *,
    days_ahead: int = 30,
    days_back: int = 7,
    today: date | None = None,
) -> int:
    """拉 akshare 披露日历 → universe 过滤 → upsert earnings_calendar。

    返回写入行数。
    """
    today = today or date.today()
    universe_set = set(load_earnings_universe(conn))
    if not universe_set:
        logger.warning("earnings universe 空,跳过 calendar 构建")
        return 0

    from src.data_pipeline.fetchers.akshare_fetcher import fetch_cn_earnings_disclosure_calendar
    all_rows: list[pd.DataFrame] = []
    for period in _periods_to_check(today):
        try:
            df = fetch_cn_earnings_disclosure_calendar(period)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"calendar {period} failed: {exc}")
            continue
        if df.empty:
            continue
        all_rows.append(df)
    if not all_rows:
        logger.warning("akshare 披露日历无数据返回")
        return 0
    big = pd.concat(all_rows, ignore_index=True)

    # universe + 日期窗口过滤
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=days_ahead)
    big = big[
        big["symbol"].astype(str).isin(universe_set)
        & (big["disclosure_date"] >= start)
        & (big["disclosure_date"] <= end)
    ]
    if big.empty:
        return 0

    # 标 universe 字段
    big["universe"] = big["symbol"].map(lambda s: _universe_tag(s, universe_set))
    big["status"] = big["disclosure_date"].map(lambda d: "DISCLOSED" if d < today else "EXPECTED")
    big["updated_at"] = datetime.now()

    # 落表(INSERT OR REPLACE)
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_cal AS SELECT * FROM big")  # noqa: F841
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_calendar (
            symbol, report_period, disclosure_date, disclosure_type,
            status, universe, source, updated_at
        )
        SELECT symbol, report_period, disclosure_date, disclosure_type,
               status, universe, source, updated_at
        FROM _tmp_cal
        """
    )
    return len(big)


def _universe_tag(symbol: str, universe_set: set[str]) -> str:
    """简化:港股带 .HK 返回 HSTECH,其它 CSI300_500。"""
    if symbol.endswith(".HK"):
        return "HSTECH"
    return "CSI300_500"


def load_upcoming_calendar(
    conn: duckdb.DuckDBPyConnection, days_ahead: int = 7,
) -> list[dict[str, Any]]:
    """供 Dashboard 用:未来 N 天披露日历 + 股票名 + 行业。"""
    rows = conn.execute(
        f"""
        SELECT ec.symbol, COALESCE(si.name, ec.symbol) AS name,
               COALESCE(si.industry, '-') AS industry,
               ec.disclosure_date, ec.disclosure_type, ec.universe
        FROM earnings_calendar ec
        LEFT JOIN stock_info si ON si.symbol = ec.symbol
        WHERE ec.disclosure_date >= CURRENT_DATE
          AND ec.disclosure_date <= CURRENT_DATE + INTERVAL '{int(days_ahead)} days'
          AND ec.status = 'EXPECTED'
        ORDER BY ec.disclosure_date, ec.universe, ec.symbol
        """
    ).fetchdf()
    return rows.to_dict(orient="records") if not rows.empty else []
