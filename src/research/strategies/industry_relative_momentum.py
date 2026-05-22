"""Research-only industry relative momentum prototype.

Ranks industries by equal-weight price momentum, then ranks stocks by their
relative strength versus industry peers. This module is intentionally not wired
into production signal generation.
"""
from __future__ import annotations

import pandas as pd

MODEL_NAME = "industry_relative_momentum"

SCORE_COLUMNS = [
    "trade_date",
    "symbol",
    "industry",
    "symbol_return",
    "industry_return",
    "relative_return",
    "industry_rank",
    "within_industry_rank",
    "score",
]

SIGNAL_COLUMNS = [
    "trade_date",
    "symbol",
    "side",
    "score",
    "confidence",
    "model_name",
    "horizon",
    "expected_holding_days",
    "max_position_pct",
    "thesis",
    "risk_tags",
]


def compute_industry_momentum_scores(
    prices: pd.DataFrame,
    lookback: int = 60,
    short_lookback: int = 20,
    min_industry_members: int = 2,
) -> pd.DataFrame:
    """Compute industry and within-industry relative momentum scores."""
    if prices.empty or not {"trade_date", "symbol", "close", "industry"}.issubset(prices.columns):
        return pd.DataFrame(columns=SCORE_COLUMNS)

    lookback = max(int(lookback or 1), 1)
    short_lookback = max(int(short_lookback or 1), 1)
    min_industry_members = max(int(min_industry_members or 1), 1)

    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str)
    df["industry"] = df["industry"].fillna("unknown").astype(str)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["trade_date", "symbol", "industry", "close"])
    if df.empty:
        return pd.DataFrame(columns=SCORE_COLUMNS)

    df = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    symbol_groups = df.groupby("symbol", sort=False)["close"]
    df["long_return"] = symbol_groups.pct_change(periods=lookback)
    df["short_return"] = symbol_groups.pct_change(periods=short_lookback)
    df["symbol_return"] = df["long_return"].where(df["long_return"].notna(), df["short_return"])

    member_counts = (
        df.dropna(subset=["symbol_return"])
        .groupby(["trade_date", "industry"], sort=False)["symbol"]
        .transform("nunique")
    )
    df.loc[df["symbol_return"].notna(), "industry_member_count"] = member_counts
    eligible = df[pd.to_numeric(df["industry_member_count"], errors="coerce") >= min_industry_members].copy()
    if eligible.empty:
        return pd.DataFrame(columns=SCORE_COLUMNS)

    eligible["industry_return"] = eligible.groupby(
        ["trade_date", "industry"],
        sort=False,
    )["symbol_return"].transform("mean")
    eligible["relative_return"] = eligible["symbol_return"] - eligible["industry_return"]
    industry_ranks = eligible[["trade_date", "industry", "industry_return"]].drop_duplicates()
    industry_ranks["industry_rank"] = industry_ranks.groupby("trade_date", sort=False)["industry_return"].rank(
        method="average",
        pct=True,
        ascending=True,
    )
    eligible = eligible.merge(
        industry_ranks[["trade_date", "industry", "industry_rank"]],
        on=["trade_date", "industry"],
        how="left",
    )
    eligible["within_industry_rank"] = eligible.groupby(
        ["trade_date", "industry"],
        sort=False,
    )["relative_return"].rank(method="average", pct=True, ascending=True)
    eligible["score"] = (
        0.60 * eligible["industry_rank"]
        + 0.40 * eligible["within_industry_rank"]
    ).clip(0.0, 1.0)

    scored = eligible.dropna(subset=["score"])
    return (
        scored[SCORE_COLUMNS]
        .sort_values(["trade_date", "score", "symbol"], ascending=[True, False, True])
        .reset_index(drop=True)
    )


def generate_industry_momentum_signals(
    prices: pd.DataFrame,
    top_industries: int = 3,
    stocks_per_industry: int = 5,
    min_score: float = 0.65,
    latest_only: bool = True,
    **kwargs,
) -> pd.DataFrame:
    """Convert industry relative momentum scores into standard BUY signal rows."""
    scored = compute_industry_momentum_scores(prices, **kwargs)
    if scored.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    top_industries = max(int(top_industries or 1), 1)
    stocks_per_industry = max(int(stocks_per_industry or 1), 1)
    min_score = float(min_score)

    if latest_only:
        latest_date = pd.to_datetime(scored["trade_date"]).max()
        scored = scored[pd.to_datetime(scored["trade_date"]) == latest_date].copy()

    df = scored[pd.to_numeric(scored["score"], errors="coerce") >= min_score].copy()
    if df.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    selected_frames = []
    for trade_date, date_df in df.groupby("trade_date", sort=True):
        industries = (
            date_df[["industry", "industry_rank", "industry_return"]]
            .drop_duplicates("industry")
            .sort_values(["industry_rank", "industry_return", "industry"], ascending=[False, False, True])
            .head(top_industries)["industry"]
        )
        selected = (
            date_df[date_df["industry"].isin(industries)]
            .sort_values(["industry", "score", "within_industry_rank", "symbol"], ascending=[True, False, False, True])
            .groupby("industry", sort=False)
            .head(stocks_per_industry)
        )
        selected_frames.append(selected.assign(trade_date=trade_date))

    if not selected_frames:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    signals_base = pd.concat(selected_frames, ignore_index=True)
    if signals_base.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    signals_base = signals_base.sort_values(["trade_date", "score", "symbol"], ascending=[True, False, True])
    confidence = (0.60 + signals_base["score"].clip(0.0, 1.0) * 0.30).clip(0.60, 0.90)

    signals = pd.DataFrame({
        "trade_date": pd.to_datetime(signals_base["trade_date"]).dt.date,
        "symbol": signals_base["symbol"].astype(str).values,
        "side": "BUY",
        "score": signals_base["score"].clip(0.0, 1.0).values,
        "confidence": confidence.values,
        "model_name": MODEL_NAME,
        "horizon": "20d",
        "expected_holding_days": 20,
        "max_position_pct": 0.08,
        "thesis": [
            (
                f"Industry relative momentum: {row.industry} rank {row.industry_rank:.2f}; "
                f"stock relative return {row.relative_return:.2%}; score {row.score:.2f}"
            )
            for row in signals_base.itertuples(index=False)
        ],
        "risk_tags": [[MODEL_NAME, "industry", "momentum", "research_only"] for _ in range(len(signals_base))],
    })
    return signals[SIGNAL_COLUMNS].reset_index(drop=True)
