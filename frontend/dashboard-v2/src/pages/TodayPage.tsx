import { ArrowRight, ChevronRight } from "lucide-react";

import type { RouteKey } from "../components/AppShell";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { RiskAlertStack } from "../components/RiskAlertStack";
import type { TodayMarket, TodaySnapshot } from "../types";
import { formatCurrency, formatNumber, formatPercent } from "../utils";

type Props = {
  data: TodaySnapshot;
  onNavigate: (route: RouteKey) => void;
};

const ALLOC_COLORS = ["alloc-seg--c0", "alloc-seg--c1", "alloc-seg--c2"];
const LEGEND_COLORS = ["var(--accent)", "#4aa8ff", "#b98cff"];

export function TodayPage({ data, onNavigate }: Props) {
  const next = data.next_action;
  const market = data.market;
  const blocking = data.health.blocking;

  return (
    <div className="page-grid page-grid--with-evidence">
      <section className="page-main cockpit">
        <div className="page-title-row">
          <div>
            <h1>市场驾驶舱</h1>
            <p>交易日 {data.trade_date || "暂无"} · 以指数为核心：看市场 → 定仓位 → 配指数</p>
          </div>
          <button
            className="primary-cta"
            disabled={next.enabled === false}
            onClick={() => next.href && onNavigate(next.href as RouteKey)}
            type="button"
          >
            <ArrowRight size={18} />
            {next.label}
          </button>
        </div>

        {market?.state ? <MarketStrip market={market} /> : null}
        <DecisionCard market={market} onNavigate={onNavigate} />

        <div className="gate-row">
          <span className={`status-dot status-dot--${blocking ? "bad" : data.health.status === "degraded" ? "warn" : "ok"}`} />
          <strong style={{ fontFamily: "var(--font-display)", letterSpacing: "0.04em" }}>
            {blocking ? "数据不可用" : "数据可用"}
          </strong>
          <span className="cmp" style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12.5 }}>
            {data.health.label}
          </span>
          <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 12.5 }}>
            {data.blockers.length === 0 ? "无阻塞项" : `${data.blockers.length} 个阻塞/警告`}
          </span>
        </div>

        {data.blockers.length > 0 ? (
          <section className="panel">
            <h2>阻塞 / 风险</h2>
            <RiskAlertStack alerts={data.blockers} />
          </section>
        ) : null}

        <details className="shadow-fold">
          <summary>
            <ChevronRight size={14} />
            卫星个股信号
            <strong style={{ fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
              {market?.satellite_shadow_signals ?? 0}
            </strong>
            <span className="shadow-fold__tag">shadow · 不占主仓</span>
          </summary>
          <p style={{ marginTop: 10 }}>
            主动选股目前为研究/影子态，未跑赢指数前不占主仓。详情见 调仓执行 与 策略竞赛。
          </p>
        </details>

        <section className="panel">
          <h2>账户摘要</h2>
          <dl className="account-dl">
            <dt>统一总资产</dt>
            <dd>{formatCurrency(data.capital?.unified_total_value ?? data.account.total_value)}</dd>
            <dt>现金</dt>
            <dd>{formatCurrency(data.capital?.cash ?? data.account.cash)}</dd>
            <dt>NAV</dt>
            <dd>{formatNumber(data.account.nav, 4)}</dd>
            <dt>回撤</dt>
            <dd>{formatPercent(data.account.drawdown)}</dd>
          </dl>
        </section>
      </section>
      <EvidenceDrawer evidence={data.evidence} />
    </div>
  );
}

function MarketStrip({ market }: { market: TodayMarket }) {
  const s = market.state!;
  const heat = Number(s.heat_score ?? 0);
  return (
    <div className="market-strip">
      <span className="market-strip__stage">{s.stage || "—"}</span>
      <div className="market-strip__item">
        <span className="k">趋势分</span>
        <span className="v">{formatNumber(s.stage_score, 0)}</span>
      </div>
      <div className="market-strip__item">
        <span className="k">热度 {formatNumber(heat, 0)}</span>
        <span className="heatbar"><span className="heatbar__fill" style={{ width: `${Math.max(0, Math.min(100, heat))}%` }} /></span>
      </div>
      <div className="market-strip__item">
        <span className="k">估值分位</span>
        <span className="v">{s.pe_pct_10y == null ? "—" : formatPercent(s.pe_pct_10y, 0)}</span>
      </div>
      <span className="market-strip__summary">{s.summary || ""}</span>
    </div>
  );
}

function DecisionCard({ market, onNavigate }: { market?: TodayMarket; onNavigate: (r: RouteKey) => void }) {
  if (!market || market.target_exposure == null) {
    return (
      <div className="decision-card">
        <span className="decision-card__eyebrow">今日核心决策</span>
        <p style={{ marginTop: 12 }}>暂无市场仓位信号，请先运行市场层（python -m src.market.daily）。</p>
      </div>
    );
  }
  const action = (market.exposure?.action || "HOLD").toUpperCase();
  const actionCls = action === "ADD" ? "action--add" : action === "REDUCE" ? "action--reduce" : "action--hold";
  const actionLabel = action === "ADD" ? "加仓" : action === "REDUCE" ? "减仓" : "维持";
  const cur = market.current_exposure;
  const gap = market.exposure_gap;
  const funds = market.allocation.filter((a) => (a.weight ?? 0) > 0);
  const cash = Math.max(0, 1 - funds.reduce((s, a) => s + (a.weight ?? 0), 0));

  return (
    <div className="decision-card">
      <span className="decision-card__eyebrow">今日核心决策 · 目标权益仓位</span>
      <div className="decision-row">
        <span className="exposure-big">{formatPercent(market.target_exposure, 0)}</span>
        <div className="exposure-meta">
          <span className={`action ${actionCls}`}>{actionLabel}</span>
          <span className="cmp">
            当前 {cur == null ? "—" : formatPercent(cur, 0)}
            {gap != null ? `（差 ${gap >= 0 ? "+" : ""}${formatPercent(gap, 0)}）` : ""}
          </span>
        </div>
        <button className="secondary-link" style={{ marginLeft: "auto" }} onClick={() => onNavigate("/market")} type="button">
          市场温度计 <ArrowRight size={16} />
        </button>
      </div>

      <div className="alloc-bar">
        {funds.map((a, i) => (
          <div
            key={a.fund_code}
            className={`alloc-seg ${ALLOC_COLORS[i % ALLOC_COLORS.length]}`}
            style={{ width: `${(a.weight ?? 0) * 100}%` }}
            title={`${a.index_name || a.fund_code} ${formatPercent(a.weight)}`}
          >
            {(a.weight ?? 0) >= 0.1 ? formatPercent(a.weight, 0) : ""}
          </div>
        ))}
        {cash > 0.001 ? (
          <div className="alloc-seg alloc-seg--cash" style={{ width: `${cash * 100}%` }}>
            {cash >= 0.1 ? formatPercent(cash, 0) : ""}
          </div>
        ) : null}
      </div>
      <div className="alloc-legend">
        {funds.map((a, i) => (
          <span key={a.fund_code}>
            <i style={{ background: LEGEND_COLORS[i % LEGEND_COLORS.length] }} />
            {a.index_name || a.fund_code} {formatPercent(a.weight)}
          </span>
        ))}
        <span><i style={{ background: "var(--surface-subtle)", border: "1px solid var(--line-strong)" }} />现金 {formatPercent(cash)}</span>
      </div>
    </div>
  );
}
