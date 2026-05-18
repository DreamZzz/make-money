"""Backfill metadata and valuation coverage for decision-relevant CN scopes."""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import uuid4

import pandas as pd
from loguru import logger


def resolve_scope_symbols(
    conn: Any,
    scope: str,
    as_of: date | None = None,
    limit: int | None = None,
    exclude_symbols: set[str] | None = None,
) -> list[str]:
    """Resolve symbols for a staged field-coverage scope."""
    exclude_symbols = exclude_symbols or set()
    if scope == "current_holdings":
        symbols = _current_holding_symbols(conn, as_of=as_of)
    elif scope == "signal_candidates":
        symbols = _latest_active_signal_symbols(conn, as_of=as_of)
    elif scope == "target_universe":
        symbols = _target_universe_symbols(conn, as_of=as_of)
    elif scope == "local_market":
        symbols = _local_market_symbols(conn, as_of=as_of)
    else:
        raise ValueError(f"Unsupported field coverage scope: {scope}")

    filtered = [symbol for symbol in symbols if symbol not in exclude_symbols]
    if limit is not None:
        filtered = filtered[: max(int(limit), 0)]
    return filtered


def load_field_coverage(conn: Any, symbols: list[str], as_of: date | None = None) -> pd.DataFrame:
    """Return metadata/valuation coverage flags for an explicit symbol list."""
    symbols = _normalize_symbols(symbols)
    if not symbols:
        return _empty_coverage_frame()
    values_sql = ",".join(["(?)"] * len(symbols))
    date_filter = "AND trade_date <= ?" if as_of else ""
    params: list[Any] = [*symbols]
    if as_of:
        params.append(as_of)
    df = conn.execute(
        f"""
        WITH scope_symbols AS (
            SELECT symbol
            FROM (VALUES {values_sql}) AS t(symbol)
        ),
        latest_price AS (
            SELECT symbol, trade_date, pe_ttm, pb
            FROM daily_price
            WHERE symbol IN (SELECT symbol FROM scope_symbols)
              {date_filter}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        )
        SELECT
            ss.symbol,
            COALESCE(si.country, 'CN') AS country,
            si.name,
            si.industry,
            si.market_cap,
            lp.trade_date AS price_date,
            lp.pe_ttm,
            lp.pb
        FROM scope_symbols ss
        LEFT JOIN stock_info si ON si.symbol = ss.symbol
        LEFT JOIN latest_price lp ON lp.symbol = ss.symbol
        ORDER BY ss.symbol
        """,
        params,
    ).fetchdf()
    return _add_missing_flags(df)


