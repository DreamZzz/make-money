import type { RouteKey } from "../components/AppShell";
import { DataTable } from "../components/DataTable";
import { CapitalBreakdown } from "../components/CapitalBreakdown";
import { RegimePolicyPanel } from "../components/RegimePolicyPanel";
import type { PortfolioFundRow, PortfolioFundsPanel, PortfolioSnapshot, RiskAlert } from "../types";
import { fieldLabel, formatCurrency, formatPercent, formatValueForField, text } from "../utils";

type Props = {
  data: PortfolioSnapshot;
  onNavigate?: (route: RouteKey) => void;
};

export function PortfolioPage({ data, onNavigate }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>组合体检</h1>
          <p>把资金池、持仓、暴露和信号收益放在同一个检查面板里。</p>
        </div>
      </div>
      <div className="account-strip">
        <strong>统一总资产 {formatCurrency(data.capital?.unified_total_value ?? data.account.total_value)}</strong>
        <span>现金 {formatCurrency(data.capital?.cash ?? data.account.cash)}</span>
        <span>Core基金市值 {formatCurrency(data.capital?.core_value)}</span>
        <span>Satellite股票市值 {formatCurrency(data.capital?.satellite_value ?? data.account.position_value)}</span>
        <span>股票纸盘资产 {formatCurrency(data.capital?.trading_account_total_value ?? data.account.total_value)}</span>
        <span>回撤 {formatPercent(data.account.drawdown)}</span>
      </div>
      <CapitalBreakdown capital={data.capital} />
      <RegimePolicyPanel policy={data.regime_policy} compact />
      <section className="two-column">
        <div className="panel">
          <h2>风险处置清单</h2>
          <RiskActionList alerts={data.risk_alerts} />
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
        <h2>暴露解释</h2>
        <ExposureInsightCards insights={data.exposure.insights || []} />
      </section>
      {data.funds_panel?.available ? <FundsPanel fp={data.funds_panel} onNavigate={onNavigate} /> : null}
      <section className="panel">
        <h2>当前持仓</h2>
        <DataTable
          rows={data.holdings}
          columns={[
            "symbol",
            "entry_strategy_label",
            "industry",
            "market_value",
            "weight",
            "pnl_pct",
            "holding_days",
            "weight_change_7d",
            "weight_change_20d",
            "qlib_alignment",
            "qlib_alignment_reason",
            "qlib_prediction_date",
            "qlib_rank",
            "qlib_confidence",
            "latest_signal_side",
          ]}
        />
      </section>
      <section className="panel">
        <h2>信号收益跟踪</h2>
        <SignalOutcomePanel outcomes={data.signal_outcomes} />
      </section>
    </section>
  );
}

