import { fieldLabel, formatValueForField } from "../utils";

type Column = string | {
  key: string;
  label?: string;
};

type Props = {
  rows: Record<string, unknown>[];
  columns: Column[];
  empty?: string;
  limit?: number;
};

export function DataTable({ rows, columns, empty = "暂无数据", limit = 12 }: Props) {
  if (!rows.length) return <div className="empty-panel">{empty}</div>;
  const normalizedColumns = columns.map((column) => (typeof column === "string" ? { key: column } : column));

  return (
    <table className="data-table">
      <thead>
        <tr>
          {normalizedColumns.map((column) => (
            <th key={column.key}>{column.label || fieldLabel(column.key)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, limit).map((row, rowIndex) => (
          <tr key={stableRowKey(row, rowIndex)}>
            {normalizedColumns.map((column) => (
              <td key={column.key}>{formatValueForField(column.key, row[column.key], row)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function stableRowKey(row: Record<string, unknown>, index: number): string {
  const parts = [
    row.signal_id,
    row.experiment_id,
    row.symbol,
    row.fund_code,
    row.instrument_id,
    row.trade_date,
    row.started_at,
    row.model_name,
  ].filter(Boolean);
  return parts.length ? parts.join("-") : String(index);
}
