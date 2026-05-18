import type { SellSignalRow } from "../types";
import { formatCurrency, formatInstrumentLabel, formatNumber, formatPercent, text } from "../utils";

type Props = {
  rows?: SellSignalRow[];
};

export function SellSignalPanel({ rows = [] }: Props) {
  return (
    <div className="panel sell-signals">
      <div className="panel-title-row">
        <div className="panel-title-block">
          <h2>持仓卖出信号</h2>
          <p>这里只展示当前持仓中已触发 active SELL 的标的；纸交易执行前会优先处理卖出，再处理买入。</p>
        </div>
        <strong>{rows.length} 只</strong>
      </div>
      {rows.length ? (
        <div className="candidate-table-wrap">
          <table className="candidate-table sell-signal-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>持仓</th>
                <th>浮盈亏</th>
                <th>SELL 来源</th>
                <th>用户决策</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <SellSignalTableRow row={row} key={`${row.strategy_name || "-"}-${row.symbol}`} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-panel">暂无当前持仓的 active SELL 信号</div>
      )}
    </div>
  );
}

function SellSignalTableRow({ row }: { row: SellSignalRow }) {
  return (
    <tr>
      <td>
        <strong>{formatInstrumentLabel(row as unknown as Record<string, unknown>)}</strong>
        <span className="muted-line">{text(row.strategy_name || "paper")}</span>
      </td>
      <td>
        <strong>{formatCurrency(row.market_value)}</strong>
        <span className="muted-line">{formatNumber(row.quantity, 0)} 股</span>
      </td>
      <td>
        <strong className={Number(row.pnl_pct || 0) < 0 ? "negative-text" : "positive-text"}>
          {formatPercent(row.pnl_pct)}
        </strong>
        <span className="muted-line">{formatCurrency(row.pnl)}</span>
      </td>
      <td>
        <span className="status-chip status-chip--failed">卖出</span>
        <span className="muted-line">
          {text(row.model_name)} · 置信度 {formatPercent(row.confidence)} · {text(row.signal_count)} 条
        </span>
      </td>
      <td className="candidate-decision-cell">{text(row.decision)}</td>
    </tr>
  );
}
