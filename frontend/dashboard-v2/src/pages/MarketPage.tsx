import { DataTable } from "../components/DataTable";
import { Sparkline } from "../components/Sparkline";
import type { MarketSnapshot } from "../types";
import { formatNumber, formatPercent } from "../utils";

type Props = {
  data: MarketSnapshot;
};

const RS_NAMES: Record<string, string> = { "000300": "沪深300", "000905": "中证500", HSTECH: "恒生科技" };

export function MarketPage({ data }: Props) {
  const s = data.market_state;
  const e = data.exposure;

  if (!s) {
    return (
      <section className="page-main">
        <div className="page-title-row"><div><h1>市场温度计</h1></div></div>
        <div className="empty-panel">暂无市场状态数据，请先运行 `python -m src.market.daily`</div>
      </section>
    );
  }

  const allocRows = data.allocation.map((a) => ({
    index_name: a.index_name,
    fund_code: a.fund_code,
    rs_rank: a.rs_rank,
    rs_score: a.rs_score == null ? "—" : formatPercent(a.rs_score),
    weight: formatPercent(a.weight),
  }));

  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>市场温度计</h1>
          <p>{s.trade_date} · 阶段 / 宽度 / 热度 / 估值 / 相对强弱 综合判断，驱动指数核心仓位与搭配。</p>
        </div>
      </div>

      <section className="panel panel--ok">
        <h2>专业判读</h2>
        <p><strong>{s.summary}</strong></p>
      </section>

      {data.history && data.history.length >= 2 ? (
        <section className="panel">
          <h2>趋势（近 {data.history.length} 个交易日）</h2>
          <Sparkline
            label="趋势分 stage_score（-100~100，虚线=0）"
            values={data.history.map((h) => h.stage_score)}
            min={-100}
            max={100}
            refLine={0}
            color="#1d6f5b"
          />
          <Sparkline
            label="热度分 heat_score（0~100，虚线=50 常态）"
            values={data.history.map((h) => h.heat_score)}
            min={0}
            max={100}
            refLine={50}
            color="#b4540a"
          />
        </section>
      ) : null}

      <section className="two-column">
        <div className="panel">
          <h2>阶段与热度</h2>
          <dl className="account-dl">
            <dt>阶段</dt><dd>{s.stage}（趋势分 {formatNumber(s.stage_score, 0)}）</dd>
            <dt>热度分</dt><dd>{formatNumber(s.heat_score, 0)} / 100</dd>
            <dt>量能比</dt><dd>{formatNumber(s.volume_ratio, 2)}×</dd>
            <dt>相对强弱领先</dt><dd>{RS_NAMES[s.rs_leader || ""] || s.rs_leader || "—"}</dd>
          </dl>
        </div>
        <div className="panel">
          <h2>宽度与估值</h2>
          <dl className="account-dl">
            <dt>站上年线(MA200)</dt><dd>{formatPercent(s.breadth_above_ma200)}</dd>
            <dt>站上MA50</dt><dd>{formatPercent(s.breadth_above_ma50)}</dd>
            <dt>当日上涨家数</dt><dd>{formatPercent(s.advance_ratio)}</dd>
            <dt>PE 近10年分位</dt><dd>{formatPercent(s.pe_pct_10y)}</dd>
            <dt>PB 近10年分位</dt><dd>{formatPercent(s.pb_pct_10y)}</dd>
          </dl>
        </div>
      </section>

      {e ? (
        <section className="panel">
          <h2>T+1 仓位建议</h2>
          <p>
            <strong>目标权益仓位 {formatPercent(e.target_exposure)}</strong>
            （基准 {formatPercent(e.base_exposure)} · 估值 {formatPercent(e.valuation_adj)} · 宽度 {formatPercent(e.breadth_adj)} · 热度 {formatPercent(e.heat_adj)}）
          </p>
          <p className="muted">动作：{e.action} —— {e.advice}</p>
        </section>
      ) : null}

      <section className="panel">
        <h2>指数搭配（相对强弱轮动）</h2>
        <DataTable
          empty="暂无指数搭配"
          rows={allocRows}
          columns={[
            { key: "index_name", label: "指数" },
            { key: "fund_code", label: "基金" },
            { key: "rs_rank", label: "强弱排名" },
            { key: "rs_score", label: "动量" },
            { key: "weight", label: "目标权重" },
          ]}
        />
        <p className="muted">权重之和 = 权益预算；其余为现金。轮动超配动量领先指数、低配走弱指数。</p>
      </section>
    </section>
  );
}
