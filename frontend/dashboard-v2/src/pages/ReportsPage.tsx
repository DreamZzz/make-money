import type { ReportsSnapshot } from "../types";
import { formatPercent } from "../utils";

type Props = { data: ReportsSnapshot };

const SENTIMENT_COLOR: Record<string, string> = {
  POSITIVE: "var(--positive, #4ade80)",
  NEUTRAL: "var(--muted)",
  NEGATIVE: "var(--negative, #ff6b6b)",
};

export function ReportsPage({ data }: Props) {
  const sd = data.sentiment_distribution;
  const sentTotal = sd.POSITIVE + sd.NEUTRAL + sd.NEGATIVE;
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>财报分析</h1>
          <p>CSI300/500 + 恒生科技成分股的业绩预告/快报事件流;sentiment 注入 arbiter,不直接发交易信号</p>
        </div>
        {data.as_of_date ? <span className="funding-gap"><small>as of</small><strong>{data.as_of_date}</strong></span> : null}
      </div>

      <section className="panel">
        <h2>覆盖范围</h2>
        <div style={{ display: "flex", gap: 18, marginTop: 6, fontFamily: "var(--font-mono)" }}>
          <span>候选池 <strong>{data.coverage.universe_size}</strong></span>
          <span>CSI300+CSI500 <strong>{data.coverage.csi_size}</strong></span>
          <span>恒生科技 <strong>{data.coverage.hstech_size}</strong></span>
        </div>
      </section>

      <section className="panel" style={{ marginTop: 14 }}>
        <h2>30 日 sentiment 分布</h2>
        {sentTotal === 0 ? (
          <div className="empty-panel">无最近 30 日 earnings_alerts</div>
        ) : (
          <div style={{ marginTop: 8 }}>
            <div style={{ display: "flex", height: 24, borderRadius: 4, overflow: "hidden", border: "1px solid var(--line)" }}>
              {(["POSITIVE", "NEUTRAL", "NEGATIVE"] as const).map((k) => {
                const v = sd[k];
                if (v <= 0) return null;
                return (
                  <div key={k} style={{ width: `${(v / sentTotal) * 100}%`, background: SENTIMENT_COLOR[k],
                                          display: "flex", alignItems: "center", justifyContent: "center",
                                          fontFamily: "var(--font-mono)", fontSize: 11, color: "#000" }}>
                    {v}
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", gap: 14, marginTop: 6, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {(["POSITIVE", "NEUTRAL", "NEGATIVE"] as const).map((k) => (
                <span key={k}>
                  <i style={{ display: "inline-block", width: 8, height: 8, background: SENTIMENT_COLOR[k], marginRight: 4 }}></i>
                  {k}: {sd[k]} ({formatPercent(sd[k] / sentTotal)})
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="panel" style={{ marginTop: 14 }}>
        <h2>今日披露 ({data.today_disclosed.length})</h2>
        {data.today_disclosed.length === 0 ? (
          <div className="empty-panel">今日候选池内无新披露</div>
        ) : (
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>代码</th><th>名称</th><th>类型</th><th>Sentiment</th>
                <th>Impact</th><th>净利同比</th><th>vs 预告</th><th>headline</th>
              </tr>
            </thead>
            <tbody>
              {data.today_disclosed.map((r) => (
                <tr key={r.symbol}>
                  <td><strong>{r.symbol}</strong></td>
                  <td>{r.name}</td>
                  <td><small>{r.event_type}</small></td>
                  <td><span style={{ color: SENTIMENT_COLOR[r.sentiment] || "var(--ink)", fontFamily: "var(--font-mono)" }}>{r.sentiment}</span></td>
                  <td><span style={{ color: r.impact_score >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
                    {r.impact_score >= 0 ? "+" : ""}{r.impact_score.toFixed(2)}
                  </span></td>
                  <td>{r.np_yoy !== null ? formatPercent(r.np_yoy / 100) : "—"}</td>
                  <td>{r.surprise_pct !== null ? formatPercent(r.surprise_pct / 100) : "—"}</td>
                  <td><small style={{ color: "var(--muted)" }}>{r.headline}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel" style={{ marginTop: 14 }}>
        <h2>未来 7 日财报日历 ({data.upcoming_7d.length})</h2>
        {data.upcoming_7d.length === 0 ? (
          <div className="empty-panel">候选池内未来 7 日无预约披露(可能日历未刷新)</div>
        ) : (
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>披露日</th><th>代码</th><th>名称</th><th>行业</th><th>类型</th><th>universe</th>
              </tr>
            </thead>
            <tbody>
              {data.upcoming_7d.slice(0, 30).map((r, i) => (
                <tr key={`${r.symbol}-${i}`}>
                  <td>{r.disclosure_date}</td>
                  <td><strong>{r.symbol}</strong></td>
                  <td>{r.name}</td>
                  <td><small>{r.industry}</small></td>
                  <td><small>{r.disclosure_type}</small></td>
                  <td><small style={{ color: "var(--muted)" }}>{r.universe}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel" style={{ marginTop: 14 }}>
        <h2>Top Surprises (30 日)</h2>
        {data.top_surprises.length === 0 ? (
          <div className="empty-panel">无明显 surprise 数据</div>
        ) : (
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>代码</th><th>事件日</th><th>Surprise</th><th>Sentiment</th><th>Impact</th><th>headline</th>
              </tr>
            </thead>
            <tbody>
              {data.top_surprises.map((r) => (
                <tr key={`${r.symbol}-${r.event_date}`}>
                  <td><strong>{r.symbol}</strong></td>
                  <td>{r.event_date}</td>
                  <td><strong style={{ color: r.surprise_pct >= 0 ? "var(--positive, #4ade80)" : "var(--negative, #ff6b6b)" }}>
                    {r.surprise_pct >= 0 ? "+" : ""}{r.surprise_pct.toFixed(1)}%
                  </strong></td>
                  <td><span style={{ color: SENTIMENT_COLOR[r.sentiment] || "var(--ink)" }}>{r.sentiment}</span></td>
                  <td>{r.impact_score.toFixed(2)}</td>
                  <td><small style={{ color: "var(--muted)" }}>{r.headline}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </section>
  );
}
