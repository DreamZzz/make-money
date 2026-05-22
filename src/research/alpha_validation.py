"""Generic research-only validation helpers for cross-sectional alpha scores."""
from __future__ import annotations

import itertools
import math
from typing import Any

import pandas as pd

from src.backtest.qlib_runner import (
    align_benchmark_to_strategy_periods,
    compute_periodic_metrics,
    simulate_topn_open,
)
from src.research.alpha_gate import evaluate_alpha_gate
from src.research.strategies.value_quality import measure_return_correlation

PREDICTION_COLUMNS = ["datetime", "instrument", "score"]
SUPPORTED_CANDIDATES = {
    "cross_reversal",
    "industry_relative_momentum",
    "low_vol",
}


def load_research_price_panel(
    conn: Any,
    *,
    start: str,
    end: str,
    country: str = "CN",
) -> pd.DataFrame:
    """Load the shared research price panel for cross-sectional alpha candidates."""
    df = conn.execute(
        """
        SELECT
            dp.trade_date,
            dp.symbol,
            dp.open,
            dp.high,
            dp.low,
            dp.close,
            dp.pre_close,
            dp.volume,
            dp.amount,
            dp.turnover_rate,
            dp.pe_ttm,
            dp.pb,
            dp.is_st,
            dp.is_suspended,
            si.name,
            si.industry,
            si.sector,
            si.market_cap
        FROM daily_price dp
        JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country = ?
          AND dp.trade_date >= ?
          AND dp.trade_date <= ?
        ORDER BY dp.trade_date, dp.symbol
        """,
        [str(country), pd.to_datetime(start).date(), pd.to_datetime(end).date()],
    ).fetchdf()
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str)
    return df


def score_panel_to_predictions(scores: pd.DataFrame) -> pd.DataFrame:
    """Map a research score panel into the Top-N simulator prediction shape."""
    if scores.empty or not {"trade_date", "symbol", "score"}.issubset(scores.columns):
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    out = pd.DataFrame({
        "datetime": pd.to_datetime(scores["trade_date"], errors="coerce"),
        "instrument": scores["symbol"].astype(str),
        "score": pd.to_numeric(scores["score"], errors="coerce"),
    })
    return (
        out.dropna(subset=["datetime", "instrument", "score"])
        .sort_values(["datetime", "instrument"])
        .reset_index(drop=True)[PREDICTION_COLUMNS]
    )


def run_score_panel_validation(
    *,
    strategy_name: str,
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int = 20,
    buffer_n: int | None = None,
    max_replacements_per_rebalance: int | None = None,
    holding_days: int = 20,
    rebalance_freq: str = "monthly",
    benchmark_name: str = "MIXED_EQUAL",
    benchmark_returns: pd.Series | None = None,
    reference_returns: pd.Series | None = None,
    factor_coverage: float | None = None,
    market: str = "CN",
) -> dict[str, Any]:
    """Validate a research-only score panel with the shared alpha gate."""
    pred = score_panel_to_predictions(scores)
    returns = simulate_topn_open(
        pred,
        prices,
        top_n=int(top_n),
        buffer_n=buffer_n,
        max_replacements_per_rebalance=max_replacements_per_rebalance,
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
        market=market,
    )
    periods_per_year = int(returns.attrs.get("periods_per_year") or _periods_per_year(rebalance_freq))
    aligned_benchmark = align_benchmark_to_strategy_periods(
        pd.Series(benchmark_returns) if benchmark_returns is not None else pd.Series(dtype=float),
        pd.DatetimeIndex(returns.index),
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
    )
    metrics = compute_periodic_metrics(
        returns,
        benchmark_returns=aligned_benchmark,
        periods_per_year=periods_per_year,
        turnover=returns.attrs.get("turnover"),
    )
    aligned_reference = align_benchmark_to_strategy_periods(
        pd.Series(reference_returns) if reference_returns is not None else pd.Series(dtype=float),
        pd.DatetimeIndex(returns.index),
        holding_days=int(holding_days),
        rebalance_freq=rebalance_freq,
    )
    coverage = _factor_coverage(scores, factor_coverage)
    gate_metrics = {
        "information_ratio": metrics.get("info_ratio") if metrics else None,
        "correlation_alpha158": measure_return_correlation(returns, aligned_reference),
        "correlation_benchmark": measure_return_correlation(returns, aligned_benchmark),
        "max_drawdown": metrics.get("max_drawdown") if metrics else None,
        "annual_turnover": metrics.get("turnover") if metrics else None,
        "factor_coverage": coverage,
    }
    gate = evaluate_alpha_gate(gate_metrics)
    return {
        "strategy_name": str(strategy_name),
        "decision_scope": "research_only",
        "score_rows": int(len(scores)),
        "score_dates": int(pd.to_datetime(scores["trade_date"]).nunique()) if "trade_date" in scores.columns else 0,
        "avg_score_coverage": coverage,
        "return_periods": int(len(returns)),
        "top_n": int(top_n),
        "buffer_n": int(buffer_n) if buffer_n is not None else None,
        "max_replacements_per_rebalance": (
            int(max_replacements_per_rebalance)
            if max_replacements_per_rebalance is not None else None
        ),
        "holding_days": int(holding_days),
        "rebalance_freq": rebalance_freq,
        "benchmark_name": benchmark_name,
        "reference_periods": int(len(aligned_reference)),
        "correlation_alpha158": gate_metrics["correlation_alpha158"],
        "correlation_benchmark": gate_metrics["correlation_benchmark"],
        "alpha_gate_passed": gate.passed,
        "alpha_gate_failed_reasons": gate.failed_reasons,
        "alpha_gate_metrics": gate.metrics,
        "metrics": metrics,
    }


