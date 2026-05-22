import type { RebalanceGroups, RebalanceItem } from "../types";
import {
  formatCurrency,
  formatInstrumentLabel,
  text,
  translateAction,
  translateBudgetAction,
  translateInstrumentType,
  translateSleeve,
} from "../utils";

type Props = {
  groups: RebalanceGroups;
};

const GROUP_LABELS: Array<[keyof RebalanceGroups, string]> = [
  ["budget", "资金分配"],
  ["executable", "可执行"],
  ["confirm", "需人工确认"],
  ["deferred", "暂缓"],
];

export function RebalancePlanTable({ groups }: Props) {
  return (
    <div className="rebalance-table">
      {GROUP_LABELS.map(([key, label]) => (
        <section className="table-section" key={key} aria-label={label}>
          <div className="table-section__head">
            <h3>{label}</h3>
            <span>{groups[key]?.length ?? 0} 条</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>标的</th>
                <th>层</th>
                <th>动作</th>
                <th>当前 / 目标</th>
                <th>预留 / 现金影响</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {(groups[key] || []).length ? (
                (groups[key] || []).map((item, index) => <RebalanceRow item={item} key={`${key}-${item.instrument_id}-${index}`} />)
              ) : (
                <tr className="empty-row">
                  <td colSpan={6}>暂无{label}项目</td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function RebalanceRow({ item }: { item: RebalanceItem }) {
  return (
    <tr>
      <td>
        <strong>{formatInstrumentLabel(item as unknown as Record<string, unknown>, "instrument_id")}</strong>
        <span className="muted-line">{translateInstrumentType(item.instrument_type)}</span>
      </td>
      <td>{translateSleeve(item.sleeve)}</td>
      <td>
        <span className={`action-pill action-pill--${String(item.action).toLowerCase()}`}>{displayAction(item)}</span>
      </td>
      <td>
        <strong>{formatCurrency(item.current_value)}</strong>
        <span className="muted-line">目标 {formatCurrency(item.target_value)}</span>
      </td>
      <td>
        <strong>{cashImpactLabel(item)}</strong>
        <span className="muted-line">{cashImpactHelp(item)}</span>
      </td>
      <td>{item.bucket_reason || item.reason || "-"}</td>
    </tr>
  );
}

function displayAction(item: RebalanceItem): string {
  if (item.instrument_type === "sleeve" || String(item.execution_mode || "").toUpperCase() === "BUDGET") {
    return translateBudgetAction(item.action);
  }
  return translateAction(item.action);
}

function cashImpactLabel(item: RebalanceItem): string {
  const action = String(item.action || "").toUpperCase();
  if (item.instrument_type === "sleeve" || String(item.execution_mode || "").toUpperCase() === "BUDGET") {
    return formatCurrency(item.budget_consumption ?? item.expected_cash ?? item.budget_delta);
  }
  if (action === "REDUCE" || action === "SELL") {
    return formatCurrency(item.expected_cash ?? item.cash_effect);
  }
  return formatCurrency(item.expected_cash ?? item.budget_delta);
}

function cashImpactHelp(item: RebalanceItem): string {
  const action = String(item.action || "").toUpperCase();
  if (item.instrument_type === "sleeve" || String(item.execution_mode || "").toUpperCase() === "BUDGET") {
    return "资金池预留，不是订单";
  }
  if (action === "REDUCE" || action === "SELL") return "预计释放现金";
  if (action === "BUY" || action === "ADD") return "预计占用现金";
  return "无现金动作";
}
