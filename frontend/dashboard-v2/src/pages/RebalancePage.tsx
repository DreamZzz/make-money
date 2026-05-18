import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { OperationSummary } from "../components/OperationSummary";
import { RebalancePlanTable } from "../components/RebalancePlanTable";
import { SatelliteCandidatePanel } from "../components/SatelliteCandidatePanel";
import { SellSignalPanel } from "../components/SellSignalPanel";
import type { RebalanceSnapshot } from "../types";
import { formatCurrency, formatInstrumentLabel, formatPercent, formatValueForField, text, translateSide } from "../utils";

type Props = {
  data: RebalanceSnapshot;
};

export function RebalancePage({ data }: Props) {
  return (
    <div className="page-grid page-grid--with-evidence page-grid--rebalance">
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
      </section>
      <EvidenceDrawer evidence={data.evidence} />
      <section className="rebalance-wide-section">
        <SellSignalPanel rows={data.sell_signals} />
      </section>
      <section className="rebalance-wide-section">
        <SatelliteCandidatePanel context={data.satellite_candidates} />
      </section>
      <section className="panel rebalance-wide-section">
        <h2>冲突信号</h2>
        <CompactRows rows={data.conflicts} empty="暂无冲突信号" />
      </section>
    </div>
  );
}

function CompactRows({ rows, empty }: { rows: Record<string, unknown>[]; empty: string }) {
  if (!rows.length) return <div className="empty-panel">{empty}</div>;
  return (
    <div className="compact-rows">
      {rows.slice(0, 8).map((row, index) => (
        <div key={index}>
          <strong>{formatInstrumentLabel(row)}</strong>
          <span>{compactRowDetail(row)}</span>
        </div>
      ))}
    </div>
  );
}

function compactRowDetail(row: Record<string, unknown>): string {
  if (row.one_lot_cash !== undefined) {
    const confidence = row.confidence === undefined ? "" : ` · 置信度 ${formatPercent(row.confidence)}`;
    const model = row.model_name ? ` · ${text(row.model_name)}` : "";
    return `一手约 ${formatValueForField("one_lot_cash", row.one_lot_cash, row)}${confidence}${model}`;
  }
  if (row.sides !== undefined) {
    return `方向 ${translateSide(row.sides)} · ${formatValueForField("signal_count", row.signal_count, row)} 条信号`;
  }
  return text(row.name || row.model_name || row.reason);
}
