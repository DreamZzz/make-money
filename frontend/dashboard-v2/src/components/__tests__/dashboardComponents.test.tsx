import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DataTable } from "../DataTable";
import { DataHealthRibbon } from "../DataHealthRibbon";
import { OperationSummary } from "../OperationSummary";
import { RebalancePlanTable } from "../RebalancePlanTable";
import { RiskAlertStack } from "../RiskAlertStack";
import { AppShell } from "../AppShell";
import { HealthPage } from "../../pages/HealthPage";
import { PortfolioPage } from "../../pages/PortfolioPage";
import { RebalancePage } from "../../pages/RebalancePage";
import { UserGuidePage } from "../../pages/UserGuidePage";

afterEach(cleanup);

describe("Dashboard V2 core components", () => {
  it("renders a blocking health ribbon with the primary status visible", () => {
    render(
      <DataHealthRibbon
        health={{ status: "failed", label: "任务失败", blocking: true, messages: ["收盘闭环失败"] }}
      />,
    );

    expect(screen.getByText("任务失败")).toBeInTheDocument();
    expect(screen.getByText("收盘闭环失败")).toBeInTheDocument();
  });

  it("renders operation count, required cash and estimated minutes", () => {
    render(
      <OperationSummary
        summary={{ operation_count: 3, cash_required: 28000, estimated_minutes: 18, buy_count: 2, reduce_count: 1 }}
      />,
    );

    expect(screen.getByText("3 次")).toBeInTheDocument();
    expect(screen.getByText("¥28,000")).toBeInTheDocument();
    expect(screen.getByText("18 分钟")).toBeInTheDocument();
  });

  it("groups rebalance rows into executable, confirm and deferred sections", () => {
    render(
      <RebalancePlanTable
        groups={{
          budget: [{
            instrument_id: "core",
            instrument_name: "Core 指数基金池",
            display_name: "Core 指数基金池",
            action: "ADD",
            expected_cash: 20000,
            budget_consumption: 20000,
            sleeve: "core",
            instrument_type: "sleeve",
            bucket_reason: "资金池预算，不是交易指令",
          }],
          executable: [{
            instrument_id: "000001.SZ",
            instrument_name: "平安银行",
            display_name: "平安银行（000001.SZ）",
            action: "BUY",
            expected_cash: 10000,
            sleeve: "satellite",
          }],
          confirm: [{
            instrument_id: "510300",
            instrument_name: "华泰柏瑞沪深300ETF",
            display_name: "华泰柏瑞沪深300ETF（510300）",
            action: "BUY",
            expected_cash: 8000,
            sleeve: "core",
          }],
          deferred: [{
            instrument_id: "600000.SH",
            instrument_name: "浦发银行",
            display_name: "浦发银行（600000.SH）",
            action: "PAUSE",
            expected_cash: 0,
            sleeve: "satellite",
          }],
        }}
      />,
    );

    expect(screen.getByText("资金分配")).toBeInTheDocument();
    expect(screen.getByText("资金池预算，不是交易指令")).toBeInTheDocument();
    expect(screen.getByText("可执行")).toBeInTheDocument();
    expect(screen.getByText("需人工确认")).toBeInTheDocument();
    expect(screen.getAllByText("暂缓").length).toBeGreaterThan(0);
    expect(screen.getByText("平安银行（000001.SZ）")).toBeInTheDocument();
    expect(screen.getByText("浦发银行（600000.SH）")).toBeInTheDocument();
    expect(screen.getAllByText("买入").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Satellite 个股").length).toBeGreaterThan(0);
  });

  it("renders semantic table columns with Chinese headers and formatted decimals", () => {
    render(
      <DataTable
        columns={["symbol", "market_value", "weight", "pnl_pct", "confidence", "avg_cost"]}
        rows={[{
          symbol: "000001.SZ",
          name: "平安银行",
          market_value: 12345.67,
          weight: 0.1234,
          pnl_pct: -0.0567,
          confidence: 0.7172,
          avg_cost: 10.1234,
        }]}
      />,
    );

    expect(screen.getByText("标的")).toBeInTheDocument();
    expect(screen.getByText("持仓市值")).toBeInTheDocument();
    expect(screen.getByText("仓位")).toBeInTheDocument();
    expect(screen.getByText("平安银行（000001.SZ）")).toBeInTheDocument();
    expect(screen.getByText("¥12,346")).toBeInTheDocument();
    expect(screen.getByText("12.3%")).toBeInTheDocument();
    expect(screen.getByText("-5.7%")).toBeInTheDocument();
    expect(screen.getByText("71.7%")).toBeInTheDocument();
    expect(screen.getByText("10.12")).toBeInTheDocument();
  });

  it("explains satellite stock candidates as budget checks instead of manual orders", () => {
    render(
      <RebalancePage
        data={{
          plan_id: "PLAN-1",
          plan_date: "2026-05-15",
          summary: { operation_count: 1, cash_required: 0, estimated_minutes: 6, buy_count: 0, reduce_count: 1, funding_gap: 0 },
          groups: { budget: [], executable: [], confirm: [], deferred: [] },
          sell_signals: [{
            symbol: "000001.SZ",
            name: "平安银行",
            display_name: "平安银行（000001.SZ）",
            strategy_name: "alpha158",
            quantity: 1000,
            market_value: 10000,
            pnl: -1000,
            pnl_pct: -0.0909,
            confidence: 0.61,
            model_name: "mean_reversion",
            signal_count: 1,
            decision: "已触发 SELL，且浮亏超过 8%；建议优先在纸交易预览中确认卖出。",
          }],
          conflicts: [],
          one_lot_gaps: [],
          satellite_candidates: {
            budget: 19987.5,
            base_budget: 10000,
            sell_release_estimate: 9987.5,
            candidate_count: 2,
            covered_count: 2,
            over_budget_count: 0,
            executable_count: 2,
            budget_blocked_count: 0,
            threshold_blocked_count: 1,
            decision_hint: "优先关注 2 只预算够且过门槛的候选；运行纸交易后还会继续经过风控、换手和可交易性检查。",
            rows: [{
              symbol: "688001.SH",
              name: "华兴源创",
              display_name: "华兴源创（688001.SH）",
              one_lot_cash: 16000,
              confidence: 0.81,
              rank_score: 0.729,
              budget_status: "covered",
              budget_status_label: "预算可覆盖",
              execution_status: "executable_candidate",
              execution_status_label: "过门槛且预算够",
              budget_gap: 0,
              decision: "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。",
            }, {
              symbol: "000002.SZ",
              name: "万科A",
              display_name: "万科A（000002.SZ）",
              one_lot_cash: 3000,
              confidence: 0.82,
              rank_score: 0.656,
              budget_status: "covered",
              budget_status_label: "预算可覆盖",
              execution_status: "executable_candidate",
              execution_status_label: "过门槛且预算够",
              budget_gap: 0,
              decision: "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。",
            }],
          },
          evidence: {},
        }}
      />,
    );

    expect(screen.getByText("持仓卖出信号")).toBeInTheDocument();
    expect(screen.getByText("平安银行（000001.SZ）")).toBeInTheDocument();
    expect(screen.getByText("-9.1%")).toBeInTheDocument();
    expect(screen.getByText(/浮亏超过 8%/)).toBeInTheDocument();
    expect(screen.getByText("Satellite 股票候选")).toBeInTheDocument();
    expect(screen.getByText(/低于执行门槛的记录会前置过滤/)).toBeInTheDocument();
    expect(screen.getByText("基础预算")).toBeInTheDocument();
    expect(screen.getByText("SELL预计释放")).toBeInTheDocument();
    expect(screen.getByText("有效BUY预算")).toBeInTheDocument();
    expect(screen.getAllByText("¥10,000").length).toBeGreaterThan(0);
    expect(screen.getByText("¥19,988")).toBeInTheDocument();
    expect(screen.getAllByText("预算可覆盖").length).toBeGreaterThan(0);
    expect(screen.getAllByText("过门槛且预算够").length).toBeGreaterThan(0);
    expect(screen.getByText("门槛过滤")).toBeInTheDocument();
    expect(screen.getByText("华兴源创（688001.SH）")).toBeInTheDocument();
    expect(screen.getAllByText("可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。").length).toBeGreaterThan(0);
  });

  it("shows every satellite candidate in a full-width readable table", () => {
    const rows = Array.from({ length: 10 }, (_, index) => ({
      symbol: `00000${index}.SZ`,
      name: `候选股票${index}`,
      display_name: `候选股票${index}（00000${index}.SZ）`,
      one_lot_cash: 1000 + index * 100,
      confidence: 0.6 + index * 0.01,
      rank_score: 0.6 + index * 0.01,
      model_name: "trend_following",
      budget_status: "covered",
      budget_status_label: "预算可覆盖",
      execution_status: "executable_candidate",
      execution_status_label: "过门槛且预算够",
      budget_gap: 0,
      decision: "可进入纸交易执行队列；仍需通过持仓上限、换手率和可交易性检查。",
    }));

    const { container } = render(
      <RebalancePage
        data={{
          plan_id: "PLAN-1",
          plan_date: "2026-05-15",
          summary: { operation_count: 0, cash_required: 0, estimated_minutes: 0, buy_count: 0, reduce_count: 0, funding_gap: 0 },
          groups: { budget: [], executable: [], confirm: [], deferred: [] },
          sell_signals: [],
          conflicts: [],
          one_lot_gaps: [],
          satellite_candidates: {
            budget: 10000,
            candidate_count: rows.length,
            covered_count: rows.length,
            over_budget_count: 0,
            executable_count: rows.length,
            budget_blocked_count: 0,
            threshold_blocked_count: 0,
            decision_hint: "优先关注 10 只预算够且过门槛的候选；运行纸交易后还会继续经过风控、换手和可交易性检查。",
            rows,
          },
          evidence: {},
        }}
      />,
    );

    expect(screen.getByText("一手资金")).toBeInTheDocument();
    expect(screen.getByText("置信度 / 模型")).toBeInTheDocument();
    expect(screen.getByText("执行资格")).toBeInTheDocument();
    expect(screen.getByText("预算状态")).toBeInTheDocument();
    expect(screen.getByText("用户决策")).toBeInTheDocument();
    expect(screen.getByText("候选股票9（000009.SZ）")).toBeInTheDocument();
    expect(container.querySelector(".satellite-candidates")?.closest(".two-column")).toBeNull();
    expect(container.querySelector(".satellite-candidates")?.closest(".rebalance-wide-section")).not.toBeNull();
  });

  it("renders scheduled job execution history on the health page", () => {
    render(
      <HealthPage
        data={{
          status: "ok",
          label: "数据可用",
          blocking: false,
          messages: [],
          latest_quote_date: "2026-05-15",
          data_sources: [],
          field_coverage: [{
            scope_label: "当前持仓",
            field_label: "行业",
            covered_display: "13/13",
            coverage: 1,
            coverage_status: "可用",
            decision_use: "直接影响组合体检可信度",
          }],
          scheduled_jobs: [{
            label: "收盘闭环",
            trigger: "工作日 20:00（watchdog 每 5 分钟检查）",
            watchdog_status: "WAITING",
            watchdog_status_label: "等待窗口",
            next_due_at: "2026-05-18T20:00:00",
            last_run_date: "2026-05-17",
            last_result: "执行完成",
            plist_status: "存在",
            action_hint: "由 StartInterval watchdog 检查执行窗口并防重复；Dashboard 只展示状态和异常提醒。",
          }],
          scheduled_job_history: [{
            job_name: "收盘闭环",
            scheduled_time: "20:00",
            started_at: "2026-05-17 20:00:04",
            ended_at: "2026-05-17 20:03:57",
            duration_seconds: 233,
            schedule_alignment: "异常时间",
            schedule_note: "计划 20:00，实际 11:00，偏离 -540 分钟",
            status: "SUCCEEDED",
            status_label: "成功（异常时间）",
            result: "执行完成",
          }, {
            job_name: "开盘纸交易",
            scheduled_time: "09:40",
            started_at: "2026-05-16 09:40:01",
            ended_at: "2026-05-16 09:51:29",
            duration_seconds: 688,
            status: "SUCCEEDED",
            status_label: "成功",
            result: "目标更新 targets=95 updated=95 no_data=0",
          }],
          qlib: {},
          latest_job: null,
          failure_diagnostic: null,
        }}
      />,
    );

    expect(screen.getByText("定时执行历史")).toBeInTheDocument();
    expect(screen.getByText("定时任务")).toBeInTheDocument();
    expect(screen.getByText("字段覆盖率")).toBeInTheDocument();
    expect(screen.getByText("当前持仓")).toBeInTheDocument();
    expect(screen.getByText("13/13")).toBeInTheDocument();
    expect(screen.getByText("直接影响组合体检可信度")).toBeInTheDocument();
    expect(screen.getByText("等待窗口")).toBeInTheDocument();
    expect(screen.getByText("下次应执行")).toBeInTheDocument();
    expect(screen.getByText("执行时间")).toBeInTheDocument();
    expect(screen.getByText("时间判断")).toBeInTheDocument();
    expect(screen.getByText("异常时间")).toBeInTheDocument();
    expect(screen.getByText("执行状态")).toBeInTheDocument();
    expect(screen.getByText("执行结果")).toBeInTheDocument();
    expect(screen.getAllByText("收盘闭环").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("开盘纸交易")).toBeInTheDocument();
    expect(screen.getByText("目标更新 targets=95 updated=95 no_data=0")).toBeInTheDocument();
  });

  it("shows empty risk state and concrete risk warnings", () => {
    const { rerender } = render(<RiskAlertStack alerts={[]} />);
    expect(screen.getByText("暂无风险警告")).toBeInTheDocument();

    rerender(<RiskAlertStack alerts={[{ level: "warning", label: "Top5集中度", message: "Top5 权重过高" }]} />);
    const list = screen.getByRole("list");
    expect(within(list).getByText("Top5集中度")).toBeInTheDocument();
    expect(within(list).getByText("Top5 权重过高")).toBeInTheDocument();
  });

  it("adds the product guide to the primary navigation", () => {
    render(
      <AppShell
        route="/guide"
        health={{ status: "ok", label: "数据可用", blocking: false, messages: [] }}
        onNavigate={() => undefined}
      >
        <UserGuidePage />
      </AppShell>,
    );

    expect(screen.getByRole("button", { name: "使用手册" })).toHaveClass("nav-item--active");
    expect(screen.getByRole("heading", { name: "产品使用手册" })).toBeInTheDocument();
  });

  it("renders the guide with expectation management, onboarding and glossary thresholds", () => {
    render(<UserGuidePage />);

    expect(screen.getAllByText("在你开始之前").length).toBeGreaterThan(0);
    expect(screen.getByText("年化超额收益")).toBeInTheDocument();
    expect(screen.getAllByText(/3-8%/).length).toBeGreaterThan(0);
    expect(screen.getByText("第一次使用：从零到首次调仓")).toBeInTheDocument();
    expect(screen.getByText(/small <= 10 万/)).toBeInTheDocument();
    expect(screen.getByText("每周复盘的量化门槛")).toBeInTheDocument();
    expect(screen.getByText(/30 日累计落后基准 >= 5 个百分点/)).toBeInTheDocument();
    expect(screen.getByText("如何读信号收益跟踪")).toBeInTheDocument();
    expect(screen.getAllByText("alpha_vs_benchmark").length).toBeGreaterThan(0);
    expect(screen.getByText("异常场景应急")).toBeInTheDocument();
    expect(screen.getByText(/0.02 算及格/)).toBeInTheDocument();
  });

  it("turns portfolio health into actionable diagnostics", () => {
    render(
      <PortfolioPage
        data={{
          account: { total_value: 300000, cash: 120000, position_value: 180000, drawdown: -0.03 },
          holdings: [{
            symbol: "000001.SZ",
            name: "平安银行",
            display_name: "平安银行（000001.SZ）",
            industry: "银行",
            market_value: 10000,
            weight: 0.0333,
            pnl_pct: -0.0909,
            holding_days: 14,
            weight_change_7d: 0.0083,
            weight_change_20d: null,
            qlib_rank: 4,
            qlib_confidence: 0.91,
            latest_signal_side: "BUY,SELL",
          }],
          risk_alerts: [{
            metric: "top1_weight",
            label: "最大单票",
            severity: "WARN",
            detail: "最大单票 18.0%，上限 15.0%",
            severity_reason: "主要由平安银行（000001.SZ）贡献。",
            suggested_actions: ["不新增该标的，优先等待 SELL 或再平衡信号。"],
            affected_holdings: [{
              symbol: "000001.SZ",
              name: "平安银行",
              display_name: "平安银行（000001.SZ）",
              weight: 0.18,
              pnl_pct: -0.0909,
            }],
          }],
          exposure: {
            industry: [],
            size: [],
            summary: {
              position_count: 1,
              top5_weight: 0.18,
              pe_coverage: 1,
              pb_coverage: 1,
            },
            insights: [{
              key: "industry",
              title: "最大行业暴露",
              message: "银行权重最高，组合可能被单一行业波动牵动。",
              suggested_action: "暂停新增该行业，优先处理低置信度或已有 SELL 信号的标的。",
              affected_holdings: [{
                symbol: "000001.SZ",
                display_name: "平安银行（000001.SZ）",
                weight: 0.18,
              }],
            }],
          },
          signal_outcomes: {
            summary: [],
            monthly: [],
            detail: [],
            state: {
              status: "empty",
              message: "暂无信号收益数据：尚未产生可跟踪的纸交易成交；成交后需等待 T+1/T+5/T+20 到期。",
              ready_count: 0,
              pending_count: 0,
              total_count: 0,
              next_ready_date: null,
            },
          },
        }}
      />,
    );

    expect(screen.getByText("风险处置清单")).toBeInTheDocument();
    expect(screen.getByText("影响标的")).toBeInTheDocument();
    expect(screen.getAllByText("平安银行（000001.SZ）").length).toBeGreaterThan(0);
    expect(screen.getByText(/不新增该标的/)).toBeInTheDocument();
    expect(screen.getByText("暴露解释")).toBeInTheDocument();
    expect(screen.getByText("最大行业暴露")).toBeInTheDocument();
    expect(screen.getByText(/单一行业波动/)).toBeInTheDocument();
    expect(screen.getByText("已持有天数")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("7日仓位变化")).toBeInTheDocument();
    expect(screen.getByText("Qlib排名")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/尚未产生可跟踪的纸交易成交/)).toBeInTheDocument();
  });
});
