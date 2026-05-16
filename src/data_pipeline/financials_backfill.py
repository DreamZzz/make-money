"""Free-source CN financials backfill helpers."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pandas as pd
from loguru import logger


def select_cn_financial_symbols(
    conn: Any,
    symbols: list[str] | None = None,
    limit: int | None = None,
    country: str = "CN",
    priced_only: bool = False,
) -> list[str]:
    """Return normalized A-share symbols to backfill."""
    if symbols:
        selected = [_normalize_cn_symbol(symbol) for symbol in symbols if str(symbol).strip()]
    elif priced_only:
        selected = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT si.symbol
                FROM stock_info si
                JOIN daily_price dp ON si.symbol = dp.symbol
                WHERE si.country = ?
                ORDER BY si.symbol
                """,
                [country],
            ).fetchall()
        ]
    else:
        selected = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT symbol
                FROM stock_info
                WHERE country = ?
                ORDER BY symbol
                """,
                [country],
            ).fetchall()
        ]
    if limit is not None:
        selected = selected[: max(int(limit), 0)]
    return selected


def backfill_cn_financials(
    conn: Any,
    symbols: list[str] | None = None,
    limit: int | None = None,
    fetch_financials: Callable[[str], pd.DataFrame] | None = None,
    force: bool = False,
    sleep_seconds: float = 0.0,
    country: str = "CN",
    priced_only: bool = False,
) -> dict[str, Any]:
    """Fetch and upsert free AkShare CN financial snapshots for selected symbols."""
    from src.data_pipeline.fetchers import akshare_fetcher as ak
    from src.data_pipeline.loader import upsert_financials

    fetch_financials = fetch_financials or ak.fetch_cn_financials
    selected = select_cn_financial_symbols(
        conn,
        symbols=symbols,
        limit=limit,
        country=country,
        priced_only=priced_only,
    )
    result: dict[str, Any] = {
        "status": "OK",
        "selected_symbols": len(selected),
        "attempted": 0,
        "skipped_existing": 0,
        "inserted_rows": 0,
        "empty": 0,
        "failed": 0,
        "failed_symbols": [],
    }

    for idx, symbol in enumerate(selected):
        if not force and _has_financial_rows(conn, symbol):
            result["skipped_existing"] += 1
            continue
        if sleep_seconds > 0 and result["attempted"] > 0:
            time.sleep(float(sleep_seconds))

        result["attempted"] += 1
        try:
            df = fetch_financials(symbol)
            if df is None or df.empty:
                result["empty"] += 1
                continue
            inserted = upsert_financials(conn, df)
            result["inserted_rows"] += int(inserted)
            logger.info(f"Backfilled financials for {symbol}: rows={inserted} ({idx + 1}/{len(selected)})")
        except Exception as exc:
            result["failed"] += 1
            result["failed_symbols"].append(symbol)
            logger.warning(f"Backfill financials failed for {symbol}: {exc}")

    if result["failed"] > 0:
        result["status"] = "WARN"
    return result


def _has_financial_rows(conn: Any, symbol: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM financials WHERE symbol = ? LIMIT 1", [symbol]).fetchone())


def _normalize_cn_symbol(symbol: str) -> str:
    text = str(symbol).strip()
    if "." in text:
        left, right = text.split(".", 1)
        text = right if len(right) == 6 else left
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text
