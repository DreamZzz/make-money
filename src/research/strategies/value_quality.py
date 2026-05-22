"""Value-quality fundamental factor prototype.

The module is intentionally research-only for now. It builds a cross-sectional
score from valuation and quality fields already present in the local DuckDB
schema, then can emit standard BUY signal rows or simulate simple Top-N returns.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

MODEL_NAME = "value_quality"
MODEL_VERSION = "0.2"

VALUE_COLUMNS = ("pe_ttm", "pb")
QUALITY_HIGH_COLUMNS = ("roe", "net_margin")
QUALITY_LOW_COLUMNS = ("debt_ratio",)


def compute_value_quality_scores(
    fundamentals: pd.DataFrame,
    value_weight: float = 0.45,
    quality_weight: float = 0.45,
    liquidity_weight: float = 0.10,
    industry_neutral: bool = False,
) -> pd.DataFrame:
    """Compute cross-sectional value-quality scores in [0, 1]."""
    if fundamentals.empty or "symbol" not in fundamentals.columns:
        return _empty_score_frame()

    df = fundamentals.copy()
    if "trade_date" not in df.columns:
        df["trade_date"] = pd.NaT
    if industry_neutral and "industry" not in df.columns:
        df["industry"] = "unknown"
    if "industry" in df.columns:
        df["industry"] = df["industry"].fillna("unknown").astype(str)
    for col in [*VALUE_COLUMNS, *QUALITY_HIGH_COLUMNS, *QUALITY_LOW_COLUMNS, "market_cap"]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    value_parts = [
        _factor_rank(df, col, higher_is_better=False, positive_only=True, industry_neutral=industry_neutral)
        for col in VALUE_COLUMNS
    ]
    quality_parts = [
        _factor_rank(df, col, higher_is_better=True, positive_only=False, industry_neutral=industry_neutral)
        for col in QUALITY_HIGH_COLUMNS
    ] + [
        _factor_rank(df, col, higher_is_better=False, positive_only=True, industry_neutral=industry_neutral)
        for col in QUALITY_LOW_COLUMNS
    ]

    df["value_score"] = _mean_score(value_parts)
    df["quality_score"] = _mean_score(quality_parts)
    df["liquidity_score"] = _rank_score(df["market_cap"], higher_is_better=True, positive_only=True)
    df["coverage"] = _coverage(df)

    total_weight = value_weight + quality_weight + liquidity_weight
    if total_weight <= 0:
        value_weight, quality_weight, liquidity_weight = 0.45, 0.45, 0.10
        total_weight = 1.0
    score = (
        df["value_score"] * value_weight
        + df["quality_score"] * quality_weight
        + df["liquidity_score"] * liquidity_weight
    ) / total_weight
    df["score"] = score.clip(0.0, 1.0).fillna(0.0)

    columns = [
        "trade_date", "symbol", "score", "value_score", "quality_score",
        "liquidity_score", "coverage", "pe_ttm", "pb", "roe", "net_margin",
        "debt_ratio", "market_cap",
    ]
    if "industry" in df.columns:
        columns.insert(2, "industry")
    return (
        df[columns]
        .sort_values(["trade_date", "score", "symbol"], ascending=[True, False, True])
        .reset_index(drop=True)
    )


def industry_neutral_rank(df: pd.DataFrame, column: str, ascending: bool) -> pd.Series:
    """Rank a factor within industries while preserving the original row index."""
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    if "industry" not in df.columns:
        working = df.assign(industry="unknown")
    else:
        working = df.copy()
        working["industry"] = working["industry"].fillna("unknown").astype(str)
    return working.groupby("industry", dropna=False)[column].rank(pct=True, ascending=ascending)


def select_value_quality_symbols(
    scores: pd.DataFrame,
    top_n: int = 20,
    prior_symbols: set[str] | None = None,
    retention_quantile: float = 0.30,
) -> list[str]:
    """Select Top-N symbols with a score buffer for existing research holdings."""
    if scores.empty or "symbol" not in scores.columns or "score" not in scores.columns:
        return []
    top_n = max(int(top_n or 1), 1)
    prior_symbols = {str(symbol) for symbol in (prior_symbols or set())}
    ranked = scores.copy()
    ranked["symbol"] = ranked["symbol"].astype(str)
    ranked["score"] = pd.to_numeric(ranked["score"], errors="coerce").fillna(0.0)
    ranked = ranked.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)

    cutoff_idx = min(top_n, len(ranked)) - 1
    cutoff_score = float(ranked.iloc[cutoff_idx]["score"]) if cutoff_idx >= 0 else 0.0
    retention_floor = cutoff_score * (1.0 - min(max(float(retention_quantile), 0.0), 1.0))
    retained = ranked[
        ranked["symbol"].isin(prior_symbols)
        & (ranked["score"] >= retention_floor)
    ]["symbol"].tolist()
    retained_set = set(retained)
    fresh = ranked[~ranked["symbol"].isin(retained_set)]["symbol"].tolist()
    selected = fresh[: max(top_n - len(retained), 0)] + retained
    return selected[:top_n]


def generate_signals(
    scored: pd.DataFrame,
    top_n: int = 20,
    min_score: float = 0.60,
    expected_holding_days: int = 20,
) -> pd.DataFrame:
    """Convert scored rows to standard research signal rows."""
    if scored.empty:
        return pd.DataFrame()
    df = scored.copy()
    if "trade_date" not in df.columns:
        df["trade_date"] = pd.NaT
    latest_date = pd.to_datetime(df["trade_date"]).max()
    if pd.notna(latest_date):
        df = df[pd.to_datetime(df["trade_date"]) == latest_date].copy()
    df = df[pd.to_numeric(df["score"], errors="coerce").fillna(0.0) >= float(min_score)].copy()
    if df.empty:
        return pd.DataFrame()

    top_n = max(int(top_n or 1), 1)
    df = df.sort_values(["score", "symbol"], ascending=[False, True]).head(top_n).copy()
    trade_date = pd.to_datetime(df["trade_date"]).dt.date
    signals = pd.DataFrame({
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "symbol": df["symbol"].astype(str).values,
        "signal_ts": trade_date,
        "trade_date": trade_date,
        "horizon": f"{int(expected_holding_days)}d",
        "score": df["score"].clip(0.0, 1.0).values,
        "side": "BUY",
        "confidence": df["score"].clip(0.0, 1.0).values,
        "expected_holding_days": max(int(expected_holding_days or 1), 1),
        "max_position_pct": 0.06,
        "thesis": [
            (
                f"Value-quality score {row.score:.2f}; "
                f"value {row.value_score:.2f}; quality {row.quality_score:.2f}; "
                f"coverage {row.coverage:.0%}"
            )
            for row in df.itertuples(index=False)
        ],
        "risk_tags": [["value_quality", "fundamental", "research_only"]] * len(df),
    })
    return signals.reset_index(drop=True)


def simulate_topn_equal_weight_returns(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int = 20,
) -> pd.Series:
    """Simple research backtest: hold latest Top-N score basket equal-weight."""
    if scores.empty or prices.empty:
        return pd.Series(dtype=float)
    score_df = scores.copy()
    score_df["trade_date"] = pd.to_datetime(score_df["trade_date"])
    score_df["score"] = pd.to_numeric(score_df["score"], errors="coerce").fillna(0.0)
    score_dates = sorted(score_df["trade_date"].dropna().unique())
    if not score_dates:
        return pd.Series(dtype=float)

    close = prices.copy()
    close.index = pd.to_datetime(close.index)
    returns = close.pct_change().iloc[1:]
    out = []
    top_n = max(int(top_n or 1), 1)
    for dt, row in returns.iterrows():
        eligible_dates = [score_date for score_date in score_dates if score_date < dt]
        if not eligible_dates:
            continue
        latest_score_date = eligible_dates[-1]
        selected = (
            score_df[score_df["trade_date"] == latest_score_date]
            .sort_values(["score", "symbol"], ascending=[False, True])
            .head(top_n)["symbol"]
            .astype(str)
            .tolist()
        )
        available = [symbol for symbol in selected if symbol in row.index and pd.notna(row[symbol])]
        if not available:
            out.append((dt, 0.0))
            continue
        out.append((dt, float(row[available].mean())))
    if not out:
        return pd.Series(dtype=float)
    idx, vals = zip(*out)
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name=MODEL_NAME)


def measure_return_correlation(value_returns: pd.Series, reference_returns: pd.Series) -> float:
    """Return Pearson correlation between aligned return series."""
    if value_returns.empty or reference_returns.empty:
        return 0.0
    aligned = pd.concat([
        pd.Series(value_returns, name="value_quality"),
        pd.Series(reference_returns, name="reference"),
    ], axis=1, sort=False).dropna()
    if len(aligned) < 2:
        return 0.0
    corr = aligned["value_quality"].corr(aligned["reference"])
    return 0.0 if pd.isna(corr) else float(corr)


def load_fundamentals_snapshot(conn: Any, as_of: date | None = None, country: str = "CN") -> pd.DataFrame:
    """Load latest local valuation and quality fields for a research snapshot."""
    params: list[Any] = []
    date_filter = ""
    fin_date_filter = ""
    if as_of is not None:
        date_filter = "AND trade_date <= ?"
        fin_date_filter = "AND report_date <= ?"
        params.extend([as_of, as_of])
    params.append(country)
    df = conn.execute(f"""
        WITH latest_price AS (
            SELECT symbol, trade_date, pe_ttm, pb
            FROM daily_price
            WHERE 1 = 1
              {date_filter}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        ),
        latest_financials AS (
            SELECT symbol, report_date, roe, net_margin, debt_ratio
            FROM financials
            WHERE 1 = 1
              {fin_date_filter}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY report_date DESC
            ) = 1
        )
        SELECT
            COALESCE(lp.trade_date, CURRENT_DATE) AS trade_date,
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
    """, params).fetchdf()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def _rank_score(series: pd.Series, higher_is_better: bool, positive_only: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    valid = values.notna()
    if positive_only:
        valid &= values > 0
    score = pd.Series(0.5, index=series.index, dtype=float)
    if valid.sum() == 0:
        return score
    score.loc[valid] = values.loc[valid].rank(
        pct=True,
        ascending=higher_is_better,
        method="average",
    )
    return score.clip(0.0, 1.0)


def _factor_rank(
    df: pd.DataFrame,
    column: str,
    higher_is_better: bool,
    positive_only: bool,
    industry_neutral: bool,
) -> pd.Series:
    if not industry_neutral:
        return _rank_score(df[column], higher_is_better=higher_is_better, positive_only=positive_only)
    values = pd.to_numeric(df[column], errors="coerce")
    valid = values.notna()
    if positive_only:
        valid &= values > 0
    rank_input = df.copy()
    rank_input[column] = values.where(valid)
    score = industry_neutral_rank(rank_input, column, ascending=higher_is_better)
    return score.fillna(0.5).clip(0.0, 1.0)


def _mean_score(parts: list[pd.Series]) -> pd.Series:
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).mean(axis=1).fillna(0.5).clip(0.0, 1.0)


def _coverage(df: pd.DataFrame) -> pd.Series:
    cols = [*VALUE_COLUMNS, *QUALITY_HIGH_COLUMNS, *QUALITY_LOW_COLUMNS, "market_cap"]
    valid = pd.DataFrame(index=df.index)
    for col in cols:
        values = pd.to_numeric(df[col], errors="coerce")
        valid[col] = values.notna()
        if col in VALUE_COLUMNS or col in QUALITY_LOW_COLUMNS or col == "market_cap":
            valid[col] &= values > 0
    return valid.mean(axis=1).astype(float)


def _empty_score_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "trade_date", "symbol", "score", "value_score", "quality_score",
        "liquidity_score", "coverage", "pe_ttm", "pb", "roe", "net_margin",
        "debt_ratio", "market_cap",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-only value-quality factor prototype")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=0.60)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        as_of = pd.to_datetime(args.as_of).date() if args.as_of else None
        fundamentals = load_fundamentals_snapshot(conn, as_of=as_of)
    finally:
        conn.close()
    scored = compute_value_quality_scores(fundamentals)
    signals = generate_signals(scored, top_n=args.top_n, min_score=args.min_score)
    result = {
        "rows": int(len(scored)),
        "signals": int(len(signals)),
        "avg_coverage": float(scored["coverage"].mean()) if not scored.empty else 0.0,
        "top": scored.head(5)[["symbol", "score", "value_score", "quality_score", "coverage"]].to_dict("records")
        if not scored.empty else [],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
