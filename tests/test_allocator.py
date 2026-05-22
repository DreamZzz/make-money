from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from src.data_pipeline.loader import init_db
from src.portfolio.allocator import (
    AllocationConfig,
    AllocationInputs,
    compute_allocation_plan,
    create_allocation_plan,
    persist_allocation_plan,
    resolve_regime_policy_for_plan,
)
from src.portfolio.regime_policy import derive_regime_policy


def test_core_underweight_receives_cash_before_satellite_budget():
    plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=30_000,
            core_value=30_000,
            satellite_value=40_000,
        ),
        AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1_000,
            core_cash_priority=True,
        ),
    )

    assert plan.total_value == pytest.approx(100_000)
    assert plan.core_budget == pytest.approx(30_000)
    assert plan.satellite_budget == pytest.approx(0)
    assert plan.items[0].sleeve == "core"
    assert plan.items[0].action == "ADD"
    assert plan.items[1].action == "HOLD"


def test_satellite_overweight_sets_satellite_buy_budget_to_zero():
    plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=20_000,
            core_value=20_000,
            satellite_value=80_000,
        ),
        AllocationConfig(core_target_pct=0.60, satellite_target_pct=0.40),
    )

    assert plan.satellite_budget == pytest.approx(0)
    satellite = [item for item in plan.items if item.sleeve == "satellite"][0]
    assert satellite.action == "REDUCE"
    assert "超配" in satellite.reason


def test_missing_index_fund_holdings_treat_core_value_as_zero():
    plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=10_000,
            core_value=0,
            satellite_value=90_000,
        ),
        AllocationConfig(core_target_pct=0.60, satellite_target_pct=0.40),
    )

    assert plan.core_value == pytest.approx(0)
    assert plan.core_budget == pytest.approx(10_000)
    assert plan.satellite_budget == pytest.approx(0)


def test_allocation_plan_preserves_regime_cash_target_before_spending_budgets():
    plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=30_000,
            core_value=50_000,
            satellite_value=20_000,
        ),
        AllocationConfig(
            core_target_pct=0.70,
            satellite_target_pct=0.10,
            cash_target_pct=0.20,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1_000,
            core_cash_priority=True,
        ),
    )

    assert plan.cash_target_pct == pytest.approx(0.20)
    assert plan.core_budget == pytest.approx(10_000)
    assert plan.satellite_budget == pytest.approx(0)
    assert plan.items[0].target_value == pytest.approx(70_000)
    assert plan.items[1].target_value == pytest.approx(10_000)


def test_persist_allocation_plan_is_idempotent_for_account_and_date():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=10_000,
            core_value=50_000,
            satellite_value=40_000,
        ),
        AllocationConfig(core_target_pct=0.60, satellite_target_pct=0.40),
    )

    persist_allocation_plan(conn, plan)
    persist_allocation_plan(conn, plan)

    plan_rows = conn.execute("SELECT COUNT(*) FROM allocation_plans").fetchone()[0]
    item_rows = conn.execute("SELECT COUNT(*) FROM allocation_plan_items").fetchone()[0]
    saved = conn.execute("""
        SELECT core_budget, satellite_budget, status
        FROM allocation_plans
        WHERE plan_id = ?
    """, [plan.plan_id]).fetchone()

    assert plan_rows == 1
    assert item_rows == 2
    assert saved == pytest.approx((10_000, 0, "ACTIVE"))
    conn.close()


def test_create_allocation_plan_allocates_core_budget_to_latest_buy_add_fund_signals():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-15', 100000, 40000, 140000, 140000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO index_fund_signals (
            signal_id, fund_code, index_code, signal_date, action,
            target_weight, confidence, thesis, risk_tags
        )
        VALUES
            ('IFS-1', '510300', '000300', DATE '2026-05-15', 'BUY', 0.60, 0.80, 'core buy', ['underweight']),
            ('IFS-2', '513130', 'HSTECH', DATE '2026-05-15', 'ADD', 0.40, 0.70, 'core add', ['underweight'])
    """)

    plan = create_allocation_plan(
        conn,
        config=AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1000,
            core_cash_priority=True,
        ),
        persist=True,
    )
    fund_items = conn.execute("""
        SELECT instrument_id, action, target_value, budget_delta
        FROM allocation_plan_items
        WHERE plan_id = ? AND sleeve = 'core' AND instrument_type = 'index_fund'
        ORDER BY priority
    """, [plan.plan_id]).fetchdf()

    assert plan.core_budget == pytest.approx(84_000)
    assert fund_items["instrument_id"].tolist() == ["510300", "513130"]
    assert fund_items["action"].tolist() == ["BUY", "ADD"]
    assert fund_items["target_value"].tolist() == pytest.approx([50_400, 33_600])
    assert fund_items["budget_delta"].tolist() == pytest.approx([50_400, 33_600])
    conn.close()


def test_create_allocation_plan_can_consume_regime_policy_without_default_side_effects():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-15', 30000, 20000, 100000, 100000, 1, 0, 0)
    """)
    decision = derive_regime_policy({"risk_state": "risk_off", "reason": "market breakdown"})

    base = create_allocation_plan(
        conn,
        config=AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1000,
        ),
        persist=False,
    )
    adjusted = create_allocation_plan(
        conn,
        config=AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1000,
        ),
        regime_policy=decision,
        persist=False,
    )

    assert base.cash_target_pct == pytest.approx(0.0)
    assert adjusted.cash_target_pct == pytest.approx(0.20)
    assert adjusted.satellite_target_pct == pytest.approx(0.10)
    assert adjusted.satellite_budget == pytest.approx(0)
    assert adjusted.total_value == pytest.approx(50_000)
    assert adjusted.core_budget == pytest.approx(20_000)
    assert adjusted.cash - adjusted.core_budget == pytest.approx(10_000)
    conn.close()


