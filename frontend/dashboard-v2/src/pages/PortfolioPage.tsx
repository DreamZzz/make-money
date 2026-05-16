import { RiskAlertStack } from "../components/RiskAlertStack";
import type { PortfolioSnapshot } from "../types";
import { formatCurrency, formatPercent, text } from "../utils";

type Props = {
  data: PortfolioSnapshot;
};

export function PortfolioPage({ data }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>组合体检</h1>
          <p>把资金池、持仓、暴露和信号收益放在同一个检查面板里。</p>
        </div>
      </div>
      <div className="account-strip">
        <strong>总资产 {formatCurrency(data.account.total_value)}</strong>
        <span>现金 {formatCurrency(data.account.cash)}</span>
        <span>持仓 {formatCurrency(data.account.position_value)}</span>
        <span>回撤 {text(data.account.drawdown)}</span>
      </div>
      <section className="two-column">
        <div className="panel">
          <h2>风险警告</h2>
          <RiskAlertStack alerts={data.risk_alerts} />
        </div>
        <div className="panel">
          <h2>暴露摘要</h2>
          <dl className="account-dl">
            <dt>持仓数</dt>
            <dd>{text(data.exposure.summary.position_count, "0")}</dd>
            <dt>Top5</dt>
            <dd>{formatPercent(data.exposure.summary.top5_weight)}</dd>
            <dt>PE覆盖</dt>
            <dd>{formatPercent(data.exposure.summary.pe_coverage)}</dd>
            <dt>PB覆盖</dt>
            <dd>{formatPercent(data.exposure.summary.pb_coverage)}</dd>
          </dl>
        </div>
      </section>
      <section className="panel">
        <h2>当前持仓</h2>
        <DataTable rows={data.holdings} columns={["symbol", "name", "industry", "market_value", "weight", "pnl_pct"]} />
      </section>
      <section className="panel">
        <h2>信号收益跟踪</h2>
        <DataTable rows={data.signal_outcomes.summary || []} columns={["model_name", "horizon_days", "sample_count", "hit_rate", "avg_return"]} />
      </section>
    </section>
  );
}

function DataTable({ rows, columns }: { rows: Record<string, unknown>[]; columns: string[] }) {
  if (!rows.length) return <div className="empty-panel">暂无数据</div>;
  return (
    <table className="data-table">
      <thead>
        <tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr>
      </thead>
      <tbody>
        {rows.slice(0, 12).map((row, index) => (
          <tr key={index}>{columns.map((col) => <td key={col}>{text(row[col])}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}
