import { JobRunTimeline } from "../components/JobRunTimeline";
import type { HealthSnapshot } from "../types";
import { formatPercent, text } from "../utils";

type Props = {
  data: HealthSnapshot;
};

export function HealthPage({ data }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>市场与数据健康</h1>
          <p>回答今天的数据能不能用于决策，异常会在全站顶部阻塞调仓。</p>
        </div>
      </div>
      <section className="two-column">
        <div className="panel">
          <h2>字段覆盖率</h2>
          <div className="coverage-list">
            {data.field_coverage.length ? data.field_coverage.map((row) => (
              <div key={String(row.field)}>
                <span>{text(row.field)}</span>
                <div className="bar"><span style={{ width: `${Number(row.coverage ?? 0) * 100}%` }} /></div>
                <strong>{formatPercent(row.coverage)}</strong>
              </div>
            )) : <div className="empty-panel">暂无覆盖率数据</div>}
          </div>
        </div>
        <div className="panel">
          <h2>最近任务</h2>
          <JobRunTimeline job={data.latest_job} failure={data.failure_diagnostic} />
        </div>
      </section>
      <section className="panel">
        <h2>数据源健康</h2>
        <DataTable rows={data.data_sources} columns={["source", "market", "operation", "status", "updated", "failed", "message"]} />
      </section>
    </section>
  );
}

function DataTable({ rows, columns }: { rows: Record<string, unknown>[]; columns: string[] }) {
  if (!rows.length) return <div className="empty-panel">暂无数据源记录</div>;
  return (
    <table className="data-table">
      <thead><tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr></thead>
      <tbody>
        {rows.slice(0, 14).map((row, index) => (
          <tr key={index}>{columns.map((col) => <td key={col}>{text(row[col])}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}
