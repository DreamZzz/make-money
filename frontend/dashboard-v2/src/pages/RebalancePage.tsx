import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { OperationSummary } from "../components/OperationSummary";
import { RebalancePlanTable } from "../components/RebalancePlanTable";
import type { RebalanceSnapshot } from "../types";
import { formatCurrency, text } from "../utils";

type Props = {
  data: RebalanceSnapshot;
};

export function RebalancePage({ data }: Props) {
  return (
    <div className="page-grid page-grid--with-evidence">
      <section className="page-main">
        <div className="page-title-row">
          <div>
            <h1>调仓执行</h1>
            <p>计划 {data.plan_id || "暂无"}，Core 基金与 Satellite 个股同屏确认。</p>
          </div>
          <div className="funding-gap">
            <span>资金缺口</span>
            <strong>{formatCurrency(data.summary.funding_gap)}</strong>
          </div>
        </div>
        <OperationSummary summary={data.summary} />
        <RebalancePlanTable groups={data.groups} />
        <section className="two-column">
          <div className="panel">
            <h2>冲突信号</h2>
            <CompactRows rows={data.conflicts} empty="暂无冲突信号" />
          </div>
          <div className="panel">
            <h2>一手门槛</h2>
            <CompactRows rows={data.one_lot_gaps || []} empty="暂无一手门槛提示" />
          </div>
        </section>
      </section>
      <EvidenceDrawer evidence={data.evidence} />
    </div>
  );
}

function CompactRows({ rows, empty }: { rows: Record<string, unknown>[]; empty: string }) {
  if (!rows.length) return <div className="empty-panel">{empty}</div>;
  return (
    <div className="compact-rows">
      {rows.slice(0, 8).map((row, index) => (
        <div key={index}>
          <strong>{text(row.symbol || row.fund_code || row.instrument_id)}</strong>
          <span>{text(row.name || row.sides || row.model_name || row.one_lot_cash)}</span>
        </div>
      ))}
    </div>
  );
}
