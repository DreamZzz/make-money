import type { CapitalBreakdown as CapitalBreakdownData } from "../types";
import { formatCurrency, formatPercent, text } from "../utils";

type Props = {
  capital?: CapitalBreakdownData;
  compact?: boolean;
};

export function CapitalBreakdown({ capital, compact = false }: Props) {
  if (!capital) return null;
  const rows = [
    ["现金", formatCurrency(capital.cash)],
    ["Core基金市值", formatCurrency(capital.core_value)],
    ["Satellite股票市值", formatCurrency(capital.satellite_value)],
    ["已预留现金", formatCurrency(capital.reserved_cash)],
    ["未预留现金", formatCurrency(capital.unreserved_cash)],
  ];
  if (!compact) {
    rows.push(
      ["Core目标", targetText(capital.core_target_pct, capital.core_target_value)],
      ["Satellite目标", targetText(capital.satellite_target_pct, capital.satellite_target_value)],
      ["股票纸盘资产", formatCurrency(capital.trading_account_total_value)],
    );
  }
  return (
    <section className="capital-panel" aria-label="资金口径">
      <div className="capital-panel__head">
        <div>
          <span>{text(capital.scope_label, "统一资金池")}</span>
          <strong>统一总资产 {formatCurrency(capital.unified_total_value)}</strong>
        </div>
        <small>{text(capital.reconciliation?.formula || capital.formula)}</small>
      </div>
      <dl className={compact ? "capital-grid capital-grid--compact" : "capital-grid"}>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <p className="capital-panel__note">{text(capital.scope_note)}</p>
    </section>
  );
}

function targetText(pct: unknown, value: unknown): string {
  return `${formatPercent(pct)} / ${formatCurrency(value)}`;
}
