import pandas as pd

from src.research.strategies.low_vol import compute_low_vol_scores


def test_low_vol_score_prefers_lower_realized_volatility():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"] * 2
            ),
            "symbol": ["LOW"] * 4 + ["HIGH"] * 4,
            "close": [10.0, 10.1, 10.0, 10.1, 10.0, 12.0, 8.0, 13.0],
            "amount": [1000.0] * 8,
        }
    )

    scored = compute_low_vol_scores(prices, lookback=3)

    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-04")]
    low_score = float(latest.loc[latest["symbol"] == "LOW", "score"].iloc[0])
    high_score = float(latest.loc[latest["symbol"] == "HIGH", "score"].iloc[0])
    assert low_score > high_score
