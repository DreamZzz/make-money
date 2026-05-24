"""回灌全A股估值历史（PE-TTM / PB 中位数 + 历史分位），判断"市场贵不贵"。

数据源：akshare 乐咕乐股 `stock_a_ttm_lyr`（PE）+ `stock_a_all_pb`（PB），
两条全市场单序列（2005 至今），自带近10年/全历史分位列。比逐股回灌稳健得多。
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

PE_COLS = {
    "date": "trade_date",
    "middlePETTM": "pe_ttm_median",
    "quantileInRecent10YearsMiddlePeTtm": "pe_ttm_pct_10y",
    "quantileInAllHistoryMiddlePeTtm": "pe_ttm_pct_all",
    "close": "close",
}
PB_COLS = {
    "date": "trade_date",
    "middlePB": "pb_median",
    "quantileInRecent10YearsMiddlePB": "pb_pct_10y",
    "quantileInAllHistoryMiddlePB": "pb_pct_all",
}


def build_valuation_frame(pe_df: pd.DataFrame, pb_df: pd.DataFrame) -> pd.DataFrame:
    """把 akshare 的 PE / PB 序列规整并按日期合并为 market_valuation 行。"""
    pe = pe_df[[c for c in PE_COLS if c in pe_df.columns]].rename(columns=PE_COLS)
    pb = pb_df[[c for c in PB_COLS if c in pb_df.columns]].rename(columns=PB_COLS)
    pe["trade_date"] = pd.to_datetime(pe["trade_date"]).dt.date
    pb["trade_date"] = pd.to_datetime(pb["trade_date"]).dt.date
    merged = pe.merge(pb, on="trade_date", how="outer").sort_values("trade_date")
    for col in ["pe_ttm_median", "pe_ttm_pct_10y", "pe_ttm_pct_all",
                "pb_median", "pb_pct_10y", "pb_pct_all", "close"]:
        if col not in merged.columns:
            merged[col] = None
        else:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    merged["source"] = "akshare_lg"
    # 源数据偶有重复日期，按日期去重保留最后一条
    merged = merged.drop_duplicates(subset="trade_date", keep="last")
    return merged.sort_values("trade_date").reset_index(drop=True)


def _default_fetchers() -> tuple[Callable[[], pd.DataFrame], Callable[[], pd.DataFrame]]:
    import akshare as ak

    return ak.stock_a_ttm_lyr, ak.stock_a_all_pb


def backfill_market_valuation(
    conn: duckdb.DuckDBPyConnection,
    fetchers: tuple[Callable[[], pd.DataFrame], Callable[[], pd.DataFrame]] | None = None,
) -> dict[str, Any]:
    """拉取并全量刷新 market_valuation 表。"""
    pe_fn, pb_fn = fetchers or _default_fetchers()
    try:
        pe_df, pb_df = pe_fn(), pb_fn()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"估值源拉取失败: {exc}")
        return {"status": "FAILED", "reason": str(exc), "rows": 0}

    frame = build_valuation_frame(pe_df, pb_df)
    if frame.empty:
        return {"status": "EMPTY", "rows": 0}

    conn.register("_val_df", frame)
    conn.execute("DELETE FROM market_valuation")
    conn.execute(
        """
        INSERT INTO market_valuation (
            trade_date, pe_ttm_median, pe_ttm_pct_10y, pe_ttm_pct_all,
            pb_median, pb_pct_10y, pb_pct_all, close, source
        )
        SELECT trade_date, pe_ttm_median, pe_ttm_pct_10y, pe_ttm_pct_all,
               pb_median, pb_pct_10y, pb_pct_all, close, source
        FROM _val_df
        """
    )
    conn.unregister("_val_df")
    latest = conn.execute(
        "SELECT trade_date, pe_ttm_pct_10y, pb_pct_10y FROM market_valuation ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    logger.info(f"market_valuation 刷新 {len(frame)} 行；最新 {latest}")
    return {"status": "OK", "rows": len(frame), "latest": latest}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="回灌全A股估值历史").parse_args(argv)
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        result = backfill_market_valuation(conn)
        logger.info(f"估值回灌结果: {result}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
