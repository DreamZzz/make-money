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


def test_apply_exposure_drives_equity_cash_split():
    from src.portfolio.allocator import AllocationConfig, apply_exposure_to_allocation_config

    cfg = AllocationConfig(core_target_pct=0.50, satellite_target_pct=0.45, cash_target_pct=0.05)
    out = apply_exposure_to_allocation_config(cfg, 0.87)
    # 现金 = 1 - 仓位
    assert abs(out.cash_target_pct - 0.13) < 1e-6
    # 权益(core+satellite) = 目标仓位
    assert abs((out.core_target_pct + out.satellite_target_pct) - 0.87) < 1e-6
    # core:satellite 原比例(0.50:0.45)保持
    assert abs(out.core_target_pct / out.satellite_target_pct - 0.50 / 0.45) < 1e-3
    # None 不改变
    assert apply_exposure_to_allocation_config(cfg, None) == cfg


def test_exposure_lower_target_raises_cash():
    from src.portfolio.allocator import AllocationConfig, apply_exposure_to_allocation_config

    cfg = AllocationConfig(core_target_pct=0.50, satellite_target_pct=0.45, cash_target_pct=0.05)
    defensive = apply_exposure_to_allocation_config(cfg, 0.40)
    assert abs(defensive.cash_target_pct - 0.60) < 1e-6
    assert abs((defensive.core_target_pct + defensive.satellite_target_pct) - 0.40) < 1e-6


def test_m4_index_weights_drive_per_fund_core_targets():
    """A2: M4 index_allocation 权重应覆盖 fund_signals 的静态 target_weight,
    并按 delta 推 ADD/REDUCE,而非沿用 fund signal 的 BUY/PAUSE。"""
    from src.portfolio.allocator import (
        AllocationConfig,
        AllocationInputs,
        attach_core_execution_plan,
        compute_allocation_plan,
    )

    cfg = AllocationConfig(core_target_pct=0.50, satellite_target_pct=0.45, cash_target_pct=0.05)
    plan = compute_allocation_plan(
        AllocationInputs(plan_date=date(2026, 5, 29), cash=50_000, core_value=50_000, satellite_value=0, total_value=100_000),
        cfg,
    )
    # 两只基金,fund_signal 都是 PAUSE(老逻辑会忽略它们)
    signals = pd.DataFrame([
        {"fund_code": "012963", "action": "PAUSE", "target_weight": 0.33, "confidence": 0.5, "thesis": ""},
        {"fund_code": "004192", "action": "PAUSE", "target_weight": 0.33, "confidence": 0.5, "thesis": ""},
    ])
    # M4 权重: 012963 当前 0 但目标 30%(应 ADD 30k); 004192 持仓 40k 但目标 10%(应 REDUCE 30k)
    holdings = pd.DataFrame([{"fund_code": "004192", "market_value": 40_000}])
    new_plan = attach_core_execution_plan(
        plan, signals, holdings, cfg,
        index_weights={"012963": 0.30, "004192": 0.10},
    )
    fund_items = [it for it in new_plan.items if it.instrument_type == "index_fund"]
    by_fund = {it.instrument_id: it for it in fund_items}
    # 012963: target 30k, current 0 -> ADD
    assert by_fund["012963"].action == "ADD"
    assert by_fund["012963"].target_value == 30_000
    # 004192: target 10k, current 40k -> REDUCE
    assert by_fund["004192"].action == "REDUCE"
    assert by_fund["004192"].target_value == 10_000


def test_attach_core_falls_back_when_no_index_weights():
    """A2 向后兼容: 不传 index_weights 时沿用 fund_signal 的 BUY/PAUSE 与静态 target_weight"""
    from src.portfolio.allocator import (
        AllocationConfig,
        AllocationInputs,
        attach_core_execution_plan,
        compute_allocation_plan,
    )

    cfg = AllocationConfig(core_target_pct=0.50, satellite_target_pct=0.45, cash_target_pct=0.05)
    plan = compute_allocation_plan(
        AllocationInputs(plan_date=date(2026, 5, 29), cash=50_000, core_value=0, satellite_value=0, total_value=100_000),
        cfg,
    )
    signals = pd.DataFrame([
        {"fund_code": "012963", "action": "BUY", "target_weight": 1.0, "confidence": 0.5, "thesis": ""},
    ])
    new_plan = attach_core_execution_plan(plan, signals, None, cfg)  # 无 index_weights
    fund_items = [it for it in new_plan.items if it.instrument_type == "index_fund"]
    assert fund_items[0].action == "BUY"  # 沿用 fund_signal 的 BUY
