"""Architecture and persistence-adjacent contract tests."""
from pathlib import Path

import pandas as pd
import pytest

from src.backtest import qlib_runner
from src.backtest.qlib_runner import (
    _dynamic_instrument_ranges_check,
    _dynamic_membership_history_check,
    _latest_covered_cn_data_date,
    _passes_publish_gate,
    _persist_prediction_frame,
    _production_gate_failure,
    _production_inference_segments,
    _should_skip_rebalance_signal_write,
    _valid_rebalance_horizon,
    compute_daily_ic,
    default_candidate_specs,
    save_candidate_result,
    score_candidate_grid_row,
    select_best_candidate_grid,
    simulate_topn_open,
    simulate_topn_open_constrained,
    simulate_topn_t1_open,
    train_alpha158,
)
from src.backtest.results import compute_metrics
from src.config import PROJECT_ROOT
from src.data_pipeline.loader import init_db
from src.portfolio.cashbook import compute_cashflow_adjusted_return, signed_flow
from src.portfolio.optimizer import build_executable_rebalance_plan
from src.portfolio.paper_engine import _prioritize_signals
from src.signals.generator import filter_executable_universe
from src.signals.lifecycle import expire_stale_signals, retire_replaced_signals


def test_project_root_is_repo_root():
    assert (PROJECT_ROOT / "pyproject.toml").exists()
    assert PROJECT_ROOT == Path(__file__).resolve().parents[1]


def test_qlib_python39_entry_modules_defer_annotations():
    for rel_path in [
        "scripts/convert_to_qlib.py",
        "src/backtest/results.py",
        "src/data_pipeline/loader.py",
    ]:
        text = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
        assert "from __future__ import annotations" in text


def test_predict_latest_cli_returns_nonzero_on_failed_result(monkeypatch):
    monkeypatch.setattr(
        qlib_runner,
        "predict_latest",
        lambda model="production", top_n=None: {"status": "FAILED", "reason": "boom"},
    )

    assert qlib_runner.main(["predict-latest", "--model", "production"]) == 1


def _status_df(status: str, close: float | None = None, error: str = "") -> pd.DataFrame:
    if close is None:
        df = pd.DataFrame()
    else:
        df = pd.DataFrame({
            "symbol": ["000001"],
            "trade_date": [pd.Timestamp("2024-01-03")],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [1000],
        })
    df.attrs["source_status"] = status
    df.attrs["source_error"] = error
    return df


