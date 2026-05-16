import { useEffect, useMemo, useState } from "react";

import { apiGet, apiPost } from "./api";
import { AppShell, type RouteKey } from "./components/AppShell";
import { ErrorPanel, LoadingPanel } from "./components/StatePanel";
import { HealthPage } from "./pages/HealthPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { RebalancePage } from "./pages/RebalancePage";
import { ResearchPage } from "./pages/ResearchPage";
import { TodayPage } from "./pages/TodayPage";
import type { HealthSnapshot, PortfolioSnapshot, RebalanceSnapshot, ResearchSummary, TodaySnapshot } from "./types";

const DEFAULT_HEALTH = { status: "degraded", label: "数据加载中", blocking: false, messages: ["正在连接 Dashboard V2 API"] };

function currentRoute(): RouteKey {
  const path = window.location.pathname as RouteKey;
  if (["/today", "/rebalance", "/portfolio", "/health", "/research"].includes(path)) return path;
  return "/today";
}

export function App() {
  const [route, setRoute] = useState<RouteKey>(currentRoute());
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [today, setToday] = useState<TodaySnapshot | null>(null);
  const [rebalance, setRebalance] = useState<RebalanceSnapshot | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null);
  const [research, setResearch] = useState<ResearchSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobMessage, setJobMessage] = useState<string | null>(null);

  const healthState = useMemo(() => health || today?.health || DEFAULT_HEALTH, [health, today]);

  useEffect(() => {
    void loadRoute(route);
  }, [route]);

  function navigate(next: RouteKey) {
    setRoute(next);
    window.history.pushState(null, "", next);
  }

  async function loadRoute(next: RouteKey) {
    setError(null);
    try {
      const healthData = await apiGet<HealthSnapshot>("/api/v2/health");
      setHealth(healthData);
      if (next === "/today") setToday(await apiGet<TodaySnapshot>("/api/v2/today"));
      if (next === "/rebalance") setRebalance(await apiGet<RebalanceSnapshot>("/api/v2/rebalance/latest"));
      if (next === "/portfolio") setPortfolio(await apiGet<PortfolioSnapshot>("/api/v2/portfolio"));
      if (next === "/research") setResearch(await apiGet<ResearchSummary>("/api/v2/research/summary"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function startJob(jobKey: string) {
    try {
      const result = await apiPost<{ run_id: string }>(`/api/v2/jobs/${jobKey}/start`);
      setJobMessage(`任务已启动：${result.run_id}`);
      navigate("/health");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <AppShell route={route} health={healthState} onNavigate={navigate}>
      {jobMessage ? <div className="job-toast">{jobMessage}</div> : null}
      {error ? <ErrorPanel message={error} /> : renderPage(route)}
    </AppShell>
  );

  function renderPage(next: RouteKey) {
    if (next === "/today") {
      return today ? <TodayPage data={today} onNavigate={navigate} onStartJob={startJob} /> : <LoadingPanel message="正在加载今日行动" />;
    }
    if (next === "/rebalance") {
      return rebalance ? <RebalancePage data={rebalance} /> : <LoadingPanel message="正在加载调仓计划" />;
    }
    if (next === "/portfolio") {
      return portfolio ? <PortfolioPage data={portfolio} /> : <LoadingPanel message="正在加载组合体检" />;
    }
    if (next === "/health") {
      return health ? <HealthPage data={health} /> : <LoadingPanel message="正在加载数据健康" />;
    }
    return research ? <ResearchPage data={research} /> : <LoadingPanel message="正在加载研究摘要" />;
  }
}
