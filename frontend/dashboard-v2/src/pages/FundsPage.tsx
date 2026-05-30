import type { FundEvaluation, FundsSnapshot } from "../types";
import { formatCurrency, formatPercent } from "../utils";

type Props = { data: FundsSnapshot };

export function FundsPage({ data }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>Core 基金每日评估</h1>
          <p>
            按 M4 动态权重 × 宏观目标仓位算出每支基金应有市值,与你录入的实盘镜像对比,
            给出加/减/暂停建议。决策辅助,不替代你的判断。
          </p>
        </div>
        {data.eval_date ? <span className="funding-gap"><small>as of</small><strong>{data.eval_date}</strong></span> : null}
      </div>
      <OverallCard data={data} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 14, marginTop: 14 }}>
        {data.funds.map((f) => <FundCard key={f.fund_code} f={f} />)}
      </div>
      <section className="panel" style={{ marginTop: 14, overflowX: "auto" }}>
        <h2>决策矩阵</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>基金</th><th>跟踪</th><th>持仓市值</th><th>账户权重</th>
              <th>M4 目标权重</th><th>目标市值</th><th>偏离</th>
              <th>应执行</th><th>份额数</th><th>Action</th><th>价格分位</th><th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {data.funds.map((f) => (
              <tr key={f.fund_code}>
                <td><strong>{f.fund_code}</strong><br /><small style={{ color: "var(--muted)" }}>{f.fund_name}</small></td>
                <td>{f.tracking_index_name || f.tracking_index}</td>
                <td>{f.current_value !== null ? formatCurrency(f.current_value) : "—"}</td>
                <td>{f.current_account_weight !== null ? formatPercent(f.current_account_weight) : "—"}</td>
                <td><strong>{f.target_weight_m4 !== null ? formatPercent(f.target_weight_m4) : "—"}</strong></td>
                <td>{f.target_value !== null ? formatCurrency(f.target_value) : "—"}</td>
                <td><DriftBadge pct={f.drift_pct} /></td>
                <td><DeltaCell amount={f.delta_amount} /></td>
                <td>{f.delta_shares !== null ? formatShares(f.delta_shares) : "—"}</td>
                <td><ActionPill action={f.action} /></td>
                <td>{f.price_pct !== null ? formatPercent(f.price_pct) : "—"}</td>
                <td><RiskTags tags={f.risk_tags} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}

function OverallCard({ data }: { data: FundsSnapshot }) {
  const total = data.core_total_delta_amount;
  const cls = Math.abs(total) < 1000 ? "action--hold"
    : total > 0 ? "action--add" : "action--reduce";
  return (
    <section className="panel" style={{ marginTop: 0 }}>
      <h2>今日 Core 综述</h2>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginTop: 6 }}>
        <span className={`action ${cls}`}
              style={{ fontFamily: "var(--font-display)", padding: "4px 10px", borderRadius: 4,
                       border: "1px solid var(--line-strong)" }}>
          {Math.abs(total) < 1000 ? "在容忍区间"
            : total > 0 ? `净增 ${formatCurrency(total)}` : `净减 ${formatCurrency(-total)}`}
        </span>
        <strong style={{ fontFamily: "var(--font-mono)", fontSize: 14 }}>{data.overall_advice.headline}</strong>
        {data.equity_exposure !== null ? (
          <small style={{ color: "var(--muted)" }}>
            宏观目标权益 {formatPercent(data.equity_exposure)} · 账户 {formatCurrency(data.account_total_value || 0)}
          </small>
        ) : null}
      </div>
      {data.overall_advice.actions.length > 0 ? (
        <ul style={{ marginTop: 8, paddingLeft: 20, color: "var(--muted)", lineHeight: 1.6 }}>
          {data.overall_advice.actions.map((a, i) => <li key={i}>{a}</li>)}
        </ul>
      ) : null}
    </section>
  );
}

