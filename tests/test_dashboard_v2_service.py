from __future__ import annotations

import duckdb

from src.data_pipeline.loader import init_db


def _seed_dashboard_v2_db(db_path) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        init_db(conn)
        conn.execute("""
            INSERT INTO daily_price (symbol, trade_date, close)
            VALUES ('000001.SZ', DATE '2026-05-15', 10.0)
        """)
        conn.execute("""
            INSERT INTO stock_info (symbol, country, name, industry, market_cap)
            VALUES ('000001.SZ', 'CN', '平安银行', '银行', 3000)
        """)
        conn.execute("""
            INSERT INTO account_daily (
                account_id, trade_date, cash, position_value, total_value, net_contribution, nav, daily_return, drawdown
            )
            VALUES ('default', DATE '2026-05-15', 120000, 180000, 300000, 300000, 1.0, 0.0, 0.0)
        """)
        conn.execute("""
            INSERT INTO allocation_plans (
                plan_id, plan_date, account_id, total_value, cash, core_target_pct, satellite_target_pct,
                core_value, satellite_value, core_budget, satellite_budget, core_drift_pct, satellite_drift_pct
            )
            VALUES (
                'PLAN-1', DATE '2026-05-15', 'default', 300000, 120000, 0.6, 0.4,
                160000, 140000, 20000, 10000, -0.0667, 0.0667
            )
        """)
        conn.execute("""
            INSERT INTO allocation_plan_items (
                plan_id, sleeve, instrument_type, instrument_id, action, current_value, target_value,
                budget_delta, execution_mode, expected_cash, cash_effect, budget_consumption, priority, reason
            )
            VALUES (
                'PLAN-1', 'core', 'index_fund', '510300', 'BUY', 10000, 20000,
                10000, 'MANUAL', 10000, -10000, 10000, 1, 'core补仓'
            )
        """)
    finally:
        conn.close()


def test_dashboard_v2_service_builds_today_from_local_db(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)

    snapshot = DashboardV2Service(db_path=db_path).build_today_snapshot()

    assert snapshot["trade_date"] == "2026-05-15"
    assert snapshot["account"]["total_value"] == 300000
    assert snapshot["operation_summary"]["operation_count"] == 1
    assert snapshot["next_action"]["label"] == "查看调仓计划"


def test_dashboard_v2_safe_writes_persist_audit_log(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    service = DashboardV2Service(db_path=db_path)

    cashflow = service.record_cashflow({
        "flow_date": "2026-05-15",
        "flow_type": "DEPOSIT",
        "amount": 10000,
        "note": "追加资金",
    })
    snapshot = service.record_index_fund_snapshot({
        "snapshot_date": "2026-05-15",
        "fund_code": "510300",
        "shares": 1000,
        "cost_amount": 3800,
        "note": "手动快照",
    })

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        audits = conn.execute("""
            SELECT action, status
            FROM dashboard_audit_log
            ORDER BY created_at, action
        """).fetchall()
        flow_count = conn.execute("SELECT COUNT(*) FROM account_cashflows WHERE flow_id = ?", [cashflow["id"]]).fetchone()[0]
        snap_count = conn.execute(
            "SELECT COUNT(*) FROM index_fund_snapshots WHERE snapshot_id = ?",
            [snapshot["id"]],
        ).fetchone()[0]
    finally:
        conn.close()

    assert flow_count == 1
    assert snap_count == 1
    assert audits == [("cashflow.create", "ok"), ("index_fund_snapshot.create", "ok")]
