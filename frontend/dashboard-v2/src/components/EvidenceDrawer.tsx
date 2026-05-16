import { Database, GitBranch, ShieldCheck } from "lucide-react";

import { text } from "../utils";

type Props = {
  evidence?: Record<string, unknown>;
};

export function EvidenceDrawer({ evidence = {} }: Props) {
  return (
    <aside className="evidence-drawer" aria-label="证据口径">
      <div className="evidence-drawer__head">
        <ShieldCheck size={18} />
        <h2>证据口径</h2>
      </div>
      <dl>
        <Evidence label="数据日期" value={evidence.data_date} />
        <Evidence label="信号日期" value={evidence.signal_date} />
        <Evidence label="模型版本" value={evidence.model_version} />
        <Evidence label="成本口径" value={evidence.cost_model} />
      </dl>
      <div className="evidence-drawer__note">
        <Database size={16} />
        <span>前端只读汇总口径，安全写入会进入审计日志。</span>
      </div>
      <div className="evidence-drawer__note">
        <GitBranch size={16} />
        <span>Streamlit 8501 保留为研究工作台。</span>
      </div>
    </aside>
  );
}

function Evidence({ label, value }: { label: string; value: unknown }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{text(value)}</dd>
    </>
  );
}
