import { cssStatus, text } from "../utils";

type Props = {
  job: Record<string, unknown> | null;
  failure?: Record<string, unknown> | null;
};

export function JobRunTimeline({ job, failure }: Props) {
  if (!job) {
    return <div className="empty-panel">暂无任务运行记录</div>;
  }
  const steps = Array.isArray(job.steps) ? (job.steps as Record<string, unknown>[]) : [];
  return (
    <section className="timeline" aria-label="任务步骤">
      <div className="timeline__summary">
        <strong>{text(job.job_label || job.job_key)}</strong>
        <span className={`status-chip status-chip--${cssStatus(String(job.status))}`}>
          {text(job.status_label || job.status)}
        </span>
      </div>
      <ol>
        {steps.map((step) => (
          <li key={String(step.key)}>
            <span className={`timeline-dot timeline-dot--${cssStatus(String(step.status))}`} />
            <div>
              <strong>{text(step.label)}</strong>
              <span>{text(step.status_label || step.status)}</span>
            </div>
          </li>
        ))}
      </ol>
      {failure ? (
        <pre className="failure-box">{text(failure.cmd_text)}
{text(failure.log_excerpt)}</pre>
      ) : null}
    </section>
  );
}
