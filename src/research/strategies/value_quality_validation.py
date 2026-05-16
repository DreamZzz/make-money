"""Standalone validation helpers for the value-quality research factor."""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from src.backtest.qlib_runner import (
    align_benchmark_to_strategy_periods,
    compute_periodic_metrics,
    simulate_topn_open,
)
from src.research.strategies.value_quality import (
    MODEL_NAME,
    compute_value_quality_scores,
    measure_return_correlation,
)


def build_rebalance_dates(
    conn: Any,
    start: str | date,
    end: str | date,
    rebalance_freq: str = "monthly",
) -> list[date]:
    """Return the last available CN trade date for each rebalance period."""
    freq = _period_freq(rebalance_freq)
    df = conn.execute(
        """
        SELECT DISTINCT dp.trade_date
        FROM daily_price dp
        JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country = 'CN'
          AND dp.trade_date >= ?
          AND dp.trade_date <= ?
        ORDER BY dp.trade_date
        """,
        [pd.to_datetime(start).date(), pd.to_datetime(end).date()],
    ).fetchdf()
    if df.empty:
        return []
    dates = pd.to_datetime(df["trade_date"]).sort_values()
    if rebalance_freq == "daily":
        return [ts.date() for ts in dates]
    selected = pd.DataFrame({"trade_date": dates})
    selected["period"] = selected["trade_date"].dt.to_period(freq)
    last = selected.groupby("period", sort=True)["trade_date"].max()
    return [ts.date() for ts in last]


def build_value_quality_score_panel(
    conn: Any,
    start: str | date,
    end: str | date,
    rebalance_freq: str = "monthly",
    financial_lag_days: int = 60,
    country: str = "CN",
) -> pd.DataFrame:
    """Build point-in-time value-quality scores for each rebalance date."""
    frames = []
    for rebalance_date in build_rebalance_dates(conn, start=start, end=end, rebalance_freq=rebalance_freq):
        financial_as_of = rebalance_date - timedelta(days=max(int(financial_lag_days), 0))
        fundamentals = _load_fundamentals_snapshot(
            conn,
            price_as_of=rebalance_date,
            financial_as_of=financial_as_of,
            country=country,
        )
        if fundamentals.empty:
            continue
        scored = compute_value_quality_scores(fundamentals)
        if scored.empty:
            continue
        scored["trade_date"] = rebalance_date
        frames.append(scored)
    return pd.concat(frames, ignore_index=True) if frames else compute_value_quality_scores(pd.DataFrame())


def run_value_quality_validation(
    conn: Any,
    start: str | date = "2022-01-01",
    end: str | date = "2025-12-31",
    top_n: int = 20,
    holding_days: int = 20,
    rebalance_freq: str = "monthly",
    financial_lag_days: int = 60,
    benchmark_name: str = "MIXED_EQUAL",
    benchmark_returns: pd.Series | None = None,
    reference_returns: pd.Series | None = None,
    save_result: bool = False,
) -> dict[str, Any]:
    """Run standalone value-quality validation and return metrics plus correlations."""
    scores = build_value_quality_score_panel(
        conn,
        start=start,
        end=end,
        rebalance_freq=rebalance_freq,
        financial_lag_days=financial_lag_days,
    )
    pred = _scores_to_prediction_frame(scores)
    price_end = pd.to_datetime(end).date() + timedelta(days=max(90, int(holding_days) * 5))
    prices = _load_price_frame(
        conn,
        symbols=sorted(pred["instrument"].astype(str).unique().tolist()) if not pred.empty else [],
        start=pd.to_datetime(start).date(),
        end=price_end,
    )
    returns = simulate_topn_open(
        pred,
        prices,
        top_n=int(top_n),
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
        market="CN",
    )

    benchmark = benchmark_returns
    if benchmark is None:
        benchmark = _load_benchmark_suite(conn).get(benchmark_name, pd.Series(dtype=float))
    aligned_benchmark = align_benchmark_to_strategy_periods(
        pd.Series(benchmark) if benchmark is not None else pd.Series(dtype=float),
        pd.DatetimeIndex(returns.index),
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
    )
    periods_per_year = int(returns.attrs.get("periods_per_year") or _periods_per_year(rebalance_freq))
    metrics = compute_periodic_metrics(
        returns,
        benchmark_returns=aligned_benchmark,
        periods_per_year=periods_per_year,
        turnover=returns.attrs.get("turnover"),
    )

    reference = reference_returns
    reference_experiment_id = None
    if reference is None:
        reference, reference_experiment_id = load_latest_alpha158_portfolio_returns(conn, start=start, end=end)
    aligned_reference = align_benchmark_to_strategy_periods(
        pd.Series(reference) if reference is not None else pd.Series(dtype=float),
        pd.DatetimeIndex(returns.index),
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
    )

    result = {
        "strategy_name": MODEL_NAME,
        "score_rows": int(len(scores)),
        "score_dates": int(scores["trade_date"].nunique()) if not scores.empty else 0,
        "avg_score_coverage": float(scores["coverage"].mean()) if not scores.empty else 0.0,
        "return_periods": int(len(returns)),
        "top_n": int(top_n),
        "holding_days": int(holding_days),
        "rebalance_freq": rebalance_freq,
        "financial_lag_days": int(financial_lag_days),
        "benchmark_name": benchmark_name,
        "reference_experiment_id": reference_experiment_id,
        "reference_periods": int(len(aligned_reference)),
        "correlation_alpha158": measure_return_correlation(returns, aligned_reference),
        "correlation_benchmark": measure_return_correlation(returns, aligned_benchmark),
        "metrics": metrics,
    }

    if save_result and metrics:
        result["run_id"] = _save_backtest_result(
            conn,
            strategy_name=MODEL_NAME,
            market="CN",
            metrics=metrics,
            config_snapshot={
                "top_n": top_n,
                "holding_days": holding_days,
                "rebalance_freq": rebalance_freq,
                "financial_lag_days": financial_lag_days,
                "benchmark_name": benchmark_name,
                "reference_experiment_id": reference_experiment_id,
            },
            engine="value_quality_validation",
            decision_scope="research_only",
        )
    return result