function FundCard({ f }: { f: FundEvaluation }) {
  const stale = f.snapshot_stale_days !== null && f.snapshot_stale_days > 3;
  return (
    <section className="panel">
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <h3 style={{ margin: 0, fontFamily: "var(--font-display)" }}>{f.fund_code}</h3>
        <ActionPill action={f.action} />
      </div>
      <small style={{ color: "var(--muted)" }}>{f.fund_name}</small>
      <div style={{ marginTop: 10, fontSize: 13, fontFamily: "var(--font-mono)", lineHeight: 1.7 }}>
        <KV label="跟踪">{f.tracking_index_name || f.tracking_index}</KV>
        <KV label="持仓">
          {f.shares !== null && f.nav !== null
            ? `${formatShares(f.shares)} × ${f.nav.toFixed(4)} = ${formatCurrency(f.current_value || 0)}`
            : "—"}
          {stale ? <span style={{ color: "var(--negative, #ff6b6b)", marginLeft: 6 }}>
            ⚠ 快照 {f.snapshot_stale_days}d 未更新
          </span> : null}
        </KV>
        <KV label="收益">
          {f.return_pct !== null
            ? <span style={{ color: f.return_pct >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
                {formatPercent(f.return_pct)} ({formatCurrency(f.return_amount || 0)})
              </span>
            : "—"}
        </KV>
        <KV label="价格分位">
          {f.price_pct !== null ? `${(f.price_pct * 100).toFixed(0)}%` : "—"}
          {f.price_pct !== null && f.price_pct >= 0.9 ? <span style={{ marginLeft: 6, color: "var(--negative, #ff6b6b)" }}>偏贵</span> : null}
        </KV>
        <KV label="趋势">
          MA120 {f.trend_healthy ? "✓" : "✗"} · MA250 {f.trend_weak ? "✗" : "✓"}
        </KV>
        <KV label="M4 目标">
          {f.target_weight_m4 !== null
            ? `${formatPercent(f.target_weight_m4)} (账户级 ${formatPercent(f.target_account_weight || 0)})`
            : "—"}
        </KV>
        <KV label="目标市值">
          {f.target_value !== null ? formatCurrency(f.target_value) : "—"}
        </KV>
        <KV label="应执行">
          <DeltaCell amount={f.delta_amount} />
          {f.delta_shares !== null ? <small style={{ color: "var(--muted)", marginLeft: 4 }}>
            ≈ {formatShares(f.delta_shares)} 份
          </small> : null}
        </KV>
      </div>
      <p style={{ marginTop: 10, color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>{f.thesis}</p>
      <RiskTags tags={f.risk_tags} />
    </section>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <span style={{ color: "var(--muted)", minWidth: 70 }}>{label}</span>
      <span style={{ flex: 1 }}>{children}</span>
    </div>
  );
}

function ActionPill({ action }: { action: string }) {
  const cls = action === "BUY" || action === "ADD" ? "action--add"
    : action === "REDUCE" ? "action--reduce"
    : action === "PAUSE" ? "action--hold"
    : "action--hold";
  return <span className={`action ${cls}`}>{action}</span>;
}

function DeltaCell({ amount }: { amount: number | null }) {
  if (amount === null) return <>—</>;
  if (Math.abs(amount) < 1) return <span style={{ color: "var(--muted)" }}>—</span>;
  const cls = amount > 0 ? "action--add" : "action--reduce";
  const sign = amount > 0 ? "+" : "−";
  return <span className={`action ${cls}`}>{sign}{formatCurrency(Math.abs(amount))}</span>;
}

function DriftBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <>—</>;
  const cls = Math.abs(pct) < 0.05 ? "action--hold"
    : pct > 0 ? "action--reduce" : "action--add";
  return <span className={`action ${cls}`}>{formatPercent(pct)}</span>;
}

function RiskTags({ tags }: { tags: string[] }) {
  if (!tags.length || (tags.length === 1 && tags[0] === "normal")) {
    return <span style={{ color: "var(--muted)", fontSize: 12 }}>—</span>;
  }
  const labelMap: Record<string, string> = {
    high_percentile: "高分位",
    low_percentile: "低分位",
    overweight: "超配",
    underweight: "欠配",
    trend_weak: "趋势弱",
    snapshot_stale: "快照过期",
    no_snapshot: "无快照",
    nav_stale: "净值滞后",
    m4_missing: "M4 缺失",
  };
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {tags.filter(t => t !== "normal").map((t) => (
        <span key={t} style={{
          fontSize: 11, padding: "2px 6px", borderRadius: 3,
          border: "1px solid var(--line)", color: "var(--muted)",
          fontFamily: "var(--font-mono)",
        }}>{labelMap[t] || t}</span>
      ))}
    </span>
  );
}

function formatShares(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 10000) return `${(n / 10000).toFixed(2)}万`;
  return n.toFixed(0);
}
