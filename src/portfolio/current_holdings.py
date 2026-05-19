"""Shared point-in-time helpers for current paper-trading holdings."""
from __future__ import annotations

from datetime import date
from typing import Any


def current_positions_cte(as_of: date | None = None, cte_name: str = "current_positions") -> tuple[str, list[Any]]:
    """Return a CTE that treats portfolio_nav as the source of each strategy's current date.

    paper_positions stores positive holdings only.  After a strategy exits all
    positions, there is no zero-quantity row for the flat date, so using
    MAX(paper_positions.trade_date) revives stale holdings.  portfolio_nav is
    written for flat dates and therefore anchors the current point in time.
    """
    params: list[Any] = []
    nav_filter = ""
    position_filter = ""
    if as_of is not None:
        nav_filter = "WHERE trade_date <= ?"
        position_filter = "AND trade_date <= ?"
        params.extend([as_of, as_of])

    return f"""
        portfolio_latest AS (
            SELECT strategy_name, MAX(trade_date) AS trade_date
            FROM portfolio_nav
            {nav_filter}
            GROUP BY strategy_name
        ),
        position_latest AS (
            SELECT strategy_name, MAX(trade_date) AS trade_date
            FROM paper_positions
            WHERE strategy_name NOT IN (SELECT strategy_name FROM portfolio_latest)
              {position_filter}
            GROUP BY strategy_name
        ),
        latest_strategy_dates AS (
            SELECT strategy_name, trade_date FROM portfolio_latest
            UNION ALL
            SELECT strategy_name, trade_date FROM position_latest
        ),
        {cte_name} AS (
            SELECT p.*
            FROM paper_positions p
            JOIN latest_strategy_dates latest
              ON p.strategy_name = latest.strategy_name
             AND p.trade_date = latest.trade_date
            WHERE COALESCE(p.quantity, 0) > 0
              AND COALESCE(p.market_value, 0) > 0
        )
    """, params


def load_current_position_symbols(conn: Any, as_of: date | None = None, country: str | None = None) -> list[str]:
    cte, params = current_positions_cte(as_of=as_of)
    country_filter = ""
    if country:
        country_filter = "AND COALESCE(si.country, 'CN') = ?"
        params.append(str(country).upper())
    rows = conn.execute(
        f"""
        WITH {cte}
        SELECT DISTINCT cp.symbol
        FROM current_positions cp
        LEFT JOIN stock_info si ON si.symbol = cp.symbol
        WHERE 1 = 1
          {country_filter}
        ORDER BY cp.symbol
        """,
        params,
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def load_current_position_quantity(
    conn: Any,
    strategy_name: str,
    symbol: str,
    as_of: date | None = None,
) -> float:
    cte, params = current_positions_cte(as_of=as_of)
    row = conn.execute(
        f"""
        WITH {cte}
        SELECT quantity
        FROM current_positions
        WHERE strategy_name = ?
          AND symbol = ?
        LIMIT 1
        """,
        [*params, strategy_name, symbol],
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.0