def load_latest_alpha158_portfolio_returns(
    conn: Any,
    start: str | date,
    end: str | date,
) -> tuple[pd.Series, str | None]:
    """Load the latest Alpha158 portfolio_return series for correlation checks."""
    row = conn.execute(
        """
        SELECT e.experiment_id
        FROM qlib_experiments e
        JOIN qlib_daily_metrics m ON e.experiment_id = m.experiment_id
        WHERE e.model_name = 'alpha158'
          AND e.status = 'SUCCEEDED'
          AND m.portfolio_return IS NOT NULL
        GROUP BY e.experiment_id, e.ended_at
        ORDER BY e.ended_at DESC NULLS LAST
        LIMIT 1
        """,
    ).fetchone()
    if not row:
        return pd.Series(dtype=float), None
    experiment_id = row[0]
    df = conn.execute(
        """
        SELECT metric_date, portfolio_return
        FROM qlib_daily_metrics
        WHERE experiment_id = ?
          AND metric_date >= ?
          AND metric_date <= ?
          AND portfolio_return IS NOT NULL
        ORDER BY metric_date
        """,
        [experiment_id, pd.to_datetime(start).date(), pd.to_datetime(end).date()],
    ).fetchdf()
    if df.empty:
        return pd.Series(dtype=float), experiment_id
    df["metric_date"] = pd.to_datetime(df["metric_date"])
    return df.set_index("metric_date")["portfolio_return"].astype(float), experiment_id


def _load_fundamentals_snapshot(
    conn: Any,
    price_as_of: date,
    financial_as_of: date,
    country: str = "CN",
) -> pd.DataFrame:
    df = conn.execute(
        """
        WITH latest_price AS (
            SELECT symbol, trade_date, pe_ttm, pb
            FROM daily_price
            WHERE trade_date <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        ),
        latest_financials AS (
            SELECT symbol, report_date, roe, net_margin, debt_ratio
            FROM financials
            WHERE report_date <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY report_date DESC
            ) = 1
        )
        SELECT
            ? AS trade_date,
            si.symbol,
            lp.pe_ttm,
            lp.pb,
            lf.roe,
            lf.net_margin,
            lf.debt_ratio,
            si.market_cap
        FROM stock_info si
        LEFT JOIN latest_price lp ON si.symbol = lp.symbol
        LEFT JOIN latest_financials lf ON si.symbol = lf.symbol
        WHERE si.country = ?
          AND lp.symbol IS NOT NULL
        ORDER BY si.symbol
        """,
        [price_as_of, financial_as_of, price_as_of, country],
    ).fetchdf()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _scores_to_prediction_frame(scores: pd.DataFrame) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame(columns=["datetime", "instrument", "score"])
    return pd.DataFrame({
        "datetime": pd.to_datetime(scores["trade_date"]),
        "instrument": scores["symbol"].astype(str),
        "score": pd.to_numeric(scores["score"], errors="coerce").fillna(0.0),
    })


