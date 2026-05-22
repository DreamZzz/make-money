import pandas as pd

from src.research.strategies.cross_reversal import compute_cross_reversal_scores


def test_cross_reversal_prefers_recent_loser_within_industry():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 2),
            "symbol": ["A"] * 3 + ["B"] * 3,
            "industry": ["tech"] * 6,
            "close": [10.0, 9.0, 8.0, 10.0, 11.0, 12.0],
            "amount": [1000.0] * 6,
        }
    )

    scored = compute_cross_reversal_scores(prices, lookback=2)

    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-03")]
    loser_score = float(latest.loc[latest["symbol"] == "A", "score"].iloc[0])
    winner_score = float(latest.loc[latest["symbol"] == "B", "score"].iloc[0])
    assert loser_score > winner_score


def test_cross_reversal_smoothing_changes_noisy_raw_reversal():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"] * 2),
            "symbol": ["A"] * 4 + ["B"] * 4,
            "industry": ["tech"] * 8,
            "close": [10.0, 9.0, 11.0, 8.5, 10.0, 11.0, 9.0, 12.0],
            "amount": [1000.0] * 8,
        }
    )

    raw = compute_cross_reversal_scores(prices, lookback=1, smooth_days=1)
    smooth = compute_cross_reversal_scores(prices, lookback=1, smooth_days=2)

    latest_raw = raw[raw["trade_date"] == pd.Timestamp("2026-01-04")].sort_values("symbol")
    latest_smooth = smooth[smooth["trade_date"] == pd.Timestamp("2026-01-04")].sort_values("symbol")
    assert "smoothed_reversal" in smooth.columns
    assert latest_smooth["smoothed_reversal"].tolist() != latest_raw["smoothed_reversal"].tolist()


def test_cross_reversal_size_neutral_residual_removes_size_exposure():
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 4)
    prices = pd.DataFrame(
        {
            "trade_date": dates,
            "symbol": ["A"] * 3 + ["B"] * 3 + ["C"] * 3 + ["D"] * 3,
            "industry": ["tech"] * 12,
            "close": [10, 10, 9, 10, 10, 8, 10, 10, 7, 10, 10, 6],
            "market_cap": [10, 10, 10, 20, 20, 20, 40, 40, 40, 80, 80, 80],
            "amount": [1000.0] * 12,
        }
    )

    scored = compute_cross_reversal_scores(prices, lookback=2, size_neutral=True)
    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-03")]

    assert "residual_reversal" in scored.columns
    exposure = (
        (latest["residual_reversal"] - latest["residual_reversal"].mean())
        * (latest["log_market_cap"] - latest["log_market_cap"].mean())
    ).sum()
    assert abs(exposure) < 1e-9


def test_cross_reversal_accepts_market_cap_without_size_neutral():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 2),
            "symbol": ["A"] * 3 + ["B"] * 3,
            "industry": ["tech"] * 6,
            "close": [10, 10, 9, 10, 10, 8],
            "market_cap": [10, 10, 10, 20, 20, 20],
        }
    )

    scored = compute_cross_reversal_scores(prices, lookback=2, size_neutral=False)

    assert not scored.empty
    assert "log_market_cap" in scored.columns


def test_cross_reversal_beta_neutral_residual_removes_beta_exposure():
    market_returns = [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]
    beta_by_symbol = {"A": 0.5, "B": 1.0, "C": 1.5, "D": 2.0}
    rows = []
    dates = pd.date_range("2026-01-01", periods=len(market_returns) + 1, freq="D")
    for symbol, beta in beta_by_symbol.items():
        close = 10.0
        rows.append({"trade_date": dates[0], "symbol": symbol, "industry": "tech", "close": close})
        for idx, market_return in enumerate(market_returns, start=1):
            close *= 1 + beta * market_return
            rows.append({"trade_date": dates[idx], "symbol": symbol, "industry": "tech", "close": close})
    prices = pd.DataFrame(rows)

    scored = compute_cross_reversal_scores(prices, lookback=2, beta_neutral=True, beta_lookback=3)
    latest = scored[scored["trade_date"] == dates[-1]].dropna(subset=["rolling_beta"])

    assert "rolling_beta" in scored.columns
    exposure = (
        (latest["residual_reversal"] - latest["residual_reversal"].mean())
        * (latest["rolling_beta"] - latest["rolling_beta"].mean())
    ).sum()
    assert abs(exposure) < 1e-9


def test_cross_reversal_beta_neutral_handles_constant_market_without_crash():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 3),
            "symbol": ["A"] * 3 + ["B"] * 3 + ["C"] * 3,
            "industry": ["tech"] * 9,
            "close": [10, 10, 9, 20, 20, 19, 30, 30, 29],
        }
    )

    scored = compute_cross_reversal_scores(prices, lookback=2, beta_neutral=True, beta_lookback=3)

    assert not scored.empty
    assert "rolling_beta" in scored.columns
    assert scored["score"].notna().all()