function FundsPanel({ fp, onNavigate }: { fp: PortfolioFundsPanel; onNavigate?: (r: RouteKey) => void }) {
  const sevByLevel = (lv: string) => fp.alerts.filter((a: any) => a.alert_level === lv).length;
  return (
    <section className="panel" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>基金持仓 (Core)</h2>
        {onNavigate ? (
          <button className="secondary-link" onClick={() => onNavigate("/funds")} type="button">
            打开 Core 基金 →
          </button>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 18, marginTop: 6, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
        <span>持仓 <strong style={{ color: "var(--ink)" }}>{fp.funds.length}</strong> 支</span>
        <span>严重 <strong style={{ color: "var(--negative, #ff6b6b)" }}>{sevByLevel("critical")}</strong></span>
        <span>警告 <strong style={{ color: "#fbbf24" }}>{sevByLevel("warning")}</strong></span>
        <span>提示 <strong>{sevByLevel("info")}</strong></span>
        <span>更优替代 <strong style={{ color: "var(--accent, #60a5fa)" }}>{fp.alternatives.length}</strong></span>
      </div>
      {fp.funds.length > 0 ? (
        <table className="data-table" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>代码</th><th>名称</th><th>类别</th>
              <th>持仓市值</th><th>累计收益</th>
              <th>今日合成</th><th>应执行</th><th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {fp.funds.map((f) => <FundRow key={f.fund_code} f={f} />)}
          </tbody>
        </table>
      ) : null}
      {fp.alternatives.length > 0 ? (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 6, background: "var(--surface-elevated, rgba(96,165,250,0.06))", borderLeft: "3px solid var(--accent, #60a5fa)" }}>
          <strong style={{ fontSize: 13 }}>同跟踪指数有更强 ETF (Top 替代):</strong>
          <ul style={{ margin: "6px 0 0 18px", padding: 0, color: "var(--muted)", lineHeight: 1.7, fontSize: 12 }}>
            {fp.alternatives.map((a: any, i) => (
              <li key={i}>
                <strong style={{ fontFamily: "var(--font-mono)" }}>{a.fund_code}</strong>: {a.headline}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

const PORTFOLIO_NET_COLOR: Record<string, string> = {
  EXIT_NOW: "#ff6b6b",
  HOLD_WAIT_TREND: "#fbbf24",
  ADD_TO_TARGET: "#4ade80",
  REDUCE_TO_TARGET: "#fb7185",
  CONSIDER_SWITCH: "#60a5fa",
  ADD_WINDOW_OPEN: "#4ade80",
  HOLD_AS_PLANNED: "#94a3b8",
};

function FundRow({ f }: { f: PortfolioFundRow }) {
  const exited = f.intent === "exited";
  const na = f.net_action;
  const naColor = na ? (PORTFOLIO_NET_COLOR[na.net_action] || "#94a3b8") : "#94a3b8";
  return (
    <tr style={exited ? { opacity: 0.55 } : {}}>
      <td><strong>{f.fund_code}</strong></td>
      <td>
        <small>{f.fund_name || "—"}</small>
        <br />
        <small style={{ color: "var(--muted)", fontSize: 10 }}>{f.intent}</small>
      </td>
      <td><small style={{ color: "var(--muted)" }}>{f.category}</small></td>
      <td>{f.current_value !== null ? formatCurrency(f.current_value) : "—"}</td>
      <td>{f.return_pct !== null
        ? <span style={{ color: f.return_pct >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
            {formatPercent(f.return_pct)}
          </span>
        : "—"}</td>
      <td style={{ maxWidth: 280 }}>
        {na ? (
          <>
            <strong style={{ color: naColor, fontSize: 11, fontFamily: "var(--font-mono)" }}>
              {na.net_action}
            </strong>
            <br />
            <small style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>{na.headline}</small>
          </>
        ) : "—"}
      </td>
      <td>{f.delta_amount !== null && Math.abs(f.delta_amount) > 1
        ? <span style={{ color: f.delta_amount > 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
            {f.delta_amount > 0 ? "+" : ""}{formatCurrency(f.delta_amount)}
          </span>
        : "—"}</td>
      <td><small style={{ color: "var(--muted)" }}>{f.risk_tags.filter(t => t !== "normal").join(",") || "—"}</small></td>
    </tr>
  );
}

function RiskActionList({ alerts }: { alerts: RiskAlert[] }) {
  if (!alerts.length) {
    return <div className="empty-panel">暂无风险警告</div>;
  }
  return (
    <div className="risk-action-list">
      {alerts.map((alert, index) => (
        <article className={`risk-action-card risk-action-card--${alert.level || "info"}`} key={`${alert.metric || alert.label}-${index}`}>
          <div className="risk-action-card__head">
            <div>
              <strong>{alert.label || alert.title || alert.metric || "风险提示"}</strong>
              <span>{alert.detail || alert.message || text(alert.value)}</span>
            </div>
            <span className="status-chip">{alert.severity || alert.level || "INFO"}</span>
          </div>
          <p>{alert.severity_reason || "请结合持仓和调仓计划确认处置动作。"}</p>
          <HoldingMiniList title="影响标的" rows={alert.affected_holdings || []} />
          <ul className="action-list">
            {(alert.suggested_actions || ["先确认数据口径，再在下一次调仓计划中处理。"]).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

function ExposureInsightCards({ insights }: { insights: Record<string, unknown>[] }) {
  if (!insights.length) {
    return <div className="empty-panel">暂无暴露解释；请先确认持仓和 stock_info 覆盖。</div>;
  }
  return (
    <div className="exposure-insights">
      {insights.map((insight, index) => (
        <article className="exposure-insight-card" key={`${text(insight.key, "insight")}-${index}`}>
          <div>
            <strong>{text(insight.title, "暴露提示")}</strong>
            <p>{text(insight.message)}</p>
          </div>
          <dl className="mini-metrics">
            {["value", "benchmark_value", "pe_coverage", "pb_coverage"].map((key) => (
              insight[key] === undefined || insight[key] === null ? null : (
                <div key={key}>
                  <dt>{fieldLabel(key)}</dt>
                  <dd>{formatPercent(insight[key])}</dd>
                </div>
              )
            ))}
          </dl>
          <p className="muted-line">{text(insight.suggested_action)}</p>
          <HoldingMiniList title="相关标的" rows={Array.isArray(insight.affected_holdings) ? insight.affected_holdings as Record<string, unknown>[] : []} />
        </article>
      ))}
    </div>
  );
}

function HoldingMiniList({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  return (
    <div className="holding-mini-list">
      <span>{title}</span>
      <ul>
        {rows.slice(0, 5).map((row, index) => (
          <li key={`${text(row.symbol, "holding")}-${index}`}>
            <strong>{text(row.display_name || row.symbol)}</strong>
            <small>
              {formatValueForField("weight", row.weight, row)}
              {row.pnl_pct !== undefined && row.pnl_pct !== null ? ` / ${formatValueForField("pnl_pct", row.pnl_pct, row)}` : ""}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SignalOutcomePanel({
  outcomes,
}: {
  outcomes: PortfolioSnapshot["signal_outcomes"];
}) {
  const rows = outcomes.summary || [];
  const state = outcomes.state;
  return (
    <div className="signal-outcome-panel">
      {state ? (
        <div className={`signal-outcome-state signal-outcome-state--${state.status || "empty"}`}>
          <strong>{text(state.message)}</strong>
          <div>
            <span>成熟样本 {formatValueForField("ready_count", state.ready_count)}</span>
            <span>待成熟 {formatValueForField("pending_count", state.pending_count)}</span>
            <span>总样本 {formatValueForField("total_count", state.total_count)}</span>
            {state.next_ready_date ? <span>下次成熟 {formatValueForField("next_ready_date", state.next_ready_date)}</span> : null}
          </div>
        </div>
      ) : null}
      <div className="signal-outcome-note">
        这里展示的是“策略 × 跟踪周期”的收益复盘，不是线上模型数量。T+1/T+5/T+20 分别表示成交后 1、5、20 个交易日的效果观察窗口。
      </div>
      {rows.length ? (
        <DataTable
          rows={rows}
          columns={[
            "strategy_label",
            "online_scope",
            "trading_role",
            "horizon_label",
            "sample_count",
            "pending_count",
            "hit_rate",
            "avg_return",
            "avg_alpha_vs_benchmark",
            "strategy_logic",
          ]}
        />
      ) : null}
    </div>
  );
}
