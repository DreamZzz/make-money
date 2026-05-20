import { DataTable } from "../components/DataTable";
import type { PortfolioSnapshot, RiskAlert } from "../types";
import { fieldLabel, formatCurrency, formatPercent, formatValueForField, text } from "../utils";

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
        <span>回撤 {formatPercent(data.account.drawdown)}</span>
      </div>
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
