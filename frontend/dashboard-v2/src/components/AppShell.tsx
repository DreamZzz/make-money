import { Activity, Beaker, BookOpenText, ClipboardList, HeartPulse, LayoutDashboard, Trophy } from "lucide-react";

import type { HealthState } from "../types";
import { DataHealthRibbon } from "./DataHealthRibbon";

export type RouteKey = "/today" | "/rebalance" | "/portfolio" | "/health" | "/tournament" | "/research" | "/guide";

const NAV_ITEMS: Array<{ path: RouteKey; label: string; icon: React.ReactNode }> = [
  { path: "/today", label: "今日行动", icon: <LayoutDashboard size={18} /> },
  { path: "/rebalance", label: "调仓执行", icon: <ClipboardList size={18} /> },
  { path: "/portfolio", label: "组合体检", icon: <Activity size={18} /> },
  { path: "/health", label: "市场与数据健康", icon: <HeartPulse size={18} /> },
  { path: "/tournament", label: "策略竞赛", icon: <Trophy size={18} /> },
  { path: "/research", label: "研究实验室", icon: <Beaker size={18} /> },
  { path: "/guide", label: "使用手册", icon: <BookOpenText size={18} /> },
];

type Props = {
  route: RouteKey;
  health: HealthState;
  onNavigate: (route: RouteKey) => void;
  children: React.ReactNode;
};

export function AppShell({ route, health, onNavigate, children }: Props) {
  return (
    <div className="app-shell">
      <DataHealthRibbon health={health} />
      <div className="workspace">
        <aside className="side-nav">
          <div className="brand">
            <span className="brand__mark">MM</span>
            <div>
              <strong>make-money</strong>
              <span>收盘调仓驾驶舱</span>
            </div>
          </div>
          <nav aria-label="主导航">
            {NAV_ITEMS.map((item) => (
              <button
                className={item.path === route ? "nav-item nav-item--active" : "nav-item"}
                key={item.path}
                onClick={() => onNavigate(item.path)}
                type="button"
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>
        <main className="main-surface">{children}</main>
      </div>
    </div>
  );
}
