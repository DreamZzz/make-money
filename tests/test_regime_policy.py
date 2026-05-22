from datetime import date, timedelta

import duckdb

from src.data_pipeline.loader import init_db
from src.portfolio.allocator import AllocationConfig
from src.portfolio.regime_policy import (
    RegimePolicyConfig,
    apply_regime_policy_to_allocation_config,
    derive_regime_policy,
    load_latest_regime_policy,
)


def test_risk_on_policy_allows_normal_buys_and_satellite_budget():
    decision = derive_regime_policy(
        {
            "risk_state": "risk_on",
            "trend_score": 1.0,
            "drawdown": -0.02,
            "reason": "trend healthy",
        }
    )

    assert decision.regime_state == "risk_on"
    assert decision.buy_policy == "normal"
    assert decision.allow_new_buys is True
    assert decision.core_target_pct == 0.50
    assert decision.satellite_target_pct == 0.45
    assert decision.cash_target_pct == 0.05
    assert "trend healthy" in decision.reason


def test_crisis_policy_blocks_buys_and_raises_cash():
    decision = derive_regime_policy(
        {
            "risk_state": "crisis",
            "return_1d": -0.08,
            "drawdown": -0.30,
            "reason": "extreme market stress",
        }
    )

    assert decision.regime_state == "crisis"
    assert decision.buy_policy == "sell_only"
    assert decision.allow_new_buys is False
    assert decision.satellite_target_pct == 0.0
    assert decision.cash_target_pct >= 0.40
    assert "停止新增BUY" in decision.action_hint


def test_unknown_policy_is_data_blocked_not_risk_on():
    decision = derive_regime_policy({"risk_state": "unknown", "reason": "missing data"})

    assert decision.regime_state == "unknown"
    assert decision.buy_policy == "data_blocked"
    assert decision.allow_new_buys is False
    assert decision.cash_target_pct == 0.10


def test_apply_regime_policy_to_allocation_config_preserves_cash_target():
    base = AllocationConfig(
        core_target_pct=0.60,
        satellite_target_pct=0.40,
        rebalance_tolerance_pct=0.05,
        min_trade_amount=1000,
    )
    decision = derive_regime_policy({"risk_state": "risk_off", "reason": "market breakdown"})

    adjusted = apply_regime_policy_to_allocation_config(base, decision)

    assert adjusted.core_target_pct == 0.70
    assert adjusted.satellite_target_pct == 0.10
    assert adjusted.cash_target_pct == 0.20
    assert adjusted.core_cash_priority is True


def test_policy_config_can_override_state_profile():
    decision = derive_regime_policy(
        {"risk_state": "defensive", "reason": "weak breadth"},
        RegimePolicyConfig(
            profiles={
                "defensive": {
                    "core_target_pct": 0.65,
                    "satellite_target_pct": 0.20,
                    "cash_target_pct": 0.15,
                    "buy_policy": "high_confidence_only",
                    "allow_new_buys": True,
                    "min_buy_confidence": 0.85,
                    "action_hint": "仅执行最高置信度BUY",
                }
            }
        ),
    )

    assert decision.core_target_pct == 0.65
    assert decision.satellite_target_pct == 0.20
    assert decision.cash_target_pct == 0.15
    assert decision.min_buy_confidence == 0.85


def test_load_latest_regime_policy_reads_benchmark_history_from_db():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    start = date(2026, 1, 1)
    rows = []
    close = 100.0
    for offset in range(130):
        trade_date = start + timedelta(days=offset)
        close = close * 0.998
        if offset == 129:
            close = 70.0
        rows.append(("000300", trade_date, close))
    conn.executemany(
        "INSERT INTO index_daily (index_code, trade_date, close) VALUES (?, ?, ?)",
        rows,
    )

    decision = load_latest_regime_policy(
        conn,
        as_of=start + timedelta(days=129),
        config={
            "portfolio": {
                "regime_policy": {
                    "enabled": True,
                    "benchmark_index": "000300",
                    "lookback_days": 260,
                }
            }
        },
    )

    assert decision is not None
    assert decision.regime_state in {"risk_off", "crisis"}
    assert decision.allow_new_buys is False
    assert decision.cash_target_pct >= 0.20
    conn.close()
