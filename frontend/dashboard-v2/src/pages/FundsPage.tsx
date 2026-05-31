import { useState } from "react";

import type { FundEvaluation, FundHoldingAlert, FundRecommendation, FundsSnapshot } from "../types";
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
      <HoldingAlertsSection alerts={data.holding_alerts} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 14, marginTop: 14 }}>
        {data.funds.map((f) => <FundCard key={f.fund_code} f={f} alerts={data.holding_alerts.filter(a => a.fund_code === f.fund_code)} />)}
      </div>
      <RecommendationsSection rec={data.recommendations} />
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

function HoldingAlertsSection({ alerts }: { alerts: FundHoldingAlert[] }) {
  if (!alerts.length) return null;
  const critical = alerts.filter(a => a.alert_level === "critical");
  const warning = alerts.filter(a => a.alert_level === "warning");
  const info = alerts.filter(a => a.alert_level === "info");
  return (
    <section className="panel" style={{ marginTop: 14 }}>
      <h2>持仓告警 (严格档)</h2>
      <div style={{ display: "flex", gap: 14, fontFamily: "var(--font-mono)", fontSize: 13, marginTop: 4 }}>
        <span><strong style={{ color: "var(--negative, #ff6b6b)" }}>{critical.length}</strong> 严重</span>
        <span><strong style={{ color: "#fbbf24" }}>{warning.length}</strong> 警告</span>
        <span><strong style={{ color: "var(--muted)" }}>{info.length}</strong> 提示</span>
      </div>
      <ul style={{ marginTop: 8, paddingLeft: 18, color: "var(--muted)", lineHeight: 1.7, fontSize: 13 }}>
        {alerts.map((a, i) => (
          <li key={i}>
            <span style={{
              fontSize: 11, fontFamily: "var(--font-mono)",
              padding: "1px 6px", borderRadius: 3, marginRight: 6,
              border: "1px solid var(--line)",
              color: a.alert_level === "critical" ? "var(--negative, #ff6b6b)"
                   : a.alert_level === "warning" ? "#fbbf24" : "var(--muted)",
            }}>{a.alert_type}</span>
            <strong style={{ fontFamily: "var(--font-mono)" }}>{a.fund_code}</strong>
            : {a.headline} → <em>{a.suggested_action}</em>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecommendationsSection({ rec }: { rec: FundsSnapshot["recommendations"] }) {
  const [showWatch, setShowWatch] = useState(false);
  return (
    <>
      <section className="panel" style={{ marginTop: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <h2>今日可加仓窗口 (in_window)</h2>
          <small style={{ color: "var(--muted)" }}>{rec.overall_advice}</small>
        </div>
        {rec.in_window.length === 0 ? (
          <div className="empty-panel" style={{ marginTop: 10 }}>
            {rec.total_candidates === 0
              ? "扫描器无候选数据(等候选池 nav 回灌完成)"
              : "今日无 in_window 候选 — 趋势 + 估值 + 宏观三者未全满足"}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12, marginTop: 10 }}>
            {rec.in_window.map((r) => <RecCard key={r.fund_code} r={r} kind="in_window" />)}
          </div>
        )}
        {(rec.excluded_holdings.length > 0 || rec.overlap_tracking.length > 0) ? (
          <small style={{ display: "block", color: "var(--muted)", marginTop: 10, fontSize: 11 }}>
            已排除持仓: {rec.excluded_holdings.join(",") || "无"}
            {rec.overlap_tracking.length ? ` · 排除同跟踪指数: ${rec.overlap_tracking.join(",")}` : ""}
          </small>
        ) : null}
      </section>
      <section className="panel" style={{ marginTop: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <h2>超跌候选 (oversold · {rec.oversold_candidates.length})</h2>
          <small style={{ color: "var(--muted)" }}>估值 &lt; 30% 分位 + 已深度回撤,等趋势(MA120/250)确立</small>
        </div>
        {rec.oversold_candidates.length === 0 ? (
          <div className="empty-panel" style={{ marginTop: 10 }}>无超跌候选</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12, marginTop: 10 }}>
            {rec.oversold_candidates.map((r) => <RecCard key={r.fund_code} r={r} kind="oversold" />)}
          </div>
        )}
      </section>
      <section className="panel" style={{ marginTop: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <h2>高价值关注名单 (watch · {rec.watch_high_value.length})</h2>
          <button type="button" onClick={() => setShowWatch(v => !v)}
                  style={{
                    fontSize: 12, padding: "2px 10px", borderRadius: 3,
                    border: "1px solid var(--line-strong)",
                    background: "transparent", color: "var(--accent, #60a5fa)",
                    cursor: "pointer", fontFamily: "var(--font-mono)",
                  }}>
            {showWatch ? "收起" : "展开"}
          </button>
        </div>
        {showWatch ? (
          rec.watch_high_value.length === 0 ? (
            <div className="empty-panel" style={{ marginTop: 10 }}>暂无 watch 候选</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12, marginTop: 10 }}>
              {rec.watch_high_value.map((r) => <RecCard key={r.fund_code} r={r} kind="watch" />)}
            </div>
          )
        ) : null}
      </section>
    </>
  );
}

function RecCard({ r, kind }: { r: FundRecommendation; kind: "in_window" | "watch" | "oversold" }) {
  const cls = kind === "in_window" ? "action--add" : kind === "oversold" ? "action--reduce" : "action--hold";
  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 6, padding: 12,
      background: "var(--surface)",
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <strong style={{ fontFamily: "var(--font-display)", fontSize: 16 }}>#{r.rank} {r.fund_code}</strong>
        <span className={`action ${cls}`} style={{ fontSize: 11 }}>{r.signal_tag}</span>
      </div>
      <small style={{ color: "var(--muted)" }}>{r.fund_name || "—"}</small>
      <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.7 }}>
        <KV label="综合分">{r.total_score.toFixed(0)} / 100</KV>
        <KV label="趋势">{r.trend_score !== null ? r.trend_score.toFixed(0) : "—"}</KV>
        <KV label="估值分位">{r.price_pct !== null ? `${(r.price_pct * 100).toFixed(0)}%` : "—"}</KV>
        <KV label="近 6 月">{r.return_6m !== null ? formatPercent(r.return_6m) : "—"}</KV>
        <KV label="规模">{r.scale_yi ? `${r.scale_yi.toFixed(0)}亿` : "—"}</KV>
        <KV label="分类">{r.etf_subcategory || "—"}</KV>
      </div>
      <p style={{ marginTop: 8, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>{r.thesis}</p>
    </div>
  );
}

function FundCard({ f, alerts = [] }: { f: FundEvaluation; alerts?: FundHoldingAlert[] }) {
  const stale = f.snapshot_stale_days !== null && f.snapshot_stale_days > 3;
  const isExited = f.intent === "exited";
  const isBalanced = f.category === "balanced";
  const isEquityLike = (f.category === "equity_index" || f.category === "qdii") && !isExited;
  const dim = isExited ? { opacity: 0.6 } : {};
  return (
    <section className="panel" style={dim}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <h3 style={{ margin: 0, fontFamily: "var(--font-display)" }}>{f.fund_code}</h3>
        <ActionPill action={f.action} />
      </div>
      <small style={{ color: "var(--muted)" }}>{f.fund_name}</small>
      <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
        <CategoryBadge category={f.category} />
        <IntentBadge intent={f.intent} />
      </div>
      <div style={{ marginTop: 10, fontSize: 13, fontFamily: "var(--font-mono)", lineHeight: 1.7 }}>
        <KV label="跟踪">{f.tracking_index_name || f.tracking_index}</KV>
        <KV label="持仓">
          {f.shares !== null
            ? `${formatShares(f.shares)} × ${(f.broker_latest_nav || f.nav || 0).toFixed(4)} = ${formatCurrency(f.current_value || 0)}`
            : "—"}
          {f.broker_market_value !== null ? <span style={{ marginLeft: 6, color: "var(--muted)", fontSize: 11 }}>
            (broker)
          </span> : null}
          {stale ? <span style={{ color: "var(--negative, #ff6b6b)", marginLeft: 6 }}>
            ⚠ 快照 {f.snapshot_stale_days}d 未更新
          </span> : null}
        </KV>
        {f.broker_cost_price !== null ? (
          <KV label="成本价">{f.broker_cost_price.toFixed(4)}</KV>
        ) : null}
        <KV label="累计收益">
          {f.return_pct !== null
            ? <span style={{ color: f.return_pct >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
                {formatPercent(f.return_pct)} ({formatCurrency(f.return_amount || 0)})
              </span>
            : "—"}
        </KV>
        {f.broker_day_return_pct !== null ? (
          <KV label="今日">
            <span style={{ color: f.broker_day_return_pct >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
              {formatPercent(f.broker_day_return_pct)}
              {f.broker_yesterday_pnl !== null ? ` (${formatCurrency(f.broker_yesterday_pnl)})` : ""}
            </span>
          </KV>
        ) : null}
        {f.holding_days !== null ? (
          <KV label="持有">{f.holding_days} 天</KV>
        ) : null}
        {isEquityLike ? (
          <>
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
                : "—(不进 RS 池)"}
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
          </>
        ) : isBalanced ? (
          <KV label="评估">
            <span style={{ color: "var(--muted)" }}>股债混合,不适用纯权益指数口径</span>
          </KV>
        ) : isExited ? (
          <KV label="状态">
            <span style={{ color: "var(--muted)" }}>已退出,系统不再驱动</span>
          </KV>
        ) : null}
      </div>
      <p style={{ marginTop: 10, color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>{f.thesis}</p>
      <RiskTags tags={f.risk_tags} />
      {alerts.length > 0 ? (
        <div style={{ marginTop: 8, padding: 8, borderRadius: 4, background: "var(--surface-elevated, rgba(255,255,255,0.04))",
                      borderLeft: `3px solid ${alerts.some(a => a.alert_level === "critical") ? "var(--negative, #ff6b6b)" : "#fbbf24"}` }}>
          {alerts.map((a, i) => (
            <div key={i} style={{ fontSize: 11, fontFamily: "var(--font-mono)", lineHeight: 1.6 }}>
              <span style={{ color: a.alert_level === "critical" ? "var(--negative, #ff6b6b)"
                                 : a.alert_level === "warning" ? "#fbbf24" : "var(--muted)" }}>
                [{a.alert_type}]
              </span> {a.headline} → <em>{a.suggested_action}</em>
            </div>
          ))}
        </div>
      ) : null}
      {f.snapshot_source ? (
        <small style={{ display: "block", marginTop: 8, color: "var(--muted)", fontSize: 11 }}>
          源: {f.snapshot_source}{f.snapshot_captured_at ? ` · ${f.snapshot_captured_at}` : ""}
        </small>
      ) : null}
    </section>
  );
}

function CategoryBadge({ category }: { category: string }) {
  const map: Record<string, { label: string; color: string }> = {
    equity_index: { label: "权益指数", color: "var(--accent, #4ade80)" },
    qdii: { label: "QDII", color: "var(--accent, #60a5fa)" },
    balanced: { label: "股债混合", color: "#fbbf24" },
    bond: { label: "债券", color: "#94a3b8" },
    other: { label: "其它", color: "var(--muted)" },
  };
  const m = map[category] || map.other;
  return (
    <span style={{
      fontSize: 11, padding: "1px 6px", borderRadius: 3,
      border: `1px solid ${m.color}`, color: m.color, fontFamily: "var(--font-mono)",
    }}>{m.label}</span>
  );
}

function IntentBadge({ intent }: { intent: string }) {
  if (intent === "active") return null;  // 默认不显示
  const map: Record<string, { label: string; color: string }> = {
    exited: { label: "已退出", color: "var(--muted)" },
    watching: { label: "观察", color: "#94a3b8" },
  };
  const m = map[intent] || { label: intent, color: "var(--muted)" };
  return (
    <span style={{
      fontSize: 11, padding: "1px 6px", borderRadius: 3,
      border: `1px dashed ${m.color}`, color: m.color, fontFamily: "var(--font-mono)",
    }}>{m.label}</span>
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
    exited: "已退出",
    balanced_no_equity_rules: "非权益规则",
    broker_mismatch: "broker 偏差",
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
