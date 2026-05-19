"""Fill missing fundamentals for the current paper-trading holdings."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from loguru import logger


def load_current_holding_coverage(conn: Any, as_of: date | None = None) -> pd.DataFrame:
    """Return latest positive paper holdings with metadata/valuation coverage flags."""
    price_date_filter = ""
    if as_of is not None:
        price_date_filter = "AND trade_date <= ?"

    from src.portfolio.current_holdings import current_positions_cte

    current_positions, position_params = current_positions_cte(as_of=as_of)
    price_params = [as_of] if as_of is not None else []
    df = conn.execute(f"""
        WITH {current_positions},
        current_symbols AS (
            SELECT
                p.symbol,
                SUM(COALESCE(p.market_value, 0)) AS market_value,
                MAX(COALESCE(si.country, 'CN')) AS country
            FROM current_positions p
            LEFT JOIN stock_info si ON p.symbol = si.symbol
            GROUP BY p.symbol
        ),
        latest_price AS (
            SELECT symbol, trade_date, pe_ttm, pb
            FROM daily_price
            WHERE 1 = 1
              {price_date_filter}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        )
        SELECT
            cs.symbol,
            cs.country,
            cs.market_value,
            si.name,
            si.industry,
            si.market_cap,
            lp.trade_date AS price_date,
            lp.pe_ttm,
            lp.pb
        FROM current_symbols cs
        LEFT JOIN stock_info si ON cs.symbol = si.symbol
        LEFT JOIN latest_price lp ON cs.symbol = lp.symbol
        ORDER BY cs.market_value DESC, cs.symbol
    """, [*position_params, *price_params]).fetchdf()
    if df.empty:
        return _empty_coverage_frame()
    return _add_missing_flags(df)


def refresh_current_holding_fundamentals(
    conn: Any,
    as_of: date | None = None,
    fetch_cn_spot: Callable[[], pd.DataFrame] | None = None,
    fetch_cn_individual: Callable[[str], pd.DataFrame] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fill missing industry/market-cap/PE/PB for current holdings without blocking callers."""
    before = load_current_holding_coverage(conn, as_of=as_of)
    if before.empty:
        return {
            "status": "OK",
            "holdings": 0,
            "updated_stock_info": 0,
            "updated_daily_price": 0,
            "failed_symbols": [],
            "missing_before": _missing_summary(before),
            "missing_after": _missing_summary(before),
        }

    missing = before[_row_needs_update(before)].copy()
    if missing.empty and not force:
        return {
            "status": "OK",
            "holdings": int(len(before)),
            "updated_stock_info": 0,
            "updated_daily_price": 0,
            "failed_symbols": [],
            "missing_before": _missing_summary(before),
            "missing_after": _missing_summary(before),
        }

    from src.data_pipeline.fetchers import akshare_fetcher as ak

    fetch_cn_spot = fetch_cn_spot or ak.fetch_cn_stock_spot
    fetch_cn_individual = fetch_cn_individual or ak.fetch_cn_stock_individual_info

    spot = _safe_fetch_spot(fetch_cn_spot)
    spot_by_symbol = {
        str(row["symbol"]): row
        for _, row in spot.iterrows()
        if pd.notna(row.get("symbol"))
    } if not spot.empty else {}

    stock_updates: dict[str, dict[str, Any]] = {}
    valuation_updates: dict[str, dict[str, Any]] = {}

    for _, holding in missing.iterrows():
        symbol = str(holding["symbol"])
        country = str(holding.get("country") or "CN").upper()
        if country != "CN":
            logger.warning(f"Fundamentals coverage skipped unsupported market for {symbol}: {country}")
            continue

        spot_row = spot_by_symbol.get(symbol)
        if spot_row is not None:
            _merge_stock_update(stock_updates, symbol, holding, spot_row, force=force)
            _merge_valuation_update(valuation_updates, symbol, holding, spot_row, force=force)

        if _needs_individual_fetch(holding, stock_updates.get(symbol), force=force):
            try:
                individual = fetch_cn_individual(symbol)
                if individual is not None and not individual.empty:
                    row = individual.iloc[0]
                    _merge_stock_update(stock_updates, symbol, holding, row, force=force)
            except Exception as exc:
                logger.warning(f"Fundamentals coverage fetch failed for {symbol}: {exc}")

    updated_stock_info = _apply_stock_updates(conn, before, stock_updates, force=force)
    updated_daily_price = _apply_valuation_updates(conn, before, valuation_updates, force=force)
    after = load_current_holding_coverage(conn, as_of=as_of)
    failed = sorted(after.loc[_row_needs_update(after), "symbol"].astype(str).unique().tolist())
    for symbol in failed:
        logger.warning(f"Fundamentals coverage unresolved for holding {symbol}")

    return {
        "status": "WARN" if failed else "OK",
        "holdings": int(len(before)),
        "updated_stock_info": int(updated_stock_info),
        "updated_daily_price": int(updated_daily_price),
        "failed_symbols": failed,
        "missing_before": _missing_summary(before),
        "missing_after": _missing_summary(after),
    }