def run_research_candidate_validation(
    conn: Any,
    *,
    candidate: str,
    start: str,
    end: str,
    country: str = "CN",
    top_n: int = 20,
    buffer_n: int | None = None,
    max_replacements_per_rebalance: int | None = None,
    holding_days: int = 20,
    rebalance_freq: str = "monthly",
    benchmark_name: str = "MIXED_EQUAL",
    benchmark_returns: pd.Series | None = None,
    reference_returns: pd.Series | None = None,
    factor_coverage: float | None = None,
    score_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load prices, build a candidate score panel, and validate it with the shared gate."""
    prices = load_research_price_panel(conn, start=start, end=end, country=country)
    scores = build_candidate_scores(candidate, prices, **(score_kwargs or {}))
    return run_score_panel_validation(
        strategy_name=candidate,
        scores=scores,
        prices=prices,
        top_n=top_n,
        buffer_n=buffer_n,
        max_replacements_per_rebalance=max_replacements_per_rebalance,
        holding_days=holding_days,
        rebalance_freq=rebalance_freq,
        benchmark_name=benchmark_name,
        benchmark_returns=benchmark_returns,
        reference_returns=reference_returns,
        factor_coverage=factor_coverage,
        market=country,
    )


def run_research_candidate_grid(
    conn: Any,
    *,
    candidate: str,
    start: str,
    end: str,
    country: str = "CN",
    lookbacks: list[int | None] | tuple[int | None, ...] = (None,),
    smooth_days_list: list[int | None] | tuple[int | None, ...] = (None,),
    size_neutral_options: list[bool] | tuple[bool, ...] = (False,),
    beta_neutral_options: list[bool] | tuple[bool, ...] = (False,),
    beta_lookbacks: list[int | None] | tuple[int | None, ...] = (None,),
    top_ns: list[int] | tuple[int, ...] = (20,),
    buffer_ns: list[int | None] | tuple[int | None, ...] = (None,),
    max_replacements_list: list[int | None] | tuple[int | None, ...] = (None,),
    holding_days_list: list[int] | tuple[int, ...] = (20,),
    rebalance_freqs: list[str] | tuple[str, ...] = ("monthly",),
    benchmark_name: str = "MIXED_EQUAL",
    benchmark_returns: pd.Series | None = None,
    reference_returns: pd.Series | None = None,
    factor_coverage: float | None = None,
) -> list[dict[str, Any]]:
    """Run a research-only parameter grid and rank results by gate pass and IR."""
    prices = load_research_price_panel(conn, start=start, end=end, country=country)
    score_cache: dict[tuple[tuple[str, Any], ...], pd.DataFrame] = {}
    seen_grid_keys: set[tuple[Any, ...]] = set()
    results = []
    for lookback, smooth_days, size_neutral, beta_neutral, beta_lookback, top_n, buffer_n, max_replacements, holding_days, rebalance_freq in itertools.product(
        lookbacks,
        smooth_days_list,
        size_neutral_options,
        beta_neutral_options,
        beta_lookbacks,
        top_ns,
        buffer_ns,
        max_replacements_list,
        holding_days_list,
        rebalance_freqs,
    ):
        score_kwargs = {}
        if lookback is not None:
            score_kwargs["lookback"] = int(lookback)
        if smooth_days is not None:
            score_kwargs["smooth_days"] = int(smooth_days)
        if size_neutral:
            score_kwargs["size_neutral"] = True
        if beta_neutral:
            score_kwargs["beta_neutral"] = True
            if beta_lookback is not None:
                score_kwargs["beta_lookback"] = int(beta_lookback)
        grid_key = (
            tuple(sorted(score_kwargs.items())),
            int(top_n),
            buffer_n,
            max_replacements,
            int(holding_days),
            rebalance_freq,
        )
        if grid_key in seen_grid_keys:
            continue
        seen_grid_keys.add(grid_key)
        cache_key = tuple(sorted(score_kwargs.items()))
        if cache_key not in score_cache:
            score_cache[cache_key] = build_candidate_scores(candidate, prices, **score_kwargs)
        strategy_name = _strategy_variant_name(candidate, score_kwargs)
        result = run_score_panel_validation(
            strategy_name=strategy_name,
            scores=score_cache[cache_key],
            prices=prices,
            top_n=int(top_n),
            buffer_n=buffer_n,
            max_replacements_per_rebalance=max_replacements,
            holding_days=int(holding_days),
            rebalance_freq=rebalance_freq,
            benchmark_name=benchmark_name,
            benchmark_returns=benchmark_returns,
            reference_returns=reference_returns,
            factor_coverage=factor_coverage,
            market=country,
        )
        result["candidate"] = candidate
        result["score_kwargs"] = score_kwargs
        results.append(result)
    return sorted(results, key=_grid_sort_key)


def build_candidate_scores(
    candidate: str,
    prices: pd.DataFrame,
    **kwargs: Any,
) -> pd.DataFrame:
    """Build a research score panel for a supported candidate strategy."""
    name = str(candidate)
    if name == "industry_relative_momentum":
        from src.research.strategies.industry_relative_momentum import (
            compute_industry_momentum_scores,
        )

        return compute_industry_momentum_scores(prices, **kwargs)
    if name == "low_vol":
        from src.research.strategies.low_vol import compute_low_vol_scores

        return compute_low_vol_scores(prices, **kwargs)
    if name == "cross_reversal":
        from src.research.strategies.cross_reversal import compute_cross_reversal_scores

        return compute_cross_reversal_scores(prices, **kwargs)
    raise ValueError(
        "Unsupported research alpha candidate "
        f"{candidate!r}; supported={sorted(SUPPORTED_CANDIDATES)}"
    )


def _factor_coverage(scores: pd.DataFrame, override: float | None) -> float:
    if override is not None:
        return float(override)
    if scores.empty:
        return 0.0
    if "coverage" in scores.columns:
        coverage = pd.to_numeric(scores["coverage"], errors="coerce").dropna()
        if not coverage.empty:
            return float(coverage.mean())
    return float(scores["score"].notna().mean()) if "score" in scores.columns else 0.0


def _periods_per_year(rebalance_freq: str) -> int:
    if rebalance_freq == "daily":
        return 252
    if rebalance_freq == "weekly":
        return 52
    if rebalance_freq == "monthly":
        return 12
    if rebalance_freq == "quarterly":
        return 4
    raise ValueError("rebalance_freq must be daily, weekly, monthly, or quarterly")


def _metric_value(result: dict[str, Any], key: str, default: float) -> float:
    value = (result.get("alpha_gate_metrics") or {}).get(key)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _grid_sort_key(result: dict[str, Any]) -> tuple[int, float, float, float]:
    passed_rank = 0 if result.get("alpha_gate_passed") else 1
    information_ratio = _metric_value(result, "information_ratio", -math.inf)
    turnover = _metric_value(result, "annual_turnover", math.inf)
    corr_alpha158 = _metric_value(result, "correlation_alpha158", math.inf)
    return (passed_rank, -information_ratio, turnover, corr_alpha158)


def _strategy_variant_name(candidate: str, score_kwargs: dict[str, Any]) -> str:
    lookback = score_kwargs.get("lookback")
    smooth_days = score_kwargs.get("smooth_days")
    size_neutral = score_kwargs.get("size_neutral")
    parts = [str(candidate)]
    if lookback is not None:
        parts.append(str(lookback))
    if smooth_days not in (None, 1):
        parts.append(f"smooth{smooth_days}")
    if size_neutral:
        parts.append("size_neutral")
    if score_kwargs.get("beta_neutral"):
        beta_lookback = score_kwargs.get("beta_lookback")
        parts.append(f"beta{beta_lookback}" if beta_lookback is not None else "beta_neutral")
    return "_".join(parts)
