import { DataTable } from "../components/DataTable";
import type { ResearchSummary } from "../types";
import { formatNumber, text } from "../utils";

type Props = {
  data: ResearchSummary;
};

export function ResearchPage({ data }: Props) {
  return (
    <section className="page-main">
      <div className="page-title-row">
        <div>
          <h1>研究实验室</h1>
          <p>高级研究内容默认收纳，散户操作主线留在前四个模块。</p>
        </div>
        {data.legacy_streamlit ? (
          <a className="secondary-link" href={data.legacy_streamlit.url} rel="noreferrer" target="_blank">
            {data.legacy_streamlit.label}
          </a>
        ) : null}
      </div>
      <section className="two-column">
        <div className="panel">
          <h2>Production 模型</h2>
          <dl className="account-dl">
            <dt>版本</dt>
            <dd>{text(data.production_model?.model_version)}</dd>
            <dt>实验</dt>
            <dd>{text(data.production_model?.experiment_id)}</dd>
            <dt>状态</dt>
            <dd>{text(data.production_model?.status)}</dd>
          </dl>
        </div>
        <div className="panel">
          <h2>IC / ICIR</h2>
          <dl className="account-dl">
            <dt>IC</dt>
            <dd>{formatNumber(data.ic.ic, 4)}</dd>
            <dt>RankIC</dt>
            <dd>{formatNumber(data.ic.rank_ic, 4)}</dd>
            <dt>ICIR</dt>
            <dd>{formatNumber(data.ic.icir, 4)}</dd>
          </dl>
        </div>
      </section>
      <section className="panel">
        <h2>最近实验</h2>
        <DataTable empty="暂无实验记录" rows={data.recent_experiments} columns={["experiment_id", "model_name", "model_version", "mode", "status", "started_at"]} />
      </section>
    </section>
  );
}
