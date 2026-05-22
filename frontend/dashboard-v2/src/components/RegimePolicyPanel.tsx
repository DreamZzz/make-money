import { ShieldAlert } from "lucide-react";

import type { RegimePolicy } from "../types";
import { formatNumber, text } from "../utils";

type Props = {
  policy?: RegimePolicy;
  compact?: boolean;
};

export function RegimePolicyPanel({ policy, compact = false }: Props) {
  const state = policy?.status || "unavailable";
  const title = policy?.regime_label || "宏观状态";
  const buyMode = buyModeLabel(policy?.buy_mode);
  const applied = applicationLabel(policy?.application_state);
  const multiplier = policy?.satellite_budget_multiplier;
  return (
    <section className={compact ? "regime-panel regime-panel--compact" : "regime-panel"}>
      <div className="regime-panel__head">
        <span className={`regime-icon regime-icon--${state}`}>
          <ShieldAlert size={18} />
        </span>
        <div>
          <h2>市场状态策略</h2>
          <p>组合层风险开关，不预测单只股票，也不替代模型信号。</p>
        </div>
      </div>
      <div className="regime-panel__body">
        <strong>{title}</strong>
        <span>{buyMode}</span>
        <span>{applied}</span>
        {typeof multiplier === "number" ? <span>Satellite预算倍率 {formatNumber(multiplier, 2)}x</span> : null}
      </div>
      <p className="regime-panel__reason">{text(policy?.reason_summary, "宏观状态暂不可用。")}</p>
      {policy?.as_of_date ? <small>数据日期 {policy.as_of_date}</small> : null}
    </section>
  );
}

function buyModeLabel(value?: string): string {
  if (value === "paused") return "暂停新增买入";
  if (value === "reduced") return "缩量/高门槛买入";
  return "正常买入";
}

function applicationLabel(value?: string): string {
  if (value === "applied_to_plan") return "已应用到本轮计划";
  if (value === "advisory_only") return "仅提示，未应用到计划";
  return "未启用";
}
