import { DataTable } from "../components/DataTable";
import type { TournamentSnapshot } from "../types";
import { formatNumber, formatPercent } from "../utils";

type Props = {
  data: TournamentSnapshot;
};

export function TournamentPage({ data }: Props) {
  const { tournament, accounts } = data;
  const winner = tournament.recommended_winner;
  const winnerName = accounts.find((a) => a.account_id === winner)?.name || winner;

  const rows = data.leaderboard.map((r) => ({
    rank: r.rank,
    name: (r.name as string) || (r.account_id as string),
    annual_return: formatPercent(r.annual_return),
    excess_return: formatPercent(r.excess_return),
    sharpe_ratio: formatNumber(r.sharpe_ratio, 2),
    max_drawdown: formatPercent(r.max_drawdown),
    turnover: formatNumber(r.turnover, 1),
    hit_rate: r.hit_rate == null ? "—" : formatPercent(r.hit_rate, 0),
    ready_outcomes: r.ready_outcomes,
    sample_days: r.sample_days,
  }));

  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>策略竞赛</h1>
          <p>多个虚拟账户在相同行情下并行对标（历史回放预热 + 前向纸盘），选最优配置指导实盘。</p>
        </div>
      </div>

      <section className={winner ? "panel panel--ok" : "panel"}>
        <h2>晋级闸门</h2>
        {winner ? (
          <p>
            推荐冠军：<strong>{winnerName}</strong>（{winner}）。{tournament.selection_note}
          </p>
        ) : (
          <p>暂无可晋级实盘的冠军。{tournament.selection_note || "需账户达标且明显优于亚军（选择偏差守卫）。"}</p>
        )}
        <p className="muted">合格账户数：{tournament.eligible_count} / {accounts.length}</p>
        <p className="muted">
          注意：并行只缩短相对排名时间，不缩短统计验证时间。冠军需独立通过门槛且明显优于亚军，方可指导实盘。
        </p>
      </section>

      <section className="panel">
        <h2>竞赛榜（按年化超额排名）</h2>
        <DataTable
          empty="暂无回放/绩效数据，请先运行账户回放与指标刷新"
          rows={rows}
          limit={20}
          columns={[
            { key: "rank", label: "#" },
            { key: "name", label: "账户" },
            { key: "annual_return", label: "年化收益" },
            { key: "excess_return", label: "年化超额" },
            { key: "sharpe_ratio", label: "Sharpe" },
            { key: "max_drawdown", label: "最大回撤" },
            { key: "turnover", label: "年化换手" },
            { key: "hit_rate", label: "命中率" },
            { key: "ready_outcomes", label: "平仓笔数" },
            { key: "sample_days", label: "样本天数" },
          ]}
        />
      </section>

      <section className="panel">
        <h2>参赛账户</h2>
        <div className="account-grid">
          {accounts.map((a) => (
            <div className="account-card" key={a.account_id}>
              <div className="account-card__head">
                <strong>{a.name}</strong>
                {a.is_real_candidate ? <span className="badge badge--ok">实盘候选</span> : null}
                <span className="badge">{a.status}</span>
              </div>
              <p className="muted">{a.description}</p>
              <p className="muted">模型：{a.models.join("、") || "全部"}　基准：{a.benchmark_index}</p>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}
