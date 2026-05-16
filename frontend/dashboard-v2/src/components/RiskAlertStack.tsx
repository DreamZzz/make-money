import { AlertCircle, CheckCircle2 } from "lucide-react";

import type { RiskAlert } from "../types";
import { cssStatus, text } from "../utils";

type Props = {
  alerts: RiskAlert[];
};

export function RiskAlertStack({ alerts }: Props) {
  if (!alerts.length) {
    return (
      <div className="risk-empty">
        <CheckCircle2 size={18} />
        <span>暂无风险警告</span>
      </div>
    );
  }

  return (
    <ul className="risk-stack">
      {alerts.map((alert, index) => {
        const level = cssStatus(alert.level);
        return (
          <li className={`risk-alert risk-alert--${level}`} key={`${alert.metric || alert.label || alert.title}-${index}`}>
            <AlertCircle size={18} />
            <div>
              <strong>{alert.label || alert.title || alert.metric || "风险提示"}</strong>
              <span>{alert.message || alert.detail || text(alert.value)}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
