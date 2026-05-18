import type { SatelliteCandidateContext, SatelliteCandidateRow } from "../types";
import { formatCurrency, formatInstrumentLabel, formatNumber, formatPercent, text } from "../utils";

type Props = {
  context?: SatelliteCandidateContext;
};

export function SatelliteCandidatePanel({ context }: Props) {
  const rows = context?.rows || [];

  return (
    <div className="panel satellite-candidates">
      <div className="panel-title-block">
        <h2>Satellite 股票候选</h2>
        <p>这里只展示已通过置信度和排序分门槛的股票 BUY 信号；低于执行门槛的记录会前置过滤。</p>
      </div>
      {context ? (
        <>
          <div className="candidate-summary" aria-label="Satellite 候选预算摘要">
            <SummaryItem label="基础预算" value={formatCurrency(context.base_budget ?? context.budget)} />
            <SummaryItem label="SELL预计释放" value={formatCurrency(context.sell_release_estimate ?? 0)} tone="ok" />
            <SummaryItem label="有效BUY预算" value={formatCurrency(context.budget)} />
            <SummaryItem label="过门槛且预算够" value={`${context.executable_count ?? 0} 只`} tone="ok" />
            <SummaryItem
              label="过门槛但预算不足"
              value={`${context.budget_blocked_count ?? 0} 只`}
              tone={context.budget_blocked_count ? "warn" : "ok"}
            />
            <SummaryItem
              label="门槛过滤"
              value={`${context.threshold_blocked_count ?? 0} 只`}
              tone={context.threshold_blocked_count ? "warn" : "ok"}
            />
          </div>
          <div className="decision-note">{text(context.decision_hint)}</div>
          {rows.length ? (
            <div className="candidate-table-wrap">
              <table className="candidate-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th>一手资金</th>
                    <th>置信度 / 模型</th>
                    <th>执行资格</th>
                    <th>预算状态</th>
                    <th>用户决策</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <CandidateRow row={row} key={row.symbol} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty-panel">暂无股票 BUY 候选</div>
          )}
        </>
      ) : (
        <div className="empty-panel">暂无 Satellite 候选预算检查</div>
      )}
    </div>
  );
}

function SummaryItem({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" }) {
  return (
    <div className={tone ? `candidate-summary__item candidate-summary__item--${tone}` : "candidate-summary__item"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CandidateRow({ row }: { row: SatelliteCandidateRow }) {
  const statusClass = row.budget_status === "covered" ? "status-chip status-chip--ok" : "status-chip status-chip--warn";
  const executionClass = executionStatusClass(row.execution_status);
  const confidence = row.confidence === undefined ? "-" : formatPercent(row.confidence);
  const rankScore = row.rank_score === undefined ? "-" : formatNumber(row.rank_score, 3);
  const model = row.model_name || "-";
  const gap = Number(row.budget_gap || 0) > 0 ? `缺口 ${formatCurrency(row.budget_gap)}` : "";

  return (
    <tr>
      <td>
        <strong>{formatInstrumentLabel(row as unknown as Record<string, unknown>)}</strong>
        <span className="muted-line">{text(row.signal_count)} 条 BUY 信号</span>
      </td>
      <td>{formatCurrency(row.one_lot_cash)}</td>
      <td>
        <strong>{confidence}</strong>
        <span className="muted-line">{model} · 排序分 {rankScore}</span>
      </td>
      <td>
        <span className={executionClass}>{text(row.execution_status_label)}</span>
      </td>
      <td>
        <span className={statusClass}>{text(row.budget_status_label)}</span>
        {gap ? <span className="muted-line">{gap}</span> : null}
      </td>
      <td className="candidate-decision-cell">
        <span>{text(row.decision)}</span>
      </td>
    </tr>
  );
}

function executionStatusClass(status: SatelliteCandidateRow["execution_status"]) {
  if (status === "executable_candidate") return "status-chip status-chip--ok";
  if (status === "budget_blocked") return "status-chip status-chip--warn";
  return "status-chip";
}
