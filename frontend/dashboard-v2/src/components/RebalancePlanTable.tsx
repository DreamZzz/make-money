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
                <th>金额</th>
                <th>预算占用</th>
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
      <td>{formatCurrency(item.expected_cash ?? item.budget_delta)}</td>
      <td>{formatCurrency(item.budget_consumption)}</td>
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
