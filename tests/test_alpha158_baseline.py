import pandas as pd

from src.research.strategies.alpha158_baseline import generate_signals


def test_alpha158_generates_sell_for_held_symbol_outside_exit_rank():
    predictions = pd.DataFrame({
        "datetime": ["2026-05-11"] * 4,
        "instrument": ["000001", "000002", "000003", "000004"],
        "score": [0.9, 0.7, 0.3, 0.1],
    })

    signals = generate_signals(
        predictions,
        top_n=1,
        current_holdings={"000001": 100, "000003": 200, "000004": 300},
        exit_rank_multiplier=2.0,
    )

    buys = signals[signals["side"] == "BUY"]
    sells = signals[signals["side"] == "SELL"]
    assert buys["symbol"].tolist() == ["000001"]
    assert sells["symbol"].tolist() == ["000003", "000004"]
    assert set(sells["max_position_pct"]) == {0.0}
    assert sells["thesis"].str.contains("outside Top2").all()


def test_alpha158_keeps_existing_holding_inside_exit_buffer():
    predictions = pd.DataFrame({
        "datetime": ["2026-05-11"] * 3,
        "instrument": ["000001", "000002", "000003"],
        "score": [0.9, 0.8, 0.1],
    })

    signals = generate_signals(
        predictions,
        top_n=1,
        current_holdings={"000002": 100},
        exit_rank_multiplier=2.0,
    )

    assert signals[signals["side"] == "SELL"].empty
    assert signals[signals["side"] == "BUY"]["symbol"].tolist() == ["000001"]


def test_alpha158_signal_timestamp_uses_prediction_date_for_t_plus_one_execution():
    predictions = pd.DataFrame({
        "datetime": ["2026-05-11"] * 2,
        "instrument": ["000001", "000002"],
        "score": [0.9, 0.7],
    })

    signals = generate_signals(predictions, top_n=1)

    assert pd.to_datetime(signals.iloc[0]["signal_ts"]).date() == pd.Timestamp("2026-05-11").date()


def test_alpha158_buy_signal_score_is_normalized_for_execution_thresholds():
    predictions = pd.DataFrame({
        "datetime": ["2026-05-11"] * 4,
        "instrument": ["000001", "000002", "000003", "000004"],
        "score": [0.003, -0.001, -0.002, -0.004],
    })

    signals = generate_signals(predictions, top_n=2)
    buys = signals[signals["side"] == "BUY"].reset_index(drop=True)

    assert buys["symbol"].tolist() == ["000001", "000002"]
    assert buys["score"].between(0, 1).all()
    assert buys.loc[0, "score"] == 1.0
    assert buys["score"].equals(buys["confidence"])
    assert "raw score" in buys.loc[1, "thesis"]