def _safe_fetch_spot(fetch_cn_spot: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    try:
        df = fetch_cn_spot()
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        logger.warning(f"Fundamentals coverage spot fetch failed: {exc}")
        return pd.DataFrame()


def _merge_stock_update(
    updates: dict[str, dict[str, Any]],
    symbol: str,
    holding: pd.Series,
    source: pd.Series,
    force: bool = False,
) -> None:
    update = updates.setdefault(symbol, {})
    if _missing_text(holding.get("name")) and not _missing_text(source.get("name")) and (force or "name" not in update):
        update["name"] = str(source.get("name"))
    if (
        (force or bool(holding.get("missing_industry")))
        and not _missing_text(source.get("industry"))
        and (force or "industry" not in update)
    ):
        update["industry"] = str(source.get("industry"))
    market_cap = _safe_positive_float(source.get("market_cap"))
    if (
        (force or bool(holding.get("missing_market_cap")))
        and market_cap is not None
        and (force or "market_cap" not in update)
    ):
        update["market_cap"] = market_cap


def _merge_valuation_update(
    updates: dict[str, dict[str, Any]],
    symbol: str,
    holding: pd.Series,
    source: pd.Series,
    force: bool = False,
) -> None:
    update = updates.setdefault(symbol, {})
    pe = _safe_float(source.get("pe_ttm"))
    if (force or bool(holding.get("missing_pe_ttm"))) and pe is not None:
        update["pe_ttm"] = pe
    pb = _safe_positive_float(source.get("pb"))
    if (force or bool(holding.get("missing_pb"))) and pb is not None:
        update["pb"] = pb


def _needs_individual_fetch(holding: pd.Series, stock_update: dict[str, Any] | None, force: bool = False) -> bool:
    if force:
        return True
    update = stock_update or {}
    return (
        (bool(holding.get("missing_industry")) and "industry" not in update)
        or (bool(holding.get("missing_market_cap")) and "market_cap" not in update)
    )


def _apply_stock_updates(
    conn: Any,
    coverage: pd.DataFrame,
    updates: dict[str, dict[str, Any]],
    force: bool = False,
) -> int:
    count = 0
    base = coverage.set_index("symbol")
    for symbol, update in updates.items():
        if not update:
            continue
        holding = base.loc[symbol]
        existing = conn.execute("""
            SELECT name, industry, market_cap
            FROM stock_info
            WHERE symbol = ?
        """, [symbol]).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO stock_info (symbol, country, name, industry, market_cap)
                VALUES (?, ?, ?, ?, ?)
            """, [
                symbol,
                str(holding.get("country") or "CN"),
                update.get("name") or holding.get("name") or symbol,
                update.get("industry"),
                update.get("market_cap"),
            ])
            count += 1
            continue

        name = existing[0]
        industry = existing[1]
        market_cap = existing[2]
        changed = False
        if _missing_text(name) and not _missing_text(update.get("name")):
            name = update["name"]
            changed = True
        if (force or _missing_text(industry)) and not _missing_text(update.get("industry")):
            industry = update["industry"]
            changed = True
        if (force or _safe_positive_float(market_cap) is None) and _safe_positive_float(update.get("market_cap")) is not None:
            market_cap = float(update["market_cap"])
            changed = True
        if changed:
            conn.execute("""
                UPDATE stock_info
                SET name = ?, industry = ?, market_cap = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ?
            """, [name, industry, market_cap, symbol])
            count += 1
    return count


def _apply_valuation_updates(
    conn: Any,
    coverage: pd.DataFrame,
    updates: dict[str, dict[str, Any]],
    force: bool = False,
) -> int:
    count = 0
    base = coverage.set_index("symbol")
    for symbol, update in updates.items():
        if not update or symbol not in base.index:
            continue
        price_date = base.loc[symbol].get("price_date")
        if pd.isna(price_date):
            logger.warning(f"Fundamentals coverage cannot update valuation without daily_price row: {symbol}")
            continue
        existing = conn.execute("""
            SELECT pe_ttm, pb
            FROM daily_price
            WHERE symbol = ? AND trade_date = ?
        """, [symbol, pd.to_datetime(price_date).date()]).fetchone()
        if not existing:
            continue
        pe_ttm = existing[0]
        pb = existing[1]
        changed = False
        if (force or pd.isna(pe_ttm)) and _safe_float(update.get("pe_ttm")) is not None:
            pe_ttm = float(update["pe_ttm"])
            changed = True
        if (force or _safe_positive_float(pb) is None) and _safe_positive_float(update.get("pb")) is not None:
            pb = float(update["pb"])
            changed = True
        if changed:
            conn.execute("""
                UPDATE daily_price
                SET pe_ttm = ?, pb = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ? AND trade_date = ?
            """, [pe_ttm, pb, symbol, pd.to_datetime(price_date).date()])
            count += 1
    return count


def _add_missing_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["missing_industry"] = out["industry"].map(_missing_text)
    out["missing_market_cap"] = out["market_cap"].map(lambda value: _safe_positive_float(value) is None)
    out["missing_pe_ttm"] = pd.to_numeric(out["pe_ttm"], errors="coerce").isna()
    out["missing_pb"] = out["pb"].map(lambda value: _safe_positive_float(value) is None)
    for col in ["missing_industry", "missing_market_cap", "missing_pe_ttm", "missing_pb"]:
        out[col] = out[col].astype(object)
    return out


def _row_needs_update(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    return (
        df["missing_industry"].astype(bool)
        | df["missing_market_cap"].astype(bool)
        | df["missing_pe_ttm"].astype(bool)
        | df["missing_pb"].astype(bool)
    )


def _missing_summary(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0}
    return {
        "industry": int(df["missing_industry"].astype(bool).sum()),
        "market_cap": int(df["missing_market_cap"].astype(bool).sum()),
        "pe_ttm": int(df["missing_pe_ttm"].astype(bool).sum()),
        "pb": int(df["missing_pb"].astype(bool).sum()),
    }


def _empty_coverage_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "symbol", "country", "market_value", "name", "industry", "market_cap",
        "price_date", "pe_ttm", "pb", "missing_industry", "missing_market_cap",
        "missing_pe_ttm", "missing_pb",
    ])


def _missing_text(value: Any) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() == ""


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_positive_float(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None or number <= 0:
        return None
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill missing fundamentals for current holdings")
    sub = parser.add_subparsers(dest="command", required=True)
    update = sub.add_parser("update", help="补齐当前持仓基础信息覆盖")
    update.add_argument("--as-of", default=None, help="只检查该日期及以前的持仓/行情，YYYY-MM-DD")
    update.add_argument("--force", action="store_true", help="覆盖已有字段；默认只补空字段")
    args = parser.parse_args(argv)

    if args.command == "update":
        from src.data_pipeline.loader import get_connection, init_db

        as_of = pd.to_datetime(args.as_of).date() if args.as_of else None
        conn = get_connection()
        try:
            init_db(conn)
            result = refresh_current_holding_fundamentals(conn, as_of=as_of, force=args.force)
        except Exception as exc:
            logger.exception(f"Fundamentals coverage failed without blocking daily close: {exc}")
            result = {
                "status": "ERROR",
                "holdings": 0,
                "updated_stock_info": 0,
                "updated_daily_price": 0,
                "failed_symbols": [],
                "missing_before": {},
                "missing_after": {},
                "error": str(exc),
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