def backfill_field_coverage(
    conn: Any,
    scope: str = "target_universe",
    symbols: list[str] | None = None,
    as_of: date | None = None,
    fetch_cn_spot: Callable[[], pd.DataFrame] | None = None,
    fetch_tencent_quote: Callable[[list[str]], pd.DataFrame] | None = None,
    fetch_cn_individual: Callable[[str], pd.DataFrame] | None = None,
    force: bool = False,
    limit: int | None = None,
    sleep_seconds: float = 0.0,
    fetch_industry: bool = True,
) -> dict[str, Any]:
    """Backfill industry/market-cap/PE/PB for one scope without touching signals."""
    resolved = _normalize_symbols(symbols) if symbols is not None else resolve_scope_symbols(conn, scope, as_of=as_of, limit=limit)
    before = load_field_coverage(conn, resolved, as_of=as_of)
    if before.empty:
        return _result(scope, 0, 0, 0, [], before, before)

    needs_update = before if force else before[_row_needs_update(before, include_industry=fetch_industry)].copy()
    if needs_update.empty:
        return _result(scope, len(before), 0, 0, [], before, before)

    from src.data_pipeline.fetchers import akshare_fetcher as ak
    from src.data_pipeline.fetchers import free_sources

    fetch_cn_spot = fetch_cn_spot or ak.fetch_cn_stock_spot
    fetch_tencent_quote = fetch_tencent_quote or free_sources.fetch_tencent_quote_snapshot
    fetch_cn_individual = fetch_cn_individual or ak.fetch_cn_stock_individual_info

    spot = _safe_fetch_spot(fetch_cn_spot)
    if spot.empty:
        spot = _safe_fetch_tencent_quote(
            fetch_tencent_quote,
            needs_update["symbol"].astype(str).tolist(),
        )
    spot_by_symbol = _rows_by_symbol(spot)
    stock_updates: dict[str, dict[str, Any]] = {}
    valuation_updates: dict[str, dict[str, Any]] = {}
    failed_symbols: set[str] = set()

    for _, row in needs_update.iterrows():
        symbol = str(row["symbol"])
        country = str(row.get("country") or "CN").upper()
        if country != "CN":
            continue
        spot_row = spot_by_symbol.get(symbol)
        if spot_row is not None:
            _merge_stock_update(stock_updates, symbol, row, spot_row, force=force)
            _merge_valuation_update(valuation_updates, symbol, row, spot_row, force=force)

        if fetch_industry and _needs_individual_fetch(row, stock_updates.get(symbol), force=force):
            try:
                individual = fetch_cn_individual(symbol)
                if individual is not None and not individual.empty:
                    _merge_stock_update(stock_updates, symbol, row, individual.iloc[0], force=force)
                else:
                    failed_symbols.add(symbol)
            except Exception as exc:
                logger.warning(f"Field coverage individual fetch failed for {symbol}: {exc}")
                failed_symbols.add(symbol)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    updated_stock_info = _apply_stock_updates(conn, before, stock_updates, force=force)
    updated_daily_price = _apply_valuation_updates(conn, before, valuation_updates, force=force)
    after = load_field_coverage(conn, resolved, as_of=as_of)
    unresolved_mask = _row_needs_update(after) if fetch_industry else _row_needs_update(after, include_industry=False)
    unresolved = set(after.loc[unresolved_mask, "symbol"].astype(str).tolist())
    failed = sorted(failed_symbols | unresolved)
    result = _result(scope, len(before), updated_stock_info, updated_daily_price, failed, before, after)
    if not fetch_industry:
        result["skipped_industry_symbols"] = int(after["missing_industry"].astype(bool).sum())
    return result


def backfill_field_coverage_scopes(
    conn: Any,
    scopes: list[str] | None = None,
    as_of: date | None = None,
    fetch_cn_spot: Callable[[], pd.DataFrame] | None = None,
    fetch_tencent_quote: Callable[[list[str]], pd.DataFrame] | None = None,
    fetch_cn_individual: Callable[[str], pd.DataFrame] | None = None,
    force: bool = False,
    limit_per_scope: int | None = None,
    sleep_seconds: float = 0.0,
    fetch_industry: bool = True,
) -> dict[str, Any]:
    """Backfill staged scopes in priority order, deduplicating already handled symbols."""
    scopes = scopes or ["current_holdings", "signal_candidates", "target_universe"]
    seen: set[str] = set()
    results = []
    for scope in scopes:
        symbols = resolve_scope_symbols(conn, scope, as_of=as_of, limit=limit_per_scope, exclude_symbols=seen)
        seen.update(symbols)
        result = backfill_field_coverage(
            conn,
            scope=scope,
            symbols=symbols,
            as_of=as_of,
            fetch_cn_spot=fetch_cn_spot,
            fetch_tencent_quote=fetch_tencent_quote,
            fetch_cn_individual=fetch_cn_individual,
            force=force,
            sleep_seconds=sleep_seconds,
            fetch_industry=fetch_industry,
        )
        results.append(result)
    return {
        "status": "WARN" if any(item["status"] != "OK" for item in results) else "OK",
        "scopes": results,
        "total_symbols": sum(item["symbols"] for item in results),
        "updated_stock_info": sum(item["updated_stock_info"] for item in results),
        "updated_daily_price": sum(item["updated_daily_price"] for item in results),
    }


