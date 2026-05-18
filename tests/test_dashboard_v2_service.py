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
            INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency)
            VALUES ('510300', '华泰柏瑞沪深300ETF', 'ETF', '000300', 'CN', 'CNY')
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
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, symbol, signal_ts, score, side, confidence, status
            )
            VALUES
                ('SIG-1', 'trend_following', '000001.SZ', TIMESTAMP '2026-05-15 15:30:00', 0.8, 'BUY', 0.72, 'ACTIVE'),
                ('SIG-2', 'trend_following', '000001.SZ', TIMESTAMP '2026-05-15 15:30:00', 0.8, 'BUY', 0.72, 'ACTIVE'),
                ('SIG-3', 'mean_reversion', '000001.SZ', TIMESTAMP '2026-05-15 15:30:00', -0.4, 'SELL', 0.61, 'ACTIVE')
        """)
        conn.execute("""
            INSERT INTO paper_positions (
                strategy_name, trade_date, symbol, quantity, avg_cost, current_price, market_value, pnl, pnl_pct, weight
            )
            VALUES ('alpha158', DATE '2026-05-15', '000001.SZ', 1000, 11.0, 10.0, 10000, -1000, -0.0909, 0.0333)
        """)
        conn.execute("""
            INSERT INTO model_monitor_alerts (
                alert_id, model_name, model_version, experiment_id, alert_date,
                severity, metric_name, observed_value, threshold_value, status,
                message, context_json
            )
            VALUES (
                'ALERT-1', 'alpha158', 'alpha158-prod', 'EXP-PROD', DATE '2026-05-15',
                'WARN', 'production_prediction_missing', NULL, NULL, 'ACTIVE',
                'production 模型尚未生成生产预测截面', '{}'
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
    assert snapshot["health"]["model_monitor"]["status"] == "degraded"
    assert snapshot["health"]["model_monitor"]["active_alert_count"] == 1


def test_dashboard_v2_today_never_starts_jobs_when_plan_is_missing(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("DELETE FROM allocation_plan_items")
        conn.execute("DELETE FROM allocation_plans")
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_today_snapshot()

    assert snapshot["next_action"] == {
        "label": "查看任务状态",
        "href": "/health",
        "enabled": True,
    }
    assert "job_key" not in snapshot["next_action"]


def test_dashboard_v2_portfolio_explains_risks_holdings_and_empty_outcomes(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            INSERT INTO paper_positions (
                strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
                market_value, pnl, pnl_pct, weight
            )
            VALUES
                ('alpha158', DATE '2026-05-01', '000001.SZ', 800, 11.5, 11.0, 8800, -400, -0.0435, 0.0200),
                ('alpha158', DATE '2026-05-08', '000001.SZ', 900, 11.2, 10.8, 9720, -360, -0.0357, 0.0250)
        """)
        conn.execute("""
            UPDATE daily_price
            SET pe_ttm = 6.5, pb = 0.8
            WHERE symbol = '000001.SZ' AND trade_date = DATE '2026-05-15'
        """)
        conn.execute("""
            INSERT INTO qlib_predictions (
                experiment_id, model_name, model_version, mode, prediction_date,
                symbol, score, rank, confidence, selected
            )
            VALUES (
                'EXP-PROD', 'alpha158', 'alpha158-prod', 'production_inference',
                DATE '2026-05-15', '000001.SZ', 0.88, 4, 0.91, TRUE
            )
        """)
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_portfolio_snapshot()

    top1 = next(alert for alert in snapshot["risk_alerts"] if alert["metric"] == "top1_weight")
    assert top1["affected_holdings"][0]["display_name"] == "平安银行（000001.SZ）"
    assert top1["affected_holdings"][0]["weight"] == 0.0333
    assert "不新增" in top1["suggested_actions"][0]
    assert "平安银行" in top1["severity_reason"]

    industry_card = next(card for card in snapshot["exposure"]["insights"] if card["key"] == "industry")
    assert industry_card["title"] == "最大行业暴露"
    assert "银行" in industry_card["message"]
    assert industry_card["affected_holdings"][0]["display_name"] == "平安银行（000001.SZ）"

    holding = snapshot["holdings"][0]
    assert holding["display_name"] == "平安银行（000001.SZ）"
    assert holding["holding_days"] == 14
    assert holding["weight_change_7d"] == 0.0083
    assert holding["weight_change_20d"] is None
    assert holding["qlib_rank"] == 4
    assert holding["qlib_confidence"] == 0.91
    assert holding["qlib_prediction_date"] == "2026-05-15"
    assert holding["latest_signal_side"] == "BUY,SELL"
    assert holding["latest_signal_count"] == 3

    assert snapshot["signal_outcomes"]["state"] == {
        "status": "empty",
        "message": "暂无信号收益数据：尚未产生可跟踪的纸交易成交；成交后需等待 T+1/T+5/T+20 到期。",
        "ready_count": 0,
        "pending_count": 0,
        "total_count": 0,
        "next_ready_date": None,
    }


def test_dashboard_v2_health_field_coverage_is_scoped_by_decision_context(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            INSERT INTO stock_info (symbol, country, name, industry, market_cap)
            VALUES
                ('000002.SZ', 'CN', '万科A', NULL, NULL),
                ('00700', 'HK', '腾讯控股', '互联网', 30000)
        """)
        conn.execute("""
            INSERT INTO daily_price (symbol, trade_date, close, pe_ttm, pb)
            VALUES
                ('000002.SZ', DATE '2026-05-15', 8.0, 12.0, 0.9),
                ('00700', DATE '2026-05-15', 400.0, NULL, NULL)
        """)
        conn.execute("""
            UPDATE daily_price
            SET pe_ttm = 6.5, pb = 0.8
            WHERE symbol = '000001.SZ' AND trade_date = DATE '2026-05-15'
        """)
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, symbol, signal_ts, score, side, confidence, status
            )
            VALUES
                ('SIG-4', 'trend_following', '000002.SZ', TIMESTAMP '2026-05-15 15:30:00', 0.7, 'BUY', 0.76, 'ACTIVE')
        """)
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_health_snapshot()
    rows = {
        (row["scope"], row["field"]): row
        for row in snapshot["field_coverage"]
    }

    assert rows[("current_holdings", "industry")]["covered_display"] == "1/1"
    assert rows[("signal_candidates", "industry")]["covered_display"] == "1/2"
    assert rows[("target_universe", "industry")]["covered_display"] == "1/2"
    assert rows[("local_market", "industry")]["covered_display"] == "2/3"
    assert rows[("target_universe", "pe_ttm")]["covered_display"] == "2/2"
    assert rows[("local_market", "pe_ttm")]["decision_use"] == "市场观察/研究扩展，不阻塞今日调仓"


def test_scheduled_job_log_parser_extracts_open_and_close_history() -> None:
    from src.dashboard_v2.service import _parse_scheduled_job_log

    close_log = """
=== 2026-05-16 20:00:03 开始每日收盘流程 ===
Python: /opt/homebrew/bin/python3.12
2026-05-16 20:03:41.439 | INFO | __main__:_run_signal_batch:490 - Paper engine: trend_following executed 0/400 signals
=== 2026-05-16 20:03:57 结束 ===
=== 2026-05-17 20:00:04 开始每日收盘流程 ===
2026-05-17 20:01:31.696 | INFO | __main__:update_all:373 - 增量更新汇总: CN attempted=708 updated=0 no_data=0 source_error=708 failed=0
退出码: 1
=== 2026-05-17 20:01:31 结束 ===
"""
    open_log = """
=== 2026-05-16 09:40:01 开盘纸交易任务开始 ===
目标行情更新汇总: targets=95 updated=95 no_data=0 skipped=0 snapshot_ready=0 status=SUCCEEDED
OPEN_TARGET_UPDATE_SUMMARY_JSON: {"targets": 95, "updated": 95, "no_data": 0, "skipped": 0, "snapshot_ready": 0, "status": "SUCCEEDED", "exit_code": 0}
--- 执行股票纸交易 exit=0 ---
2026-05-16 09:51:28.874 | INFO | __main__:_run_signal_batch:448 - Paper engine: trend_following executed 0/160 signals
=== 2026-05-16 09:51:29 开盘纸交易任务结束 ===
"""

    history = (
        _parse_scheduled_job_log("daily_close", "收盘闭环", close_log, "cron.log")
        + _parse_scheduled_job_log("open_paper_trade", "开盘纸交易", open_log, "open_trade.log")
    )

    assert [(row["job_name"], row["started_at"], row["ended_at"], row["status"]) for row in history] == [
        ("收盘闭环", "2026-05-16 20:00:03", "2026-05-16 20:03:57", "SUCCEEDED"),
        ("收盘闭环", "2026-05-17 20:00:04", "2026-05-17 20:01:31", "FAILED"),
        ("开盘纸交易", "2026-05-16 09:40:01", "2026-05-16 09:51:29", "SUCCEEDED"),
    ]
    assert history[0]["duration_seconds"] == 234
    assert history[0]["schedule_alignment"] == "按计划"
    assert history[1]["result"] == "退出码 1；增量更新汇总: CN attempted=708 updated=0 no_data=0 source_error=708 failed=0"
    assert history[2]["result"] == "目标更新 targets=95 updated=95 no_data=0；Paper engine: trend_following executed 0/160 signals"


def test_scheduled_job_log_parser_flags_old_off_schedule_close_run() -> None:
    from src.dashboard_v2.service import _parse_scheduled_job_log

    close_log = """
=== 2026-05-18 11:00:01 开始每日收盘流程 ===
2026-05-18 11:26:21.636 | INFO     | __main__:_run_signal_batch:493 - Paper engine: industry_rotation executed 0/4 signals
=== 2026-05-18 11:26:24 结束 ===
"""

    history = _parse_scheduled_job_log("daily_close", "收盘闭环", close_log, "cron.log")

    assert history[0]["status"] == "SUCCEEDED"
    assert history[0]["status_label"] == "成功（异常时间）"
    assert history[0]["schedule_alignment"] == "异常时间"
    assert history[0]["schedule_note"] == "计划 20:00，实际 11:00，偏离 -540 分钟"


def test_scheduled_job_log_parser_marks_interrupted_old_run_failed() -> None:
    from src.dashboard_v2.service import _parse_scheduled_job_log

    close_log = """
=== 2026-05-15 20:00:02 开始每日收盘流程 ===
拉取数据...
=== 2026-05-16 20:00:03 开始每日收盘流程 ===
=== 2026-05-16 20:03:57 结束 ===
"""

    history = _parse_scheduled_job_log("daily_close", "收盘闭环", close_log, "cron.log")

    assert history[0]["started_at"] == "2026-05-15 20:00:02"
    assert history[0]["status"] == "FAILED"
    assert history[0]["status_label"] == "未正常结束"
    assert history[0]["result"] == "未找到结束记录"
    assert history[1]["status"] == "SUCCEEDED"


def test_scheduler_watchdog_state_reader_normalizes_rows(tmp_path) -> None:
    from src.dashboard_v2.service import _load_scheduler_watchdog_state

    state_path = tmp_path / "scheduler_state.json"
    state_path.write_text("""
{
  "version": 1,
  "updated_at": "2026-05-18T11:30:00",
  "jobs": {
    "open_paper_trade": {
      "status": "MISSED",
      "last_run_date": "2026-05-18",
      "next_due_at": "2026-05-19T09:40:00",
      "result": "错过执行窗口：计划 09:40，窗口至 10:30，未补跑"
    }
  }
}
""")

    state = _load_scheduler_watchdog_state(state_path)

    assert state["updated_at"] == "2026-05-18T11:30:00"
    assert state["jobs"]["open_paper_trade"]["status"] == "MISSED"
    assert state["jobs"]["open_paper_trade"]["status_label"] == "已错过"
    assert state["jobs"]["open_paper_trade"]["last_result"] == "错过执行窗口：计划 09:40，窗口至 10:30，未补跑"


def test_health_snapshot_uses_scheduler_latest_instead_of_legacy_job_manager(monkeypatch, tmp_path) -> None:
    from src.dashboard_v2 import service as dashboard_service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)

    class LegacyFailedRun:
        data = {
            "run_id": "JOB-DAILY_CLOSE_WORKFLOW-OLD",
            "job_key": "daily_close_workflow",
            "job_label": "日常收盘闭环",
            "status": "FAILED",
            "started_at": "2026-05-18T18:58:40",
            "ended_at": "2026-05-18T19:14:30",
            "steps": [{"key": "update", "label": "更新行情数据", "status": "FAILED"}],
        }

    monkeypatch.setattr(dashboard_service.job_manager, "latest_run", lambda job_key=None: LegacyFailedRun())
    monkeypatch.setattr(
        dashboard_service,
        "_load_scheduled_job_history",
        lambda limit=12: [{
            "job_key": "daily_close",
            "job_name": "收盘闭环",
            "scheduled_time": "20:00",
            "started_at": "2026-05-18 20:00:17",
            "ended_at": "2026-05-18 20:10:43",
            "duration_seconds": 626,
            "source_log": "cron.log",
            "status": "SUCCEEDED",
            "status_label": "成功",
            "result": "Paper engine: industry_rotation executed 0/8 signals",
            "schedule_alignment": "按计划",
            "schedule_note": "计划 20:00，实际 20:00",
        }],
    )

    snapshot = dashboard_service.DashboardV2Service(db_path=db_path).build_health_snapshot()

    assert snapshot["latest_job"]["job_key"] == "daily_close"
    assert snapshot["latest_job"]["job_label"] == "收盘闭环"
    assert snapshot["latest_job"]["status"] == "SUCCEEDED"
    assert snapshot["latest_job"]["status_label"] == "成功"
    assert snapshot["failure_diagnostic"] is None
    assert not any("最近任务失败" in message for message in snapshot["messages"])


def test_dashboard_v2_rebalance_adds_names_and_deduplicates_repeated_signal_rows(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)

    snapshot = DashboardV2Service(db_path=db_path).build_rebalance_snapshot()

    item = snapshot["groups"]["confirm"][0]
    assert item["instrument_name"] == "华泰柏瑞沪深300ETF"
    assert item["display_name"] == "华泰柏瑞沪深300ETF（510300）"

    assert snapshot["conflicts"] == [{
        "symbol": "000001.SZ",
        "name": "平安银行",
        "display_name": "平安银行（000001.SZ）",
        "side_count": 2,
        "sides": "BUY,SELL",
        "signal_count": 3,
    }]
    assert snapshot["sell_signals"] == [{
        "symbol": "000001.SZ",
        "name": "平安银行",
        "display_name": "平安银行（000001.SZ）",
        "market": "CN",
        "strategy_name": "alpha158",
        "quantity": 1000.0,
        "market_value": 10000.0,
        "estimated_release_cash": 9987.5,
        "pnl": -1000.0,
        "pnl_pct": -0.0909,
        "confidence": 0.61,
        "score": -0.4,
        "model_name": "mean_reversion",
        "signal_count": 1,
        "signal_date": "2026-05-15",
        "decision": "已触发 SELL，且浮亏超过 8%；建议优先在纸交易预览中确认卖出。",
    }]
    assert snapshot["one_lot_gaps"] == []


def test_dashboard_v2_rebalance_separates_budget_envelopes_from_trade_actions(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("DELETE FROM allocation_plan_items WHERE plan_id = 'PLAN-1'")
        conn.execute("""
            INSERT INTO fund_info (fund_code, name, fund_type, tracking_index, market, currency)
            VALUES
                ('012963', '招商稳健平衡混合A', 'OPEN', '000300', 'CN', 'CNY'),
                ('013308', '易方达恒生科技ETF联接(QDII)A', 'OPEN', 'HSTECH', 'CN', 'CNY'),
                ('004192', '招商中证500指数增强A', 'OPEN', '000905', 'CN', 'CNY')
        """)
        conn.execute("""
            INSERT INTO allocation_plan_items (
                plan_id, sleeve, instrument_type, instrument_id, action, current_value, target_value,
                budget_delta, execution_mode, expected_cash, cash_effect, budget_consumption, priority, reason
            )
            VALUES
                ('PLAN-1', 'core', 'sleeve', 'core', 'ADD', 160000, 180000, 20000, 'BUDGET', 20000, -20000, 20000, 1, 'Core低配，预留预算'),
                ('PLAN-1', 'satellite', 'sleeve', 'satellite', 'ADD', 140000, 120000, 10000, 'BUDGET', 10000, -10000, 10000, 2, 'Satellite预留预算'),
                ('PLAN-1', 'core', 'index_fund', '012963', 'REDUCE', 90000, 95000, 0, 'MANUAL', 0, 0, 0, 3, '减仓信号但无需执行'),
                ('PLAN-1', 'core', 'index_fund', '013308', 'REDUCE', 105000, 95000, -10000, 'MANUAL', 10000, 10000, 0, 4, '基金高于目标，建议减仓'),
                ('PLAN-1', 'core', 'index_fund', '004192', 'PAUSE', 1000, 90000, 0, 'MANUAL', 0, 0, 0, 5, '暂停新增')
        """)
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_rebalance_snapshot()

    assert [item["display_name"] for item in snapshot["groups"]["budget"]] == [
        "Core 指数基金池",
        "Satellite 个股策略池",
    ]
    assert [item["display_name"] for item in snapshot["groups"]["confirm"]] == [
        "易方达恒生科技ETF联接(QDII)A（013308）",
    ]
    assert [item["display_name"] for item in snapshot["groups"]["deferred"]] == [
        "招商稳健平衡混合A（012963）",
        "招商中证500指数增强A（004192）",
    ]
    assert snapshot["summary"]["operation_count"] == 1
    assert snapshot["summary"]["cash_required"] == 0
    assert snapshot["summary"]["buy_count"] == 0
    assert snapshot["summary"]["reduce_count"] == 1
    assert snapshot["summary"]["funding_gap"] == 0


def test_dashboard_v2_rebalance_explains_satellite_candidates_against_budget(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            INSERT INTO daily_price (symbol, trade_date, close)
            VALUES ('688001.SH', DATE '2026-05-15', 160.0)
        """)
        conn.execute("""
            INSERT INTO daily_price (symbol, trade_date, close)
            VALUES ('000002.SZ', DATE '2026-05-15', 30.0)
        """)
        conn.execute("""
            INSERT INTO stock_info (symbol, country, name, industry, market_cap)
            VALUES ('688001.SH', 'CN', '华兴源创', '电子', 800)
        """)
        conn.execute("""
            INSERT INTO stock_info (symbol, country, name, industry, market_cap)
            VALUES ('000002.SZ', 'CN', '万科A', '房地产', 1200)
        """)
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, symbol, signal_ts, score, side, confidence, status
            )
            VALUES
                ('SIG-4', 'alpha158_v1', '688001.SH', TIMESTAMP '2026-05-15 15:30:00', 0.9, 'BUY', 0.81, 'ACTIVE'),
                ('SIG-5', 'alpha158_v1', '000002.SZ', TIMESTAMP '2026-05-15 15:30:00', 0.8, 'BUY', 0.82, 'ACTIVE')
        """)
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_rebalance_snapshot()

    candidates = snapshot["satellite_candidates"]
    assert candidates["budget"] == 19987.5
    assert candidates["base_budget"] == 10000
    assert candidates["sell_release_estimate"] == 9987.5
    assert candidates["candidate_count"] == 2
    assert candidates["covered_count"] == 2
    assert candidates["over_budget_count"] == 0
    assert candidates["executable_count"] == 2
    assert candidates["budget_blocked_count"] == 0
    assert candidates["threshold_blocked_count"] == 1
    assert candidates["decision_hint"] == "优先关注 2 只预算够且过门槛的候选；运行纸交易后还会继续经过风控、换手和可交易性检查。"
    assert [
        (row["display_name"], row["execution_status_label"], row["budget_status_label"], row["budget_gap"], row["decision"])
        for row in candidates["rows"]
    ] == [
        ("华兴源创（688001.SH）", "过门槛且预算够", "预算可覆盖", 0.0, "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。"),
        ("万科A（000002.SZ）", "过门槛且预算够", "预算可覆盖", 0.0, "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。"),
    ]


def test_dashboard_v2_health_blocks_when_production_signal_lags_prediction(tmp_path) -> None:
    from src.dashboard_v2.service import DashboardV2Service

    db_path = tmp_path / "dashboard_v2.duckdb"
    _seed_dashboard_v2_db(db_path)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            INSERT INTO qlib_model_registry (
                model_version, experiment_id, model_name, status, market, published_at
            )
            VALUES ('alpha158-prod', 'EXP-PROD', 'alpha158', 'production', 'CN', TIMESTAMP '2026-05-17 16:00:00')
        """)
        conn.execute("""
            INSERT INTO qlib_predictions (
                experiment_id, model_name, model_version, mode, prediction_date, symbol, score, rank, confidence, selected
            )
            VALUES ('EXP-PROD', 'alpha158', 'alpha158-prod', 'walk_forward', DATE '2026-05-15', '000001.SZ', 0.8, 1, 0.9, TRUE)
        """)
        conn.execute("""
            INSERT INTO signals (
                signal_id, model_name, model_version, symbol, signal_ts, score, side, confidence, status
            )
            VALUES ('SIG-OLD-ALPHA', 'alpha158', 'alpha158-old', '000001.SZ', TIMESTAMP '2026-05-13 15:00:00', 0.6, 'BUY', 0.8, 'ACTIVE')
        """)
    finally:
        conn.close()

    snapshot = DashboardV2Service(db_path=db_path).build_health_snapshot()

    assert [job["label"] for job in snapshot["scheduled_jobs"]] == ["收盘闭环", "开盘纸交易"]
    assert all("Dashboard 只展示状态" in job["action_hint"] for job in snapshot["scheduled_jobs"])
    assert all(job["launch_label"] == "com.quant.scheduler-watchdog" for job in snapshot["scheduled_jobs"])

    freshness = snapshot["qlib"]["signal_freshness"]
    assert freshness["status"] == "stale"
    assert freshness["blocking"] is True
    assert freshness["latest_prediction_date"] == "2026-05-15"
    assert freshness["latest_signal_date"] is None
    assert snapshot["blocking"] is True
    assert "Alpha158 production 信号滞后：预测日期 2026-05-15，信号日期 -" in snapshot["messages"]


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