def test_resolve_regime_policy_for_plan_is_opt_in_and_reads_index_daily():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    rows = []
    close = 100.0
    for offset in range(130):
        trade_date = date(2026, 1, 1) + timedelta(days=offset)
        close *= 0.998
        if offset == 129:
            close = 70.0
        rows.append(("000300", trade_date, close))
    conn.executemany(
        "INSERT INTO index_daily (index_code, trade_date, close) VALUES (?, ?, ?)",
        rows,
    )

    disabled = resolve_regime_policy_for_plan(
        conn,
        as_of=date(2026, 5, 10),
        config={"portfolio": {"regime_policy": {"enabled": False}}},
    )
    enabled = resolve_regime_policy_for_plan(
        conn,
        as_of=date(2026, 5, 10),
        config={"portfolio": {"regime_policy": {"enabled": True, "benchmark_index": "000300"}}},
    )

    assert disabled is None
    assert enabled is not None
    assert enabled.allow_new_buys is False
    assert enabled.satellite_target_pct <= 0.10
    conn.close()


def test_core_fund_items_are_manual_execution_plan_and_do_not_create_orders():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-15', 100000, 40000, 140000, 140000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO index_fund_signals (
            signal_id, fund_code, index_code, signal_date, action,
            target_weight, confidence, thesis, risk_tags
        )
        VALUES
            ('IFS-1', '510300', '000300', DATE '2026-05-15', 'BUY', 0.60, 0.80, 'core buy', ['underweight']),
            ('IFS-2', '513130', 'HSTECH', DATE '2026-05-15', 'ADD', 0.40, 0.70, 'core add', ['underweight'])
    """)

    plan = create_allocation_plan(
        conn,
        config=AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1000,
            core_cash_priority=True,
        ),
        persist=True,
    )

    rows = conn.execute("""
        SELECT instrument_id, action, execution_mode, expected_cash, cash_effect, budget_consumption
        FROM allocation_plan_items
        WHERE plan_id = ? AND instrument_type = 'index_fund'
        ORDER BY priority
    """, [plan.plan_id]).fetchall()
    order_count = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]

    assert [row[:3] for row in rows] == [
        ("510300", "BUY", "MANUAL"),
        ("513130", "ADD", "MANUAL"),
    ]
    assert [row[3:] for row in rows] == pytest.approx([
        (50_400, -50_400, 50_400),
        (33_600, -33_600, 33_600),
    ])
    assert order_count == 0
    conn.close()


def test_reduce_core_fund_manual_plan_has_positive_expected_cash_and_cash_inflow():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-15', 10000, 40000, 50000, 50000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO index_fund_signals (
            signal_id, fund_code, index_code, signal_date, action,
            target_weight, confidence, thesis, risk_tags
        )
        VALUES ('IFS-1', '510300', '000300', DATE '2026-05-15', 'REDUCE', 1.0, 0.90, 'overweight', ['overweight'])
    """)
    holdings = pd.DataFrame([{"fund_code": "510300", "market_value": 60_000}])
    base_plan = compute_allocation_plan(
        AllocationInputs(
            plan_date=date(2026, 5, 15),
            account_id="default",
            cash=10_000,
            core_value=60_000,
            satellite_value=20_000,
        ),
        AllocationConfig(core_target_pct=0.60, satellite_target_pct=0.40, min_trade_amount=1000),
    )
    signals = conn.execute("SELECT * FROM index_fund_signals").fetchdf()

    from src.portfolio.allocator import attach_core_execution_plan

    plan = attach_core_execution_plan(
        base_plan,
        signals,
        holdings=holdings,
        config=AllocationConfig(core_target_pct=0.60, satellite_target_pct=0.40, min_trade_amount=1000),
    )
    item = [item for item in plan.items if item.instrument_type == "index_fund"][0]

    assert item.action == "REDUCE"
    assert item.execution_mode == "MANUAL"
    assert item.budget_delta == pytest.approx(-6_000)
    assert item.expected_cash == pytest.approx(6_000)
    assert item.cash_effect == pytest.approx(6_000)
    assert item.budget_consumption == pytest.approx(0)
    conn.close()


def test_create_allocation_plan_keeps_pause_fund_signals_without_spending_core_budget():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-15', 60000, 40000, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO index_fund_signals (
            signal_id, fund_code, index_code, signal_date, action,
            target_weight, confidence, thesis, risk_tags
        )
        VALUES
            ('IFS-1', '510300', '000300', DATE '2026-05-15', 'BUY', 0.50, 0.80, 'core buy', ['underweight']),
            ('IFS-2', '513130', 'HSTECH', DATE '2026-05-15', 'PAUSE', 0.50, 0.70, 'pause', ['high_percentile'])
    """)

    plan = create_allocation_plan(
        conn,
        config=AllocationConfig(
            core_target_pct=0.60,
            satellite_target_pct=0.40,
            rebalance_tolerance_pct=0.05,
            min_trade_amount=1000,
            core_cash_priority=True,
        ),
        persist=True,
    )
    fund_items = conn.execute("""
        SELECT instrument_id, action, budget_delta
        FROM allocation_plan_items
        WHERE plan_id = ? AND sleeve = 'core' AND instrument_type = 'index_fund'
        ORDER BY priority
    """, [plan.plan_id]).fetchdf()

    assert plan.core_budget == pytest.approx(60_000)
    assert fund_items["instrument_id"].tolist() == ["510300", "513130"]
    assert fund_items["action"].tolist() == ["BUY", "PAUSE"]
    assert fund_items["budget_delta"].tolist() == pytest.approx([30_000, 0])
    conn.close()