def _seed_update_db(conn):
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '平安银行')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES ('000001', DATE '2024-01-02', 10, 10, 10, 10, 1000)
    """)


def test_update_all_cn_prefers_akshare_before_yfinance(monkeypatch):
    import duckdb

    from src.data_pipeline import main

    conn = duckdb.connect(":memory:")
    _seed_update_db(conn)
    calls = {"ak": 0, "yf": 0}

    def fake_ak(*_args, **_kwargs):
        calls["ak"] += 1
        return _status_df("ok", close=11)

    def fake_yf(*_args, **_kwargs):
        calls["yf"] += 1
        return _status_df("ok", close=12)

    monkeypatch.setattr(main.ak, "fetch_cn_stock_daily", fake_ak)
    monkeypatch.setattr(main.yf, "fetch_cn_daily", fake_yf)
    monkeypatch.setattr(main.yf, "fetch_hk_index_daily", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(main.ak, "fetch_cn_index_daily", lambda *_args, **_kwargs: pd.DataFrame())

    stats = main.update_all(conn, {"data": {"history_years": 1}})
    assert stats["cn_updated"] == 1
    assert calls == {"ak": 1, "yf": 0}


def test_update_all_cn_opens_yfinance_circuit_on_rate_limit(monkeypatch):
    import duckdb

    from src.data_pipeline import main

    conn = duckdb.connect(":memory:")
    _seed_update_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000002', 'CN', '万科A')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES ('000002', DATE '2024-01-02', 10, 10, 10, 10, 1000)
    """)
    calls = {"yf": 0}

    def fake_ak(*_args, **_kwargs):
        return _status_df("source_error")

    def fake_yf(*_args, **_kwargs):
        calls["yf"] += 1
        return _status_df("rate_limited")

    monkeypatch.setattr(main.ak, "fetch_cn_stock_daily", fake_ak)
    monkeypatch.setattr(main.yf, "fetch_cn_daily", fake_yf)
    monkeypatch.setattr(main.yf, "fetch_hk_index_daily", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(main.ak, "fetch_cn_index_daily", lambda *_args, **_kwargs: pd.DataFrame())

    stats = main.update_all(conn, {"data": {"history_years": 1}})
    assert calls["yf"] == 1
    assert stats["cn_yfinance_rate_limited"] == 1
    assert stats["cn_yfinance_skipped_circuit"] == 1
    assert stats["cn_source_error"] == 2


def test_update_all_cn_opens_akshare_circuit_after_transient_errors(monkeypatch):
    import duckdb

    from src.data_pipeline import main

    conn = duckdb.connect(":memory:")
    _seed_update_db(conn)
    for symbol, name in [("000002", "万科A"), ("000003", "测试三")]:
        conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES (?, 'CN', ?)", [symbol, name])
        conn.execute(
            """
            INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
            VALUES (?, DATE '2024-01-02', 10, 10, 10, 10, 1000)
            """,
            [symbol],
        )
    calls = {"ak": 0, "yf": 0}

    def fake_ak(*_args, **_kwargs):
        calls["ak"] += 1
        return _status_df("source_error", error="Connection aborted by remote")

    def fake_yf(*_args, **_kwargs):
        calls["yf"] += 1
        return _status_df("source_error", error="fallback failed")

    monkeypatch.setattr(main.ak, "fetch_cn_stock_daily", fake_ak)
    monkeypatch.setattr(main.yf, "fetch_cn_daily", fake_yf)
    monkeypatch.setattr(main.yf, "fetch_hk_index_daily", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(main.ak, "fetch_cn_index_daily", lambda *_args, **_kwargs: pd.DataFrame())

    stats = main.update_all(conn, {
        "data": {
            "history_years": 1,
            "akshare_cn_error_circuit_threshold": 2,
            "akshare_cn_min_interval_seconds": 0,
            "cn_backup_after_akshare_circuit": False,
        }
    })
    assert calls["ak"] == 2
    assert calls["yf"] == 2
    assert stats["cn_akshare_source_error"] == 2
    assert stats["cn_akshare_circuit_skip"] == 1
    assert stats["cn_source_error"] == 3


def test_update_all_cn_uses_batch_yfinance_after_akshare_circuit(monkeypatch):
    import duckdb

    from src.data_pipeline import main

    conn = duckdb.connect(":memory:")
    _seed_update_db(conn)
    for symbol, name in [("000002", "万科A"), ("000003", "测试三")]:
        conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES (?, 'CN', ?)", [symbol, name])
        conn.execute(
            """
            INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
            VALUES (?, DATE '2024-01-02', 10, 10, 10, 10, 1000)
            """,
            [symbol],
        )
    calls = {"ak": 0, "yf_single": 0, "yf_batch": 0}

    def fake_ak(*_args, **_kwargs):
        calls["ak"] += 1
        return _status_df("source_error", error="Connection aborted by remote")

    def fake_yf_single(*_args, **_kwargs):
        calls["yf_single"] += 1
        return _status_df("source_error", error="single fallback failed")

    def fake_yf_batch(symbols, *_args, **_kwargs):
        calls["yf_batch"] += 1
        out = {}
        for idx, symbol in enumerate(symbols, start=1):
            df = pd.DataFrame({
                "symbol": [symbol],
                "trade_date": [pd.Timestamp("2024-01-03")],
                "open": [10 + idx],
                "high": [10 + idx],
                "low": [10 + idx],
                "close": [10 + idx],
                "volume": [1000],
            })
            df.attrs["source_status"] = "ok"
            out[symbol] = df
        return out

    monkeypatch.setattr(main.ak, "fetch_cn_stock_daily", fake_ak)
    monkeypatch.setattr(main.yf, "fetch_cn_daily", fake_yf_single)
    monkeypatch.setattr(main.yf, "fetch_cn_daily_batch", fake_yf_batch, raising=False)
    monkeypatch.setattr(main.yf, "fetch_hk_index_daily", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(main.ak, "fetch_cn_index_daily", lambda *_args, **_kwargs: pd.DataFrame())

    stats = main.update_all(conn, {
        "data": {
            "history_years": 1,
            "akshare_cn_error_circuit_threshold": 1,
            "akshare_cn_min_interval_seconds": 0,
            "cn_backup_after_akshare_circuit": True,
            "cn_backup_batch_size": 50,
            "cn_backup_max_symbols_after_circuit": 500,
        }
    })

    assert calls == {"ak": 1, "yf_single": 1, "yf_batch": 1}
    assert stats["cn_akshare_circuit_skip"] == 2
    assert stats["cn_yfinance_batch_attempted"] == 2
    assert stats["cn_yfinance_batch_updated"] == 2
    assert stats["cn_source_error"] == 1
    updated = conn.execute("""
        SELECT symbol, close
        FROM daily_price
        WHERE trade_date = DATE '2024-01-03'
        ORDER BY symbol
    """).fetchall()
    assert updated == [("000002", 11.0), ("000003", 12.0)]


def test_compute_metrics_contains_backtest_result_fields():
    returns = pd.Series(
        [0.01, -0.005, 0.02, 0.0],
        index=pd.date_range("2024-01-01", periods=4, freq="B"),
    )
    metrics = compute_metrics(returns)
    assert metrics["start_date"] == pd.Timestamp("2024-01-01").date()
    assert metrics["end_date"] == pd.Timestamp("2024-01-04").date()
    assert metrics["cumulative_return"] == pytest.approx((1.01 * 0.995 * 1.02) - 1)
    assert "sharpe_ratio" in metrics
    assert "max_drawdown" in metrics


def test_filter_executable_universe_removes_missing_symbol_and_price():
    prices = pd.DataFrame(
        {
            "A": [10.0, 10.5],
            "B": [20.0, None],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )
    signals = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-02"]),
            "symbol": ["A", "B", "C"],
            "side": ["BUY", "BUY", "BUY"],
            "confidence": [0.8, 0.8, 0.8],
        }
    )
    result = filter_executable_universe(signals, prices)
    assert result["symbol"].tolist() == ["A"]


def test_cashflow_adjusted_return_ignores_deposit():
    assert compute_cashflow_adjusted_return(
        previous_total_value=100000,
        current_total_value=150000,
        external_flow=50000,
    ) == pytest.approx(0.0)


def test_cashflow_adjusted_return_ignores_withdrawal():
    assert compute_cashflow_adjusted_return(
        previous_total_value=100000,
        current_total_value=80000,
        external_flow=-20000,
    ) == pytest.approx(0.0)


def test_signed_cashflow_values():
    assert signed_flow("DEPOSIT", 1000) == pytest.approx(1000)
    assert signed_flow("WITHDRAW", 1000) == pytest.approx(-1000)


def test_rebalance_plan_respects_cash_budget():
    signals = pd.DataFrame({
        "symbol": ["A", "B", "C"],
        "side": ["BUY", "BUY", "BUY"],
        "score": [1.0, 0.9, 0.8],
        "confidence": [0.9, 0.8, 0.7],
        "max_position_pct": [0.10, 0.10, 0.10],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={},
        latest_prices={"A": 10, "B": 20, "C": 50},
        available_cash=27678,
        total_value=97391,
        cash_reserve_pct=0.05,
        max_gross_exposure_pct=0.95,
        estimated_fee_rate=0.0015,
    )
    executable = plan[plan["executable"]]
    total_cost = (executable["order_value"] + executable["estimated_fee"]).sum()
    assert total_cost <= 27678 - 97391 * 0.05 + 1e-6
    assert executable["order_value"].sum() <= 97391 * 0.95 + 1e-6
    assert plan.loc[plan["action"] == "候选", "reason"].notna().all()


def test_rebalance_plan_respects_gross_exposure_limit():
    signals = pd.DataFrame({
        "symbol": ["A", "B"],
        "side": ["BUY", "BUY"],
        "score": [1.0, 0.9],
        "confidence": [0.9, 0.8],
        "max_position_pct": [0.10, 0.10],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={"X": 0.80},
        latest_prices={"A": 10, "B": 20},
        available_cash=100000,
        total_value=100000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        estimated_fee_rate=0.0,
    )
    assert plan.loc[plan["executable"], "order_value"].sum() <= 15000 + 1e-6


def test_rebalance_plan_marks_unaffordable_lot_as_candidate():
    signals = pd.DataFrame({
        "symbol": ["A"],
        "side": ["BUY"],
        "score": [1.0],
        "confidence": [0.9],
        "max_position_pct": [0.10],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={},
        latest_prices={"A": 1000},
        available_cash=5000,
        total_value=100000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        estimated_fee_rate=0.0,
    )
    assert plan.iloc[0]["action"] == "候选"
    assert plan.iloc[0]["reason"] == "不足一手或预算不足"
    assert plan.iloc[0]["order_value"] == pytest.approx(0)


def test_rebalance_plan_does_not_fill_cash_with_weak_signal():
    signals = pd.DataFrame({
        "symbol": ["A"],
        "side": ["BUY"],
        "score": [0.42],
        "confidence": [0.63],
        "max_position_pct": [0.05],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={},
        latest_prices={"A": 9.46},
        available_cash=5000,
        total_value=100000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        min_buy_confidence=0.75,
        min_buy_rank_score=0.50,
        estimated_fee_rate=0.0,
    )
    assert plan.iloc[0]["action"] == "候选"
    assert plan.iloc[0]["reason"] == "低于执行置信度门槛"
    assert plan.iloc[0]["order_value"] == pytest.approx(0)


def test_rebalance_plan_allows_high_conviction_overweight():
    signals = pd.DataFrame({
        "symbol": ["A"],
        "side": ["BUY"],
        "score": [1.0],
        "confidence": [0.95],
        "max_position_pct": [0.15],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={},
        latest_prices={"A": 120},
        available_cash=50000,
        total_value=300000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        max_single_position_pct=0.10,
        overweight_single_position_pct=0.15,
        overweight_min_confidence=0.90,
        overweight_min_rank_score=0.85,
        min_buy_confidence=0.75,
        min_buy_rank_score=0.50,
        estimated_fee_rate=0.0,
    )
    assert plan.iloc[0]["action"] == "买入"
    assert plan.iloc[0]["order_value"] == pytest.approx(36000)


def test_rebalance_plan_keeps_normal_cap_without_high_conviction():
    signals = pd.DataFrame({
        "symbol": ["A"],
        "side": ["BUY"],
        "score": [0.8],
        "confidence": [0.80],
        "max_position_pct": [0.15],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={},
        latest_prices={"A": 120},
        available_cash=50000,
        total_value=300000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        max_single_position_pct=0.10,
        overweight_single_position_pct=0.15,
        overweight_min_confidence=0.90,
        overweight_min_rank_score=0.85,
        min_buy_confidence=0.75,
        min_buy_rank_score=0.50,
        estimated_fee_rate=0.0,
    )
    assert plan.iloc[0]["action"] == "买入"
    assert plan.iloc[0]["order_value"] == pytest.approx(24000)


def test_rebalance_plan_blocks_conflicting_symbol():
    signals = pd.DataFrame({
        "symbol": ["A", "A"],
        "side": ["BUY", "SELL"],
        "score": [1.0, 0.9],
        "confidence": [0.95, 0.85],
        "max_position_pct": [0.15, 0.05],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={"A": 0.10},
        current_quantities={"A": 1000},
        latest_prices={"A": 10},
        available_cash=50000,
        total_value=100000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        estimated_fee_rate=0.0,
    )
    assert len(plan) == 1
    assert plan.iloc[0]["action"] == "候选"
    assert not bool(plan.iloc[0]["executable"])
    assert plan.iloc[0]["reason"] == "多策略方向冲突，需人工确认"
    assert plan.iloc[0]["order_value"] == pytest.approx(0)


def test_rebalance_plan_uses_sell_proceeds_for_same_period_buys():
    signals = pd.DataFrame({
        "symbol": ["A", "B"],
        "side": ["SELL", "BUY"],
        "score": [1.0, 1.0],
        "confidence": [0.9, 0.8],
        "max_position_pct": [0.0, 0.10],
    })
    plan = build_executable_rebalance_plan(
        signals=signals,
        current_weights={"A": 0.10},
        current_quantities={"A": 1000},
        latest_prices={"A": 10, "B": 10},
        available_cash=0,
        total_value=100000,
        cash_reserve_pct=0.0,
        max_gross_exposure_pct=0.95,
        estimated_fee_rate=0.0,
    )
    sell = plan[plan["symbol"] == "A"].iloc[0]
    buy = plan[plan["symbol"] == "B"].iloc[0]
    assert sell["action"] == "清仓"
    assert sell["order_value"] == pytest.approx(-10000)
    assert buy["action"] == "买入"
    assert buy["order_value"] == pytest.approx(10000)
    assert buy["cash_after"] == pytest.approx(0)


def test_paper_engine_prioritizes_reduces_before_buys():
    signals = pd.DataFrame({
        "signal_id": ["buy_a", "sell_b", "short_c", "buy_d"],
        "symbol": ["A", "B", "C", "D"],
        "side": ["BUY", "SELL", "SHORT", "BUY"],
        "signal_date": [
            pd.Timestamp("2024-01-02").date(),
            pd.Timestamp("2024-01-02").date(),
            pd.Timestamp("2024-01-02").date(),
            pd.Timestamp("2024-01-02").date(),
        ],
        "confidence": [0.99, 0.80, 0.90, 0.70],
        "score": [0.99, 0.80, 0.90, 0.70],
    })
    ordered = _prioritize_signals(signals)
    assert ordered["side"].tolist() == ["SHORT", "SELL", "BUY", "BUY"]
    assert ordered["signal_id"].tolist()[-2:] == ["buy_a", "buy_d"]


def test_signal_lifecycle_expires_after_t_plus_one_window():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', '平安银行'), ('000002', 'CN', '万科A')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES
          ('000002', DATE '2024-01-03', 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-04', 10, 10, 10, 10, 1000),
          ('000002', DATE '2024-01-05', 10, 10, 10, 10, 1000)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, executed, status
        )
        VALUES ('old_buy', 'trend_following', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 1, 0.9, FALSE, 'ACTIVE')
    """)

    assert expire_stale_signals(conn, as_of=pd.Timestamp("2024-01-03").date()) == 0
    assert expire_stale_signals(conn, as_of=pd.Timestamp("2024-01-04").date()) == 1
    row = conn.execute("SELECT executed, status FROM signals WHERE signal_id = 'old_buy'").fetchone()
    assert row == (True, "EXPIRED")
    conn.close()


def test_signal_lifecycle_supersedes_older_same_direction_signal():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, executed, status
        )
        VALUES ('old_buy', 'trend_following', '1.0', '000001',
                TIMESTAMP '2024-01-02 15:00:00', 'BUY', 0.8, 0.8, FALSE, 'ACTIVE')
    """)
    new_signals = pd.DataFrame({
        "signal_id": ["new_buy"],
        "model_name": ["trend_following"],
        "symbol": ["000001"],
        "side": ["BUY"],
        "signal_ts": [pd.Timestamp("2024-01-03 15:00:00")],
    })

    assert retire_replaced_signals(conn, new_signals) == 1
    row = conn.execute("""
        SELECT executed, status, superseded_by
        FROM signals WHERE signal_id = 'old_buy'
    """).fetchone()
    assert row == (True, "SUPERSEDED", "new_buy")
    conn.close()


def test_qlib_daily_ic_uses_forward_returns():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"])
    symbols = [f"S{i:02d}" for i in range(12)]
    pred = pd.DataFrame({
        "datetime": [dates[0]] * len(symbols),
        "instrument": symbols,
        "score": list(range(len(symbols))),
    })
    rows = []
    for d_idx, dt in enumerate(dates):
        for s_idx, sym in enumerate(symbols):
            close = 10 + s_idx * (1 + 0.02 * d_idx)
            rows.append({"trade_date": dt, "symbol": sym, "open": close, "close": close})
    prices = pd.DataFrame(rows)

    result = compute_daily_ic(pred, prices, horizon=3)
    assert not result.empty
    assert result.iloc[0]["rank_ic"] > 0.9
    assert result.iloc[0]["top_return"] > result.iloc[0]["bottom_return"]


def test_qlib_topn_t1_open_simulation_applies_cost_and_turnover():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    pred = pd.DataFrame({
        "datetime": [dates[0], dates[0]],
        "instrument": ["A", "B"],
        "score": [1.0, 0.2],
    })
    prices = pd.DataFrame({
        "trade_date": [dates[0], dates[0], dates[1], dates[1], dates[2], dates[2]],
        "symbol": ["A", "B", "A", "B", "A", "B"],
        "open": [10, 10, 10, 10, 11, 9],
        "close": [10, 10, 10, 10, 11, 9],
    })
    returns = simulate_topn_t1_open(pred, prices, top_n=1, commission_rate=0.0, stamp_duty_rate=0.0)
    assert returns.iloc[0] == pytest.approx(0.10)
    returns_cost = simulate_topn_t1_open(pred, prices, top_n=1, commission_rate=0.001, stamp_duty_rate=0.001)
    assert returns_cost.iloc[0] < returns.iloc[0]
    assert returns_cost.attrs["turnover"] == pytest.approx(252.0)


def test_qlib_publish_gate_requires_positive_excess_icir_floor_and_reasonable_drawdown():
    ok, reason = _passes_publish_gate({
        "ic_mean": 0.01,
        "icir": 0.2,
        "max_drawdown": -0.25,
        "excess_return": 0.01,
    })
    assert not ok
    assert "ICIR" in reason

    ok, reason = _passes_publish_gate({
        "ic_mean": 0.01,
        "icir": 0.35,
        "max_drawdown": -0.25,
        "excess_return": -0.01,
    })
    assert not ok
    assert "主基准超额" in reason

    ok, reason = _passes_publish_gate({
        "ic_mean": 0.01,
        "icir": 0.35,
        "max_drawdown": -0.40,
        "excess_return": 0.01,
    })
    assert not ok
    assert "最大回撤" in reason

    ok, reason = _passes_publish_gate({
        "ic_mean": 0.01,
        "icir": 0.35,
        "max_drawdown": -0.25,
        "excess_return": 0.01,
    })
    assert ok
    assert reason == ""

    ok, reason = _passes_publish_gate({
        "ic_mean": -0.01,
        "icir": 0.0,
        "max_drawdown": -0.70,
        "excess_return": -0.10,
    })
    assert not ok
    assert "IC Mean" in reason
    assert "最大回撤" in reason


def test_qlib_universe_checks_reject_current_snapshot_only_membership():
    current_only = pd.DataFrame({
        "index_code": ["000300", "000905"],
        "symbol": ["000001", "000002"],
        "start_date": [pd.Timestamp("2016-01-04").date(), pd.Timestamp("2016-01-04").date()],
        "end_date": [None, None],
    })

    ok, reason = _dynamic_membership_history_check(current_only, ["000300", "000905"])

    assert not ok
    assert "历史成分" in reason

    historical = current_only.copy()
    historical.loc[0, "end_date"] = pd.Timestamp("2021-12-31").date()
    historical.loc[1, "end_date"] = pd.Timestamp("2022-06-30").date()

    ok, reason = _dynamic_membership_history_check(historical, ["000300", "000905"])

    assert ok
    assert reason == ""


def test_qlib_universe_checks_reject_static_open_ended_instruments():
    static_instruments = pd.DataFrame({
        "symbol": ["000001", "000002"],
        "start": ["2016-01-04", "2020-01-01"],
        "end": ["2099-12-31", "2099-12-31"],
    })

    ok, reason = _dynamic_instrument_ranges_check(static_instruments, "csi800")

    assert not ok
    assert "动态历史区间" in reason

    dynamic_instruments = pd.DataFrame({
        "symbol": ["000001", "000001", "000002"],
        "start": ["2016-01-04", "2022-01-01", "2020-01-01"],
        "end": ["2021-12-31", "2099-12-31", "2099-12-31"],
    })

    ok, reason = _dynamic_instrument_ranges_check(dynamic_instruments, "csi800")

    assert ok
    assert reason == ""


def test_production_gate_failure_rechecks_existing_production_metrics():
    reason = _production_gate_failure("v1", {
        "ic_mean": 0.01,
        "icir": 0.2,
        "max_drawdown": -0.25,
        "excess_return": 0.01,
    })

    assert reason is not None
    assert "v1" in reason
    assert "ICIR" in reason

    reason = _production_gate_failure("v2", {
        "ic_mean": 0.01,
        "icir": 0.35,
        "max_drawdown": -0.25,
        "excess_return": 0.01,
    })

    assert reason is None


def test_qlib_candidate_catalog_contains_runnable_lgbm_and_skipped_model_choices():
    candidates = default_candidate_specs(include_unavailable=True)
    ids = {item["candidate_id"] for item in candidates}

    assert {"lgb_baseline", "lgb_conservative", "lgb_balanced"}.issubset(ids)
    assert {"xgb_alpha158", "catboost_alpha158", "mlp_alpha158"}.issubset(ids)
    assert next(item for item in candidates if item["candidate_id"] == "lgb_conservative")["params"]["num_leaves"] < 256
    assert next(item for item in candidates if item["candidate_id"] == "xgb_alpha158")["status"] == "SKIPPED"


def test_qlib_candidate_scoring_prefers_production_friendly_low_turnover():
    grid = pd.DataFrame([
        {
            "benchmark_name": "MIXED_EQUAL",
            "top_n": 20,
            "holding_days": 1,
            "rebalance_freq": "daily",
            "buffer_n": 30,
            "annual_return": 0.30,
            "sharpe_ratio": 0.70,
            "max_drawdown": -0.36,
            "turnover": 190.0,
            "benchmark_return": 0.23,
            "excess_return": 0.07,
        },
        {
            "benchmark_name": "MIXED_EQUAL",
            "top_n": 50,
            "holding_days": 9,
            "rebalance_freq": "monthly",
            "buffer_n": 75,
            "annual_return": 0.10,
            "sharpe_ratio": 0.43,
            "max_drawdown": -0.20,
            "turnover": 11.0,
            "benchmark_return": 0.03,
            "excess_return": 0.07,
        },
    ])

    assert score_candidate_grid_row(grid.iloc[1]) > score_candidate_grid_row(grid.iloc[0])
    best = select_best_candidate_grid(grid)
    assert best["top_n"] == 50
    assert best["rebalance_freq"] == "monthly"


def test_qlib_candidate_scoring_can_target_medium_turnover_band():
    grid = pd.DataFrame([
        {
            "benchmark_name": "MIXED_EQUAL",
            "top_n": 20,
            "holding_days": 1,
            "rebalance_freq": "daily",
            "buffer_n": 30,
            "annual_return": 0.30,
            "sharpe_ratio": 0.70,
            "max_drawdown": -0.36,
            "turnover": 190.0,
            "benchmark_return": 0.23,
            "excess_return": 0.07,
        },
        {
            "benchmark_name": "MIXED_EQUAL",
            "top_n": 50,
            "holding_days": 9,
            "rebalance_freq": "monthly",
            "buffer_n": 75,
            "annual_return": 0.10,
            "sharpe_ratio": 0.43,
            "max_drawdown": -0.20,
            "turnover": 11.0,
            "benchmark_return": 0.03,
            "excess_return": 0.07,
        },
        {
            "benchmark_name": "MIXED_EQUAL",
            "top_n": 30,
            "holding_days": 5,
            "rebalance_freq": "daily",
            "buffer_n": 45,
            "annual_return": 0.14,
            "sharpe_ratio": 0.62,
            "max_drawdown": -0.18,
            "turnover": 41.0,
            "benchmark_return": 0.03,
            "excess_return": 0.08,
        },
    ])

    assert score_candidate_grid_row(grid.iloc[2], turnover_profile="medium") > score_candidate_grid_row(
        grid.iloc[1], turnover_profile="medium"
    )
    assert score_candidate_grid_row(grid.iloc[2], turnover_profile="medium") > score_candidate_grid_row(
        grid.iloc[0], turnover_profile="medium"
    )
    best = select_best_candidate_grid(grid, turnover_profile="medium")
    assert best["top_n"] == 30
    assert best["turnover"] == 41.0


def test_constrained_qlib_simulation_skips_unaffordable_high_price_lots():
    dates = pd.bdate_range("2026-01-01", periods=8)
    pred = pd.DataFrame([
        {"datetime": dt, "instrument": symbol, "score": score}
        for dt in dates
        for symbol, score in [("HIGH", 3.0), ("MID", 2.0), ("LOW", 1.0)]
    ])
    prices = pd.DataFrame([
        {
            "trade_date": dt,
            "symbol": symbol,
            "open": price + i,
            "close": price + i,
            "pe_ttm": pe,
        }
        for i, dt in enumerate(dates)
        for symbol, price, pe in [("HIGH", 300.0, 30.0), ("MID", 100.0, 18.0), ("LOW", 10.0, 12.0)]
    ])

    returns = simulate_topn_open_constrained(
        pred,
        prices,
        top_n=3,
        holding_days=2,
        rebalance_freq="weekly",
        buffer_n=3,
        account_capital=300_000,
        max_position_pct=0.06,
        cash_reserve_pct=0.05,
    )

    assert not returns.empty
    assert returns.attrs["constraint_profile"] == "small_account_300k"
    assert returns.attrs["avg_selected_count"] == pytest.approx(2.0)
    assert returns.attrs["min_selected_count"] == 2
    assert returns.attrs["skipped_price_cap_avg"] == pytest.approx(1.0)
    assert returns.attrs["max_actual_position_pct"] <= 0.06 + 1e-9
    assert returns.attrs["avg_cash_drag"] > 0


def test_small_account_grid_rows_are_preferred_over_theoretical_equal_weight_rows():
    grid = pd.DataFrame([
        {
            "benchmark_name": "MIXED_EQUAL",
            "constraint_profile": "theoretical_equal_weight",
            "top_n": 100,
            "holding_days": 10,
            "rebalance_freq": "monthly",
            "annual_return": 0.30,
            "excess_return": 0.25,
            "sharpe_ratio": 0.90,
            "max_drawdown": -0.24,
            "turnover": 8.0,
        },
        {
            "benchmark_name": "MIXED_EQUAL",
            "constraint_profile": "small_account_300k",
            "account_capital": 300_000,
            "avg_selected_count": 28,
            "avg_cash_drag": 0.08,
            "max_actual_position_pct": 0.058,
            "top_n": 50,
            "holding_days": 10,
            "rebalance_freq": "monthly",
            "annual_return": 0.18,
            "excess_return": 0.12,
            "sharpe_ratio": 0.70,
            "max_drawdown": -0.18,
            "turnover": 10.0,
        },
    ])

    best = select_best_candidate_grid(grid)

    assert best["constraint_profile"] == "small_account_300k"
    assert best["account_capital"] == 300_000


def test_simulate_topn_open_supports_weekly_rebalance_frequency():
    dates = pd.bdate_range("2026-01-01", periods=15)
    pred = pd.DataFrame([
        {"datetime": dt, "instrument": symbol, "score": score}
        for dt in dates
        for symbol, score in [("000001", 1.0), ("000002", 0.5)]
    ])
    prices = pd.DataFrame([
        {
            "trade_date": dt,
            "symbol": symbol,
            "open": 10 + i + offset,
            "close": 10 + i + offset,
        }
        for i, dt in enumerate(dates)
        for symbol, offset in [("000001", 0.0), ("000002", 1.0)]
    ])

    returns = simulate_topn_open(
        pred,
        prices,
        top_n=1,
        holding_days=2,
        rebalance_freq="weekly",
        buffer_n=1,
    )

    assert not returns.empty
    assert returns.attrs["periods_per_year"] == 52
    assert returns.attrs["turnover"] is not None


def test_simulate_topn_open_skips_cn_limit_open_candidate():
    dates = pd.bdate_range("2024-01-02", periods=4)
    pred = pd.DataFrame([
        {"datetime": dates[0], "instrument": "000001", "score": 1.0},
        {"datetime": dates[0], "instrument": "000002", "score": 0.5},
    ])
    prices = pd.DataFrame([
        {
            "trade_date": dates[0],
            "symbol": symbol,
            "open": 10.0,
            "close": 10.0,
            "pre_close": 10.0,
            "is_st": False,
            "is_suspended": False,
        }
        for symbol in ["000001", "000002"]
    ] + [
        {
            "trade_date": dates[1],
            "symbol": "000001",
            "open": 11.0,
            "close": 11.0,
            "pre_close": 10.0,
            "is_st": False,
            "is_suspended": False,
        },
        {
            "trade_date": dates[1],
            "symbol": "000002",
            "open": 10.0,
            "close": 10.0,
            "pre_close": 10.0,
            "is_st": False,
            "is_suspended": False,
        },
        {
            "trade_date": dates[2],
            "symbol": "000001",
            "open": 12.0,
            "close": 12.0,
            "pre_close": 11.0,
            "is_st": False,
            "is_suspended": False,
        },
        {
            "trade_date": dates[2],
            "symbol": "000002",
            "open": 12.0,
            "close": 12.0,
            "pre_close": 11.0,
            "is_st": False,
            "is_suspended": False,
        },
    ])

    returns = simulate_topn_open(
        pred,
        prices,
        top_n=1,
        holding_days=1,
        commission_rate=0,
        stamp_duty_rate=0,
    )

    assert len(returns) == 1
    assert returns.iloc[0] == pytest.approx(0.2)


def test_simulate_topn_open_constrained_counts_untradeable_candidates():
    dates = pd.bdate_range("2024-01-02", periods=4)
    pred = pd.DataFrame([
        {"datetime": dates[0], "instrument": "000001", "score": 1.0},
        {"datetime": dates[0], "instrument": "000002", "score": 0.5},
    ])
    prices = pd.DataFrame([
        {
            "trade_date": dt,
            "symbol": symbol,
            "open": open_price,
            "close": open_price,
            "pre_close": pre_close,
            "is_st": False,
            "is_suspended": False,
        }
        for dt, symbol, open_price, pre_close in [
            (dates[0], "000001", 10.0, 10.0),
            (dates[0], "000002", 10.0, 10.0),
            (dates[1], "000001", 11.0, 10.0),
            (dates[1], "000002", 10.0, 10.0),
            (dates[2], "000001", 12.0, 11.0),
            (dates[2], "000002", 12.0, 11.0),
        ]
    ])

    returns = simulate_topn_open_constrained(
        pred,
        prices,
        top_n=1,
        holding_days=1,
        account_capital=100_000,
        max_position_pct=0.5,
        cash_reserve_pct=0,
        commission_rate=0,
        stamp_duty_rate=0,
    )

    assert len(returns) == 1
    assert returns.attrs["skipped_untradeable_avg"] == pytest.approx(1.0)


def test_qlib_grid_skips_overlapping_weekly_holding_windows():
    assert _valid_rebalance_horizon("weekly", 5)
    assert not _valid_rebalance_horizon("weekly", 6)
    assert _valid_rebalance_horizon("daily", 10)
    assert _valid_rebalance_horizon("monthly", 10)


def test_qlib_candidate_result_persistence_records_best_combo():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    save_candidate_result(
        conn,
        {
            "candidate_id": "lgb_balanced",
            "batch_id": "QLIB-BATCH-TEST",
            "experiment_id": "QLIB-WALK_FORWARD-TEST",
            "model_name": "alpha158",
            "model_family": "lgbm",
            "model_variant": "balanced",
            "status": "SUCCEEDED",
            "mode": "walk_forward",
            "params_json": "{}",
            "grid_json": "{}",
            "best_benchmark": "MIXED_EQUAL",
            "best_top_n": 50,
            "best_holding_days": 9,
            "best_rebalance_freq": "monthly",
            "best_buffer_n": 75,
            "best_constraint_profile": "small_account_300k",
            "best_account_capital": 300000,
            "avg_selected_count": 32.5,
            "avg_cash_drag": 0.08,
            "max_actual_position_pct": 0.058,
            "annual_return": 0.10,
            "sharpe_ratio": 0.43,
            "max_drawdown": -0.20,
            "turnover": 11.0,
            "benchmark_return": 0.03,
            "excess_return": 0.07,
            "ic_mean": 0.01,
            "icir": 0.2,
            "rank_ic_mean": 0.02,
            "rank_ic_positive_rate": 0.55,
            "score": 0.02,
        },
    )

    row = conn.execute("""
        SELECT status, best_top_n, best_holding_days, best_rebalance_freq,
               best_constraint_profile, best_account_capital, avg_selected_count,
               avg_cash_drag, max_actual_position_pct, score
        FROM qlib_candidate_results
        WHERE candidate_id = 'lgb_balanced'
    """).fetchone()
    assert row == (
        "SUCCEEDED", 50, 9, "monthly", "small_account_300k", 300000,
        pytest.approx(32.5), pytest.approx(0.08), pytest.approx(0.058), pytest.approx(0.02),
    )
    conn.close()


def test_production_prediction_monthly_rebalance_skips_same_period_signal():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts, horizon,
            score, side, confidence, expected_holding_days, max_position_pct,
            thesis, risk_tags, status
        )
        VALUES (
            'sig_existing', 'alpha158', 'alpha158-prod', '000001',
            TIMESTAMP '2026-05-05 15:00:00', '15d', 0.9, 'BUY',
            0.9, 15, 0.05, 'existing monthly signal', ['multi_factor'], 'ACTIVE'
        )
    """)

    skip, reason = _should_skip_rebalance_signal_write(
        conn,
        model_version="alpha158-prod",
        prediction_date=pd.Timestamp("2026-05-13").date(),
        rebalance_freq="monthly",
    )
    next_month_skip, _ = _should_skip_rebalance_signal_write(
        conn,
        model_version="alpha158-prod",
        prediction_date=pd.Timestamp("2026-06-01").date(),
        rebalance_freq="monthly",
    )
    daily_skip, _ = _should_skip_rebalance_signal_write(
        conn,
        model_version="alpha158-prod",
        prediction_date=pd.Timestamp("2026-05-13").date(),
        rebalance_freq="daily",
    )

    assert skip
    assert "monthly" in reason
    assert not next_month_skip
    assert not daily_skip
    conn.close()


def test_production_walk_forward_inference_uses_latest_year_fold():
    segments = _production_inference_segments(
        mode="walk_forward",
        base_segments={
            "train_start": "2019-01-01",
            "train_end": "2022-12-31",
            "valid_start": "2023-01-01",
            "valid_end": "2023-12-31",
            "test_start": "2024-01-01",
            "test_end": "2026-05-14",
        },
        latest_data=pd.Timestamp("2026-05-14").date(),
    )

    assert segments == {
        "train_start": "2019-01-01",
        "train_end": "2024-12-31",
        "valid_start": "2025-01-01",
        "valid_end": "2025-12-31",
        "test_start": "2026-01-01",
        "test_end": "2026-05-14",
    }


def test_alpha158_default_universe_is_csi800():
    import inspect

    assert inspect.signature(train_alpha158).parameters["market"].default == "csi800"


def test_persist_prediction_frame_upserts_latest_production_scores():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    pred = pd.DataFrame({
        "datetime": [pd.Timestamp("2026-05-14")] * 3,
        "instrument": ["000001", "000002", "000003"],
        "score": [0.3, 0.8, 0.1],
    })

    written = _persist_prediction_frame(
        conn,
        experiment_id="QLIB-PROD",
        model_version="alpha158-prod",
        mode="production_inference",
        pred=pred,
        top_n=2,
    )

    rows = conn.execute("""
        SELECT prediction_date, symbol, score, rank, confidence, selected, model_version, mode
        FROM qlib_predictions
        WHERE experiment_id = 'QLIB-PROD'
        ORDER BY rank
    """).fetchall()

    assert written == 3
    assert rows[0][1:6] == ("000002", 0.8, 1, 1.0, True)
    assert rows[1][1] == "000001"
    assert rows[1][3] == 2
    assert rows[1][5] is True
    assert rows[2][1] == "000003"
    assert rows[2][5] is False
    assert {row[6] for row in rows} == {"alpha158-prod"}
    assert {row[7] for row in rows} == {"production_inference"}
    conn.close()


def test_latest_covered_cn_data_date_ignores_partial_target_updates():
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO stock_info (symbol, country, name)
        VALUES ('000001', 'CN', 'A'), ('000002', 'CN', 'B'), ('000003', 'CN', 'C')
    """)
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, high, low, close, volume)
        VALUES
          ('000001', DATE '2026-05-13', 10, 10, 10, 10, 1000),
          ('000002', DATE '2026-05-13', 10, 10, 10, 10, 1000),
          ('000003', DATE '2026-05-13', 10, 10, 10, 10, 1000),
          ('000001', DATE '2026-05-14', 11, 11, 11, 11, 1000)
    """)

    latest = _latest_covered_cn_data_date(conn, min_coverage=0.80)
    partial_allowed = _latest_covered_cn_data_date(conn, min_coverage=0.30)

    assert latest == pd.Timestamp("2026-05-13").date()
    assert partial_allowed == pd.Timestamp("2026-05-14").date()
    conn.close()