def _load_price_frame(conn: Any, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    ph = ",".join(["?"] * len(symbols))
    prices = conn.execute(
        f"""
        SELECT symbol, trade_date, open, close, pre_close, pe_ttm, pb, is_st, is_suspended
        FROM daily_price
        WHERE symbol IN ({ph})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY trade_date, symbol
        """,
        [*symbols, start, end],
    ).fetchdf()
    if not prices.empty:
        prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    return prices


def _load_benchmark_suite(conn: Any) -> dict[str, pd.Series]:
    bench_300 = _load_benchmark_returns(conn, "000300")
    bench_500 = _load_benchmark_returns(conn, "000905")
    all_proxy = _load_all_stock_equal_weight_returns(conn)
    suite = {
        "000300": bench_300,
        "000905": bench_500,
        "ALL_EQ_PROXY": all_proxy,
    }
    common = bench_300.index.intersection(bench_500.index).intersection(all_proxy.index)
    if len(common) > 1:
        suite["MIXED_EQUAL"] = pd.concat(
            [bench_300.loc[common], bench_500.loc[common], all_proxy.loc[common]],
            axis=1,
        ).mean(axis=1)
    return {name: returns.dropna() for name, returns in suite.items() if returns is not None and not returns.empty}


def _load_benchmark_returns(conn: Any, index_code: str) -> pd.Series:
    df = conn.execute(
        """
        SELECT trade_date, close
        FROM index_daily
        WHERE index_code = ?
        ORDER BY trade_date
        """,
        [index_code],
    ).fetchdf()
    if df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")["close"].pct_change().dropna()


def _load_all_stock_equal_weight_returns(conn: Any) -> pd.Series:
    df = conn.execute(
        """
        SELECT dp.trade_date, dp.symbol, dp.close
        FROM daily_price dp
        JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country = 'CN'
        ORDER BY dp.trade_date, dp.symbol
        """,
    ).fetchdf()
    if df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close = df.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    return close.pct_change(fill_method=None).mean(axis=1).dropna()


def _save_backtest_result(
    conn: Any,
    strategy_name: str,
    market: str,
    metrics: dict[str, Any],
    config_snapshot: dict[str, Any] | None = None,
    engine: str = "value_quality_validation",
    decision_scope: str = "research_only",
) -> str:
    if not metrics:
        raise ValueError("Cannot save empty backtest metrics")
    run_id = f"BT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    row = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "market": market,
        "engine": engine,
        "decision_scope": decision_scope,
        "config_snapshot": json.dumps(config_snapshot or {}, ensure_ascii=False, default=str),
        **metrics,
    }
    cols = [
        "run_id", "strategy_name", "market", "engine", "decision_scope", "start_date", "end_date",
        "annual_return", "cumulative_return", "annual_volatility",
        "sharpe_ratio", "sortino_ratio", "max_drawdown",
        "max_drawdown_days", "win_rate", "avg_win_loss", "turnover",
        "info_ratio", "benchmark_return", "excess_return", "config_snapshot",
    ]
    conn.register("_tmp_value_quality_backtest_result_df", pd.DataFrame([{col: row.get(col) for col in cols}]))
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE _tmp_value_quality_backtest_result "
        "AS SELECT * FROM _tmp_value_quality_backtest_result_df",
    )
    conn.execute(f"""
        INSERT OR REPLACE INTO backtest_results ({", ".join(cols)})
        SELECT {", ".join(cols)} FROM _tmp_value_quality_backtest_result
    """)
    return run_id


def _period_freq(rebalance_freq: str) -> str:
    if rebalance_freq == "daily":
        return "D"
    if rebalance_freq == "weekly":
        return "W-FRI"
    if rebalance_freq == "monthly":
        return "M"
    raise ValueError("rebalance_freq must be daily, weekly, or monthly")


def _periods_per_year(rebalance_freq: str) -> int:
    return {"daily": 252, "weekly": 52, "monthly": 12}[rebalance_freq]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the research-only value-quality factor.")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--rebalance-freq", choices=["daily", "weekly", "monthly"], default="monthly")
    parser.add_argument("--financial-lag-days", type=int, default=60)
    parser.add_argument("--benchmark", default="MIXED_EQUAL")
    parser.add_argument("--save", action="store_true", help="Persist metrics to backtest_results as research_only")
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        result = run_value_quality_validation(
            conn,
            start=args.start,
            end=args.end,
            top_n=args.top_n,
            holding_days=args.holding_days,
            rebalance_freq=args.rebalance_freq,
            financial_lag_days=args.financial_lag_days,
            benchmark_name=args.benchmark,
            save_result=args.save,
        )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("metrics") else 1


if __name__ == "__main__":
    raise SystemExit(main())
