import { Banknote, Clock, ListChecks, TrendingUp } from "lucide-react";

import type { OperationSummaryData } from "../types";
import { formatCurrency } from "../utils";

type Props = {
  summary: OperationSummaryData;
};

export function OperationSummary({ summary }: Props) {
  return (
    <section className="operation-summary" aria-label="操作量汇总">
      <Metric icon={<ListChecks size={18} />} label="本轮操作" value={`${summary.operation_count ?? 0} 次`} />
      <Metric icon={<Banknote size={18} />} label="订单需现金" value={formatCurrency(summary.cash_required)} />
      <Metric icon={<Banknote size={18} />} label="预算预留" value={formatCurrency(summary.reserved_cash)} />
      <Metric icon={<Banknote size={18} />} label="现金占用上限" value={formatCurrency(summary.cash_commitment)} />
      <Metric icon={<Clock size={18} />} label="预计耗时" value={`${summary.estimated_minutes ?? 0} 分钟`} />
      <Metric icon={<TrendingUp size={18} />} label="买入 / 减仓" value={`${summary.buy_count ?? 0} / ${summary.reduce_count ?? 0}`} />
    </section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      <span className="metric__icon">{icon}</span>
      <span className="metric__label">{label}</span>
      <strong className="metric__value">{value}</strong>
    </div>
  );
}
