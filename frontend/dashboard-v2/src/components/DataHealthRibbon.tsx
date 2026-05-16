import { AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";

import type { HealthState } from "../types";
import { cssStatus } from "../utils";

type Props = {
  health: HealthState;
};

export function DataHealthRibbon({ health }: Props) {
  const status = cssStatus(health.status);
  const Icon = status === "ok" ? CheckCircle2 : status === "failed" ? AlertTriangle : Clock3;

  return (
    <div className={`health-ribbon health-ribbon--${status}`}>
      <div className="health-ribbon__main">
        <Icon size={18} aria-hidden="true" />
        <strong>{health.label}</strong>
        {health.blocking ? <span className="health-ribbon__block">阻塞调仓</span> : null}
      </div>
      <div className="health-ribbon__messages">
        {(health.messages?.length ? health.messages : ["数据状态已同步"]).slice(0, 2).map((message) => (
          <span key={message}>{message}</span>
        ))}
      </div>
    </div>
  );
}
