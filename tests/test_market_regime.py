import pandas as pd

from src.research.market_regime import compute_market_regime, latest_market_regime


def test_rising_index_above_moving_averages_is_risk_on():
    prices = pd.DataFrame(
        {
            "index_code": ["000300"] * 150,
            "trade_date": pd.date_range("2025-01-01", periods=150),
            "close": [100.0 + i for i in range(150)],
        }
    )

    regime = compute_market_regime(prices)

    latest = regime.iloc[-1]
    assert latest["risk_state"] == "risk_on"
    assert latest["satellite_scale"] > 1.0
    assert latest["trend_score"] >= 0.75


def test_deep_drawdown_is_risk_off():
    prices = pd.DataFrame(
        {
            "index_code": ["000300"] * 150,
            "trade_date": pd.date_range("2025-01-01", periods=150),
            "close": [100.0] * 120 + [95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 65.0, 60.0] + [60.0] * 22,
        }
    )

    regime = compute_market_regime(prices)

    latest = regime.iloc[-1]
    assert latest["risk_state"] == "risk_off"
    assert latest["satellite_scale"] < 0.5
    assert latest["drawdown"] < -0.25


def test_extreme_one_day_drop_is_crisis_without_waiting_for_moving_averages():
    prices = pd.DataFrame(
        {
            "index_code": ["000300"] * 130,
            "trade_date": pd.date_range("2025-01-01", periods=130),
            "close": [100.0 + 0.05 * i for i in range(129)] + [88.0],
        }
    )

    regime = compute_market_regime(prices)

    latest = regime.iloc[-1]
    assert latest["risk_state"] == "crisis"
    assert latest["satellite_scale"] == 0.0
    assert "return_1d" in latest["reason"]


def test_empty_input_returns_unknown_latest_regime():
    latest = latest_market_regime(pd.DataFrame(columns=["index_code", "trade_date", "close"]))

    assert latest["risk_state"] == "unknown"
    assert latest["satellite_scale"] == 1.0
    assert "missing" in latest["reason"]


def test_multi_index_input_filters_benchmark():
    prices = pd.DataFrame(
        {
            "index_code": ["000905"] * 150 + ["000300"] * 150,
            "trade_date": list(pd.date_range("2025-01-01", periods=150)) * 2,
            "close": [200.0 - i for i in range(150)] + [100.0 + i for i in range(150)],
        }
    )

    regime = compute_market_regime(prices, benchmark="000300")

    assert set(regime["index_code"]) == {"000300"}
    assert regime.iloc[-1]["risk_state"] == "risk_on"


def test_single_non_benchmark_input_returns_unknown_latest_regime():
    prices = pd.DataFrame(
        {
            "index_code": ["000905"] * 20,
            "trade_date": pd.date_range("2025-01-01", periods=20),
            "close": [100.0 + i for i in range(20)],
        }
    )

    latest = latest_market_regime(prices, benchmark="000300")

    assert latest["risk_state"] == "unknown"
    assert latest["index_code"] == "000300"
