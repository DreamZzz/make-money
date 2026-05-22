import pandas as pd
import pytest

from src.research.strategies.industry_relative_momentum import (
    MODEL_NAME,
    compute_industry_momentum_scores,
    generate_industry_momentum_signals,
)


def _price_panel() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    closes = {
        "TA": [10.0, 10.0, 10.0, 14.0],
        "TB": [10.0, 10.0, 10.0, 12.0],
        "UA": [10.0, 10.0, 10.0, 10.5],
        "UB": [10.0, 10.0, 10.0, 10.2],
    }
    industries = {
        "TA": "tech",
        "TB": "tech",
        "UA": "utility",
        "UB": "utility",
    }
    rows = []
    for symbol, values in closes.items():
        for trade_date, close in zip(dates, values, strict=True):
            rows.append({
                "trade_date": trade_date,
                "symbol": symbol,
                "close": close,
                "industry": industries[symbol],
                "amount": 1000.0,
            })
    return pd.DataFrame(rows)


def test_stronger_industry_ranks_above_weaker_industry():
    scored = compute_industry_momentum_scores(_price_panel(), lookback=3)

    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-04")]
    industry_ranks = latest.groupby("industry")["industry_rank"].first()

    assert industry_ranks["tech"] > industry_ranks["utility"]


def test_within_top_industry_stronger_stock_gets_higher_score():
    scored = compute_industry_momentum_scores(_price_panel(), lookback=3)

    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-04")].set_index("symbol")

    assert latest.loc["TA", "within_industry_rank"] > latest.loc["TB", "within_industry_rank"]
    assert latest.loc["TA", "score"] > latest.loc["TB", "score"]


def test_generate_signals_respects_selection_limits_and_latest_only():
    prices = _price_panel()
    older = prices.copy()
    older["trade_date"] = older["trade_date"] - pd.Timedelta(days=10)
    all_prices = pd.concat([older, prices], ignore_index=True)

    signals = generate_industry_momentum_signals(
        all_prices,
        lookback=3,
        top_industries=1,
        stocks_per_industry=1,
        min_score=0.0,
        latest_only=True,
    )
    row = signals.iloc[0]

    assert len(signals) == 1
    assert signals["trade_date"].nunique() == 1
    assert row["symbol"] == "TA"
    assert row["side"] == "BUY"
    assert row["model_name"] == MODEL_NAME
    assert row["horizon"] == "20d"
    assert row["expected_holding_days"] == 20
    assert row["max_position_pct"] == pytest.approx(0.08)
    assert 0.60 <= row["confidence"] <= 0.90
    assert MODEL_NAME in row["risk_tags"]


def test_generate_signals_can_emit_all_dates_when_latest_only_is_false():
    prices = _price_panel()
    older = prices.copy()
    older["trade_date"] = older["trade_date"] - pd.Timedelta(days=10)
    all_prices = pd.concat([older, prices], ignore_index=True)

    signals = generate_industry_momentum_signals(
        all_prices,
        lookback=3,
        top_industries=1,
        stocks_per_industry=1,
        min_score=0.0,
        latest_only=False,
    )

    assert signals["trade_date"].nunique() > 1
    assert signals.groupby("trade_date").size().max() == 1
    assert pd.Timestamp("2026-01-04").date() in set(signals["trade_date"])


def test_latest_only_uses_latest_scored_date_before_min_score_filter():
    prices = _price_panel()
    latest_flat = prices[prices["trade_date"] == pd.Timestamp("2026-01-04")].copy()
    latest_flat["trade_date"] = pd.Timestamp("2026-01-05")
    prices = pd.concat([prices, latest_flat], ignore_index=True)

    signals = generate_industry_momentum_signals(
        prices,
        lookback=1,
        top_industries=1,
        stocks_per_industry=1,
        min_score=0.90,
        latest_only=True,
    )

    assert signals.empty


def test_industries_with_too_few_members_are_excluded():
    prices = _price_panel()
    solo = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=4, freq="D"),
        "symbol": ["SA"] * 4,
        "close": [10.0, 10.0, 10.0, 20.0],
        "industry": ["solo"] * 4,
        "amount": [1000.0] * 4,
    })

    scored = compute_industry_momentum_scores(
        pd.concat([prices, solo], ignore_index=True),
        lookback=3,
        min_industry_members=2,
    )

    assert "solo" not in set(scored["industry"])
