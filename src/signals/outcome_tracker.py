"""Persist realized forward outcomes for executed trading signals."""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from datetime import date
from typing import Any

import pandas as pd

DEFAULT_HORIZONS = (1, 5, 20)
DEFAULT_BENCHMARK_BY_COUNTRY = {
    "CN": "000300",
    "HK": "HSTECH",
}


def update_signal_outcomes(
    conn: Any,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    as_of: date | None = None,
) -> dict[str, int]:
    signals = _load_executed_signals(conn)
    horizon_values = tuple(int(h) for h in horizons if int(h) > 0)
    if signals.empty or not horizon_values:
        return {"updated": 0, "ready": 0, "pending": 0}

    rows = []
    for _, signal in signals.iterrows():
        prices = _future_prices(conn, str(signal["symbol"]), signal["execution_date"], as_of=as_of)
        benchmark_code = _benchmark_code_for_signal(signal)
        benchmark_prices = (
            _benchmark_prices(conn, benchmark_code, signal["execution_date"], as_of=as_of)
            if benchmark_code
            else pd.DataFrame()
        )
        for horizon in horizon_values:
            outcome = _outcome_for_horizon(signal, prices, horizon, benchmark_code, benchmark_prices)
            rows.append(outcome)

    df = pd.DataFrame(rows)
    signal_ids = sorted(df["signal_id"].dropna().unique().tolist())
    placeholders = ",".join(["?"] * len(signal_ids))
    conn.execute(f"DELETE FROM signal_outcomes WHERE signal_id IN ({placeholders})", signal_ids)
    conn.register("_tmp_signal_outcomes", df)
    conn.execute("""
        INSERT INTO signal_outcomes (
            signal_id, horizon_days, model_name, model_version, symbol, side,
            signal_date, execution_date, execution_price, outcome_date,
            outcome_price, return_pct, benchmark_code, benchmark_return_pct,
            alpha_vs_benchmark, status
        )
        SELECT signal_id, horizon_days, model_name, model_version, symbol, side,
               signal_date, execution_date, execution_price, outcome_date,
               outcome_price, return_pct, benchmark_code, benchmark_return_pct,
               alpha_vs_benchmark, status
        FROM _tmp_signal_outcomes
    """)
    ready = int((df["status"] == "READY").sum())
    pending = int((df["status"] == "PENDING").sum())
    return {"updated": len(df), "ready": ready, "pending": pending}


def _load_executed_signals(conn: Any) -> pd.DataFrame:
    return conn.execute("""
        SELECT s.signal_id, s.model_name, s.model_version, s.symbol, s.side,
               CAST(signal_ts AS DATE) AS signal_date,
               s.execution_date,
               s.execution_price,
               COALESCE(si.country, '') AS country
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.executed = TRUE
          AND s.status = 'FILLED'
          AND s.execution_date IS NOT NULL
          AND s.execution_price IS NOT NULL
          AND s.execution_price > 0
        ORDER BY execution_date, signal_id
    """).fetchdf()


def _future_prices(conn: Any, symbol: str, execution_date: date, as_of: date | None = None) -> pd.DataFrame:
    params: list[Any] = [symbol, execution_date]
    as_of_filter = ""
    if as_of is not None:
        as_of_filter = "AND trade_date <= ?"
        params.append(as_of)
    return conn.execute(f"""
        SELECT trade_date, close
        FROM daily_price
        WHERE symbol = ?
          AND trade_date > ?
          AND close IS NOT NULL
          {as_of_filter}
        ORDER BY trade_date
    """, params).fetchdf()


def _benchmark_prices(conn: Any, benchmark_code: str, execution_date: date, as_of: date | None = None) -> pd.DataFrame:
    params: list[Any] = [benchmark_code, execution_date, benchmark_code, execution_date]
    as_of_filter = ""
    if as_of is not None:
        as_of_filter = "AND trade_date <= ?"
        params.append(as_of)
    return conn.execute(f"""
        WITH base AS (
            SELECT trade_date, close
            FROM index_daily
            WHERE index_code = ?
              AND trade_date <= ?
              AND close IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 1
        ),
        future AS (
            SELECT trade_date, close
            FROM index_daily
            WHERE index_code = ?
              AND trade_date > ?
              AND close IS NOT NULL
              {as_of_filter}
            ORDER BY trade_date
        )
        SELECT trade_date, close, TRUE AS is_base FROM base
        UNION ALL
        SELECT trade_date, close, FALSE AS is_base FROM future
        ORDER BY is_base DESC, trade_date
    """, params).fetchdf()


