"""Lifecycle helpers for stock trading signals."""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
from loguru import logger


ACTIVE = "ACTIVE"
FILLED = "FILLED"
NO_ACTION = "NO_ACTION"
EXPIRED = "EXPIRED"
SUPERSEDED = "SUPERSEDED"


def expire_stale_signals(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
    valid_trading_days: int = 1,
) -> int:
    """
    Mark stale pending signals as expired.

    A signal is actionable for the first ``valid_trading_days`` market sessions
    after its signal date. With the default value, that means only T+1 is
    allowed; once T+2 market data exists, the unexecuted signal expires.
    """
    if valid_trading_days < 1:
        valid_trading_days = 1

    params: list[object] = []
    as_of_filter = ""
    if as_of is not None:
        as_of_filter = "AND md.trade_date <= ?"
        params.append(as_of)
    params.append(valid_trading_days)
    params.append(as_of or date.today())

    expired = conn.execute(f"""
        WITH pending AS (
            SELECT s.signal_id,
                   CAST(s.signal_ts AS DATE) AS signal_date,
                   COALESCE(si.country, 'CN') AS market
            FROM signals s
            LEFT JOIN stock_info si ON s.symbol = si.symbol
            WHERE s.executed = FALSE
              AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
        ),
        market_dates AS (
            SELECT DISTINCT COALESCE(si.country, 'CN') AS market, dp.trade_date
            FROM daily_price dp
            LEFT JOIN stock_info si ON dp.symbol = si.symbol
        ),
        post_signal_dates AS (
            SELECT p.signal_id,
                   md.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.signal_id ORDER BY md.trade_date
                   ) AS rn
            FROM pending p
            JOIN market_dates md
              ON md.market = p.market
             AND md.trade_date > p.signal_date
             {as_of_filter}
        ),
        stale AS (
            SELECT signal_id
            FROM post_signal_dates
            GROUP BY signal_id
            HAVING MAX(rn) > ?
        )
        UPDATE signals
        SET executed = TRUE,
            status = 'EXPIRED',
            status_reason = '超过T+1有效期未执行',
            execution_date = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE signal_id IN (SELECT signal_id FROM stale)
        RETURNING signal_id
    """, params).fetchall()

    count = len(expired)
    if count:
        logger.info(f"Expired stale pending signals: {count}")
    return count


def retire_replaced_signals(conn: duckdb.DuckDBPyConnection, new_signals: pd.DataFrame) -> int:
    """Mark older active signals for the same strategy/symbol/side as superseded."""
    if new_signals.empty:
        return 0

    required = {"signal_id", "model_name", "symbol", "side", "signal_ts"}
    if not required.issubset(set(new_signals.columns)):
        return 0

    replacements = new_signals[list(required)].copy()
    replacements["signal_ts"] = pd.to_datetime(replacements["signal_ts"])
    conn.execute("CREATE OR REPLACE TEMP TABLE _new_signal_replacements AS SELECT * FROM replacements")
    rows = conn.execute("""
        UPDATE signals old
        SET executed = TRUE,
            status = 'SUPERSEDED',
            status_reason = '已被更新信号替代',
            execution_date = CAST(new.signal_ts AS DATE),
            superseded_by = new.signal_id,
            updated_at = CURRENT_TIMESTAMP
        FROM _new_signal_replacements new
        WHERE old.executed = FALSE
          AND COALESCE(old.status, 'ACTIVE') = 'ACTIVE'
          AND old.model_name = new.model_name
          AND old.symbol = new.symbol
          AND old.side = new.side
          AND old.signal_ts < new.signal_ts
        RETURNING old.signal_id
    """).fetchall()

    count = len(rows)
    if count:
        logger.info(f"Superseded old pending signals: {count}")
    return count
