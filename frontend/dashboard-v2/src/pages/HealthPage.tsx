import { DataTable } from "../components/DataTable";
import { JobRunTimeline } from "../components/JobRunTimeline";
import type { HealthSnapshot } from "../types";

type Props = {
  data: HealthSnapshot;
};

export function HealthPage({ data }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>市场与数据健康</h1>
          <p>回答今天的数据能不能用于决策；任务由本机定时器执行，Dashboard 只展示状态和异常提醒。</p>
        </div>
      </div>
      <section className="panel">
        <h2>定时任务</h2>
        <DataTable
          empty="暂无定时任务配置"
          rows={data.scheduled_jobs}
          columns={[
            "label",
            "trigger",
            "watchdog_status_label",
            "next_due_at",
            "last_run_date",
            "last_result",
            "plist_status",
            "action_hint",
          ]}
        />
      </section>
      <section className="panel">
        <h2>定时执行历史</h2>
        <DataTable
          empty="暂无定时执行历史"
          limit={12}
          rows={data.scheduled_job_history}
          columns={[
            "job_name",
            { key: "scheduled_time", label: "计划时间" },
            { key: "started_at", label: "执行时间" },
            "ended_at",
            "duration_seconds",
            "schedule_alignment",
            { key: "status_label", label: "执行状态" },
            { key: "result", label: "执行结果" },
            "schedule_note",
          ]}
        />
      </section>
      <section className="two-column">
        <div className="panel">
          <h2>字段覆盖率</h2>
          <DataTable
            empty="暂无覆盖率数据"
            limit={16}
            rows={data.field_coverage}
            columns={["scope_label", "field_label", "covered_display", "coverage", "coverage_status", "decision_use"]}
          />
        </div>
        <div className="panel">
          <h2>最近定时任务</h2>
          <JobRunTimeline job={data.latest_job} failure={data.failure_diagnostic} />
        </div>
      </section>
      <section className="panel">
        <h2>数据源健康</h2>
        <DataTable
          empty="暂无数据源记录"
          limit={14}
          rows={data.data_sources}
          columns={["source", "market", "operation", "status", "updated", "failed", "message"]}
        />
      </section>
    </section>
  );
}
