type Props = {
  values: Array<number | null>;
  min: number;
  max: number;
  refLine?: number;
  color?: string;
  width?: number;
  height?: number;
  label?: string;
};

export function Sparkline({ values, min, max, refLine, color = "#1d6f5b", width = 520, height = 64, label }: Props) {
  const clean = values.filter((v): v is number => v != null);
  if (clean.length < 2) return <div className="empty-panel">趋势数据不足（需先回灌历史市场状态）</div>;
  const range = max - min || 1;
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => height - ((v - min) / range) * height;
  const path = values
    .map((v, i) => (v == null ? null : `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");
  const last = clean[clean.length - 1];

  return (
    <div className="sparkline">
      {label ? <div className="sparkline__label">{label}（最新 {last.toFixed(0)}）</div> : null}
      <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {refLine != null ? (
          <line x1={0} x2={width} y1={y(refLine)} y2={y(refLine)} stroke="var(--line-strong)" strokeDasharray="4 4" strokeWidth={1} />
        ) : null}
        <path d={path} fill="none" stroke={color} strokeWidth={2} />
      </svg>
    </div>
  );
}