def build_field_coverage_health_rows(result: dict[str, Any], run_id: str | None = None) -> list[dict[str, Any]]:
    """Convert field-coverage results into data_source_health rows."""
    run_id = run_id or f"FIELD-COVERAGE-{uuid4().hex[:12]}"
    rows = []
    for item in result.get("scopes", []):
        scope = str(item.get("scope") or "unknown")
        symbols = int(item.get("symbols") or 0)
        updated_stock_info = int(item.get("updated_stock_info") or 0)
        updated_daily_price = int(item.get("updated_daily_price") or 0)
        failed_symbols = list(item.get("failed_symbols") or [])
        unresolved = len(failed_symbols)
        updated = min(symbols, updated_stock_info + updated_daily_price)
        rows.append({
            "run_id": run_id,
            "source": "free_sources",
            "market": "CN",
            "operation": f"field_coverage_{scope}",
            "status": "DEGRADED" if unresolved else str(item.get("status") or "OK"),
            "attempted": symbols,
            "updated": updated,
            "no_data": unresolved,
            "source_error": 0,
            "rate_limited": 0,
            "circuit_skip": 0,
            "failed": unresolved,
            "message": (
                f"field coverage backfill {scope}: {symbols} symbols, "
                f"{updated_stock_info} stock_info, {updated_daily_price} valuation updates, {unresolved} unresolved"
            ),
            "stats_json": {
                "failed_symbols": failed_symbols,
                "missing_after": item.get("missing_after") or {},
            },
        })
    return rows


