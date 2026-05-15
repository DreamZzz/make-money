from datetime import date

import duckdb
import pytest

from src.data_pipeline.loader import init_db
from src.portfolio.allocator import (
    AllocationConfig,
    AllocationInputs,
    compute_allocation_plan,
    create_allocation_plan,
    persist_allocation_plan,
)


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