def _outcome_for_horizon(
    signal: pd.Series,
    prices: pd.DataFrame,
    horizon_days: int,
    benchmark_code: str | None = None,
    benchmark_prices: pd.DataFrame | None = None,
) -> dict[str, Any]:
    base = {
        "signal_id": signal["signal_id"],
        "horizon_days": horizon_days,
        "model_name": signal.get("model_name"),
        "model_version": signal.get("model_version"),
        "symbol": signal["symbol"],
        "side": str(signal.get("side") or "").upper(),
        "signal_date": signal.get("signal_date"),
        "execution_date": signal.get("execution_date"),
        "execution_price": float(signal.get("execution_price") or 0),
        "outcome_date": None,
        "outcome_price": None,
        "return_pct": None,
        "benchmark_code": benchmark_code,
        "benchmark_return_pct": None,
        "alpha_vs_benchmark": None,
        "status": "PENDING",
    }
    if len(prices) < horizon_days:
        return base
    row = prices.iloc[horizon_days - 1]
    outcome_price = float(row["close"])
    if outcome_price <= 0 or base["execution_price"] <= 0:
        return base
    base["outcome_date"] = pd.to_datetime(row["trade_date"]).date()
    base["outcome_price"] = outcome_price
    base["return_pct"] = _forward_return(base["side"], base["execution_price"], outcome_price)
    benchmark_return = _benchmark_return_for_horizon(benchmark_prices, horizon_days)
    if benchmark_return is not None:
        base["benchmark_return_pct"] = benchmark_return
        base["alpha_vs_benchmark"] = base["return_pct"] - benchmark_return
    base["status"] = "READY"
    return base


def _benchmark_code_for_signal(signal: pd.Series) -> str | None:
    country = str(signal.get("country") or "").upper()
    if country in DEFAULT_BENCHMARK_BY_COUNTRY:
        return DEFAULT_BENCHMARK_BY_COUNTRY[country]
    symbol = str(signal.get("symbol") or "")
    if symbol.startswith(("0", "3", "6")) and len(symbol) == 6:
        return DEFAULT_BENCHMARK_BY_COUNTRY["CN"]
    return None


def _benchmark_return_for_horizon(benchmark_prices: pd.DataFrame | None, horizon_days: int) -> float | None:
    if benchmark_prices is None or benchmark_prices.empty:
        return None
    is_base = benchmark_prices["is_base"].astype(bool)
    base_rows = benchmark_prices[is_base]
    future_rows = benchmark_prices[~is_base]
    if base_rows.empty or len(future_rows) < horizon_days:
        return None
    base_price = float(base_rows.iloc[0]["close"])
    outcome_price = float(future_rows.iloc[horizon_days - 1]["close"])
    if base_price <= 0 or outcome_price <= 0:
        return None
    return outcome_price / base_price - 1


def _forward_return(side: str, execution_price: float, outcome_price: float) -> float:
    if side in {"SELL", "SHORT"}:
        return execution_price / outcome_price - 1
    return outcome_price / execution_price - 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update realized signal outcome table")
    sub = parser.add_subparsers(dest="command", required=True)
    p_update = sub.add_parser("update", help="更新已成交信号的 T+N 收益")
    p_update.add_argument("--horizons", default="1,5,20", help="逗号分隔的交易日 horizon，例如 1,5,20")
    p_update.add_argument("--as-of", default=None, help="只使用该日期及以前的行情，YYYY-MM-DD")
    args = parser.parse_args(argv)

    if args.command == "update":
        from src.data_pipeline.loader import get_connection, init_db

        horizons = tuple(int(part.strip()) for part in args.horizons.split(",") if part.strip())
        as_of = pd.to_datetime(args.as_of).date() if args.as_of else None
        conn = get_connection()
        try:
            init_db(conn)
            result = update_signal_outcomes(conn, horizons=horizons, as_of=as_of)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            conn.close()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
