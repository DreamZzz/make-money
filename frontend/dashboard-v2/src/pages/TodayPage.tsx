import { ArrowRight, PlayCircle } from "lucide-react";

import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { OperationSummary } from "../components/OperationSummary";
import { RiskAlertStack } from "../components/RiskAlertStack";
import type { RouteKey } from "../components/AppShell";
import type { TodaySnapshot } from "../types";
import { formatCurrency, text } from "../utils";

type Props = {
  data: TodaySnapshot;
  onNavigate: (route: RouteKey) => void;
  onStartJob: (jobKey: string) => void;
};

export function TodayPage({ data, onNavigate, onStartJob }: Props) {
  const next = data.next_action;
  return (
    <div className="page-grid page-grid--with-evidence">
      <section className="page-main">
        <div className="page-title-row">
          <div>
            <h1>今日行动</h1>
            <p>交易日 {data.trade_date || "暂无"}，先判断数据能不能用，再决定是否调仓。</p>
          </div>
          <button
            className="primary-cta"
            disabled={next.enabled === false}
            onClick={() => {
              if (next.job_key) onStartJob(next.job_key);
              else if (next.href) onNavigate(next.href as RouteKey);
            }}
            type="button"
          >
            {next.job_key ? <PlayCircle size={18} /> : <ArrowRight size={18} />}
            {next.label}
          </button>
        </div>

        <OperationSummary summary={data.operation_summary} />

        <section className="workflow-panel">
          <h2>收盘后流程</h2>
          <div className="checklist">
            <Step index={1} title="数据可用性" detail={data.health.label} done={!data.health.blocking} />
            <Step index={2} title="统一资金池" detail={`现金 ${formatCurrency(data.account.cash)}`} done />
            <Step index={3} title="调仓建议" detail={`${data.operation_summary.operation_count} 次操作待确认`} done={data.operation_summary.operation_count >= 0} />
            <Step index={4} title="风险确认" detail={`${data.blockers.length} 个阻塞/警告`} done={data.blockers.length === 0} />
            <Step index={5} title="手动执行记录" detail="执行后录入现金流或基金快照" done={false} />
          </div>
        </section>

        <section className="two-column">
          <div className="panel">
            <h2>阻塞项</h2>
            <RiskAlertStack alerts={data.blockers} />
          </div>
          <div className="panel">
            <h2>账户摘要</h2>
            <dl className="account-dl">
              <dt>总资产</dt>
              <dd>{formatCurrency(data.account.total_value)}</dd>
              <dt>现金</dt>
              <dd>{formatCurrency(data.account.cash)}</dd>
              <dt>NAV</dt>
              <dd>{text(data.account.nav)}</dd>
              <dt>回撤</dt>
              <dd>{text(data.account.drawdown)}</dd>
            </dl>
          </div>
        </section>
      </section>
      <EvidenceDrawer evidence={data.evidence} />
    </div>
  );
}

function Step({ index, title, detail, done }: { index: number; title: string; detail: string; done: boolean }) {
  return (
    <div className={done ? "check-step check-step--done" : "check-step"}>
      <span>{index}</span>
      <div>
        <strong>{title}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}
