import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataHealthRibbon } from "../DataHealthRibbon";
import { OperationSummary } from "../OperationSummary";
import { RebalancePlanTable } from "../RebalancePlanTable";
import { RiskAlertStack } from "../RiskAlertStack";

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
          executable: [{ instrument_id: "000001.SZ", action: "BUY", expected_cash: 10000, sleeve: "satellite" }],
          confirm: [{ instrument_id: "510300", action: "BUY", expected_cash: 8000, sleeve: "core" }],
          deferred: [{ instrument_id: "600000.SH", action: "PAUSE", expected_cash: 0, sleeve: "satellite" }],
        }}
      />,
    );

    expect(screen.getByText("可执行")).toBeInTheDocument();
    expect(screen.getByText("需人工确认")).toBeInTheDocument();
    expect(screen.getByText("暂缓")).toBeInTheDocument();
    expect(screen.getByText("000001.SZ")).toBeInTheDocument();
  });

  it("shows empty risk state and concrete risk warnings", () => {
    const { rerender } = render(<RiskAlertStack alerts={[]} />);
    expect(screen.getByText("暂无风险警告")).toBeInTheDocument();

    rerender(<RiskAlertStack alerts={[{ level: "warning", label: "Top5集中度", message: "Top5 权重过高" }]} />);
    const list = screen.getByRole("list");
    expect(within(list).getByText("Top5集中度")).toBeInTheDocument();
    expect(within(list).getByText("Top5 权重过高")).toBeInTheDocument();
  });
});