def _current_holding_symbols(conn: Any, as_of: date | None = None) -> list[str]:
    date_filter = "WHERE trade_date <= ?" if as_of else ""
    params = [as_of] if as_of else []
    rows = conn.execute(
        f"""
        WITH latest_positions AS (
            SELECT strategy_name, MAX(trade_date) AS trade_date
            FROM paper_positions
            {date_filter}
            GROUP BY strategy_name
        )
        SELECT DISTINCT p.symbol
        FROM paper_positions p
        JOIN latest_positions latest
          ON p.strategy_name = latest.strategy_name
         AND p.trade_date = latest.trade_date
        LEFT JOIN stock_info si ON si.symbol = p.symbol
        WHERE COALESCE(p.quantity, 0) > 0
          AND COALESCE(p.market_value, 0) > 0
          AND COALESCE(si.country, 'CN') = 'CN'
        ORDER BY p.symbol
        """,
        params,
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _latest_active_signal_symbols(conn: Any, as_of: date | None = None) -> list[str]:
    date_filter = "AND CAST(signal_ts AS DATE) <= ?" if as_of else ""
    params = [as_of] if as_of else []
    latest = conn.execute(
        f"""
        SELECT MAX(CAST(signal_ts AS DATE))
        FROM signals
        WHERE status = 'ACTIVE'
          {date_filter}
        """,
        params,
    ).fetchone()[0]
    if latest is None:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT s.symbol
        FROM signals s
        LEFT JOIN stock_info si ON si.symbol = s.symbol
        WHERE s.status = 'ACTIVE'
          AND CAST(s.signal_ts AS DATE) = ?
          AND COALESCE(si.country, 'CN') = 'CN'
        ORDER BY s.symbol
        """,
        [latest],
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _target_universe_symbols(conn: Any, as_of: date | None = None) -> list[str]:
    latest = _latest_price_date(conn, as_of)
    if latest is None:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT dp.symbol
        FROM daily_price dp
        JOIN stock_info si ON si.symbol = dp.symbol
        WHERE dp.trade_date = ?
          AND si.country = 'CN'
          AND regexp_matches(dp.symbol, '^[0-9]{6}$')
        ORDER BY dp.symbol
        """,
        [latest],
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _local_market_symbols(conn: Any, as_of: date | None = None) -> list[str]:
    latest = _latest_price_date(conn, as_of)
    if latest is None:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_price
        WHERE trade_date = ?
          AND regexp_matches(symbol, '^[0-9]{6}$')
        ORDER BY symbol
        """,
        [latest],
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _latest_price_date(conn: Any, as_of: date | None = None) -> date | None:
    if as_of:
        return conn.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date <= ?", [as_of]).fetchone()[0]
    return conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]


def _safe_fetch_spot(fetch_cn_spot: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    try:
        df = fetch_cn_spot()
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        logger.warning(f"Field coverage spot fetch failed: {exc}")
        return pd.DataFrame()


def _safe_fetch_tencent_quote(fetch_tencent_quote: Callable[[list[str]], pd.DataFrame], symbols: list[str]) -> pd.DataFrame:
    try:
        df = fetch_tencent_quote(_normalize_symbols(symbols))
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        logger.warning(f"Field coverage Tencent quote fetch failed: {exc}")
        return pd.DataFrame()


def _rows_by_symbol(df: pd.DataFrame) -> dict[str, pd.Series]:
    if df.empty or "symbol" not in df.columns:
        return {}
    return {str(row["symbol"]).zfill(6): row for _, row in df.iterrows() if pd.notna(row.get("symbol"))}


def _merge_stock_update(
    updates: dict[str, dict[str, Any]],
    symbol: str,
    current: pd.Series,
    source: pd.Series,
    force: bool = False,
) -> None:
    update = updates.setdefault(symbol, {})
    if _missing_text(current.get("name")) and not _missing_text(source.get("name")) and (force or "name" not in update):
        update["name"] = str(source.get("name"))
    if (
        (force or bool(current.get("missing_industry")))
        and not _missing_text(source.get("industry"))
        and (force or "industry" not in update)
    ):
        update["industry"] = str(source.get("industry"))
    market_cap = _safe_positive_float(source.get("market_cap"))
    if (
        (force or bool(current.get("missing_market_cap")))
        and market_cap is not None
        and (force or "market_cap" not in update)
    ):
        update["market_cap"] = market_cap


def _merge_valuation_update(
    updates: dict[str, dict[str, Any]],
    symbol: str,
    current: pd.Series,
    source: pd.Series,
    force: bool = False,
) -> None:
    update = updates.setdefault(symbol, {})
    pe = _safe_float(source.get("pe_ttm"))
    if (force or bool(current.get("missing_pe_ttm"))) and pe is not None:
        update["pe_ttm"] = pe
    pb = _safe_positive_float(source.get("pb"))
    if (force or bool(current.get("missing_pb"))) and pb is not None:
        update["pb"] = pb


def _needs_individual_fetch(current: pd.Series, update: dict[str, Any] | None, force: bool = False) -> bool:
    if force:
        return True
    update = update or {}
    return (
        (bool(current.get("missing_industry")) and "industry" not in update)
        or (bool(current.get("missing_market_cap")) and "market_cap" not in update)
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
        if not update or symbol not in base.index:
            continue
        current = base.loc[symbol]
        existing = conn.execute(
            "SELECT name, industry, market_cap FROM stock_info WHERE symbol = ?",
            [symbol],
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO stock_info (symbol, country, name, industry, market_cap)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    symbol,
                    str(current.get("country") or "CN"),
                    update.get("name") or current.get("name") or symbol,
                    update.get("industry"),
                    update.get("market_cap"),
                ],
            )
            count += 1
            continue
        name, industry, market_cap = existing
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
            conn.execute(
                """
                UPDATE stock_info
                SET name = ?, industry = ?, market_cap = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ?
                """,
                [name, industry, market_cap, symbol],
            )
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
            continue
        existing = conn.execute(
            "SELECT pe_ttm, pb FROM daily_price WHERE symbol = ? AND trade_date = ?",
            [symbol, pd.to_datetime(price_date).date()],
        ).fetchone()
        if not existing:
            continue
        pe_ttm, pb = existing
        changed = False
        if (force or pd.isna(pe_ttm)) and _safe_float(update.get("pe_ttm")) is not None:
            pe_ttm = float(update["pe_ttm"])
            changed = True
        if (force or _safe_positive_float(pb) is None) and _safe_positive_float(update.get("pb")) is not None:
            pb = float(update["pb"])
            changed = True
        if changed:
            conn.execute(
                """
                UPDATE daily_price
                SET pe_ttm = ?, pb = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ? AND trade_date = ?
                """,
                [pe_ttm, pb, symbol, pd.to_datetime(price_date).date()],
            )
            count += 1
    return count


def _result(
    scope: str,
    symbols: int,
    updated_stock_info: int,
    updated_daily_price: int,
    failed_symbols: list[str],
    before: pd.DataFrame,
    after: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "status": "WARN" if failed_symbols else "OK",
        "scope": scope,
        "symbols": int(symbols),
        "updated_stock_info": int(updated_stock_info),
        "updated_daily_price": int(updated_daily_price),
        "failed_symbols": failed_symbols,
        "missing_before": _missing_summary(before),
        "missing_after": _missing_summary(after),
    }


def _add_missing_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["missing_industry"] = out["industry"].map(_missing_text)
    out["missing_market_cap"] = out["market_cap"].map(lambda value: _safe_positive_float(value) is None)
    out["missing_pe_ttm"] = pd.to_numeric(out["pe_ttm"], errors="coerce").isna()
    out["missing_pb"] = out["pb"].map(lambda value: _safe_positive_float(value) is None)
    for col in ["missing_industry", "missing_market_cap", "missing_pe_ttm", "missing_pb"]:
        out[col] = out[col].astype(object)
    return out


def _row_needs_update(df: pd.DataFrame, include_industry: bool = True) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    needs = (
        df["missing_market_cap"].astype(bool)
        | df["missing_pe_ttm"].astype(bool)
        | df["missing_pb"].astype(bool)
    )
    if include_industry:
        needs = needs | df["missing_industry"].astype(bool)
    return needs


def _missing_summary(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {"industry": 0, "market_cap": 0, "pe_ttm": 0, "pb": 0}
    return {
        "industry": int(df["missing_industry"].astype(bool).sum()),
        "market_cap": int(df["missing_market_cap"].astype(bool).sum()),
        "pe_ttm": int(df["missing_pe_ttm"].astype(bool).sum()),
        "pb": int(df["missing_pb"].astype(bool).sum()),
    }


def _normalize_symbols(symbols: list[str]) -> list[str]:
    seen = set()
    out = []
    for symbol in symbols:
        value = str(symbol).strip().zfill(6)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _empty_coverage_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "symbol",
        "country",
        "name",
        "industry",
        "market_cap",
        "price_date",
        "pe_ttm",
        "pb",
        "missing_industry",
        "missing_market_cap",
        "missing_pe_ttm",
        "missing_pb",
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
    parser = argparse.ArgumentParser(description="Backfill CN field coverage from free sources.")
    parser.add_argument(
        "--scopes",
        default="current_holdings,signal_candidates,target_universe",
        help="Comma-separated scopes: current_holdings,signal_candidates,target_universe,local_market",
    )
    parser.add_argument("--as-of", default=None, help="Use data no later than YYYY-MM-DD.")
    parser.add_argument("--limit-per-scope", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds between per-symbol metadata calls.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing fields instead of filling only missing values.")
    parser.add_argument("--skip-industry-fetch", action="store_true", help="Skip slow per-symbol industry fetches and only fill valuation/market-cap fields.")
    parser.add_argument("--record-health", action="store_true", help="Write scope results to data_source_health.")
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db, record_data_source_health
    from src.data_pipeline.network_env import prepare_finance_data_environment

    prepare_finance_data_environment()
    as_of = pd.to_datetime(args.as_of).date() if args.as_of else None
    scopes = [scope.strip() for scope in args.scopes.split(",") if scope.strip()]
    conn = get_connection()
    try:
        init_db(conn)
        result = backfill_field_coverage_scopes(
            conn,
            scopes=scopes,
            as_of=as_of,
            force=args.force,
            limit_per_scope=args.limit_per_scope,
            sleep_seconds=args.sleep,
            fetch_industry=not args.skip_industry_fetch,
        )
        if args.record_health:
            result["recorded_health_rows"] = record_data_source_health(
                conn,
                build_field_coverage_health_rows(result),
            )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
