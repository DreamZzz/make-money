import { useEffect, useMemo, useState } from "react";

import { apiGet } from "./api";
import { AppShell, type RouteKey } from "./components/AppShell";
import { ErrorPanel, LoadingPanel } from "./components/StatePanel";
import { FundsPage } from "./pages/FundsPage";
import { HealthPage } from "./pages/HealthPage";
import { MarketPage } from "./pages/MarketPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { RebalancePage } from "./pages/RebalancePage";
import { ResearchPage } from "./pages/ResearchPage";
import { TodayPage } from "./pages/TodayPage";
import { TournamentPage } from "./pages/TournamentPage";
import { UserGuidePage } from "./pages/UserGuidePage";
import type { FundsSnapshot, HealthSnapshot, MarketSnapshot, PortfolioSnapshot, RebalanceSnapshot, ResearchSummary, TodaySnapshot, TournamentSnapshot } from "./types";

const DEFAULT_HEALTH = { status: "degraded", label: "数据加载中", blocking: false, messages: ["正在连接 Dashboard V2 API"] };

function currentRoute(): RouteKey {
  const path = window.location.pathname as RouteKey;
  if (["/today", "/funds", "/rebalance", "/portfolio", "/market", "/health", "/tournament", "/research", "/guide"].includes(path)) return path;
  return "/today";
}

export function App() {
  const [route, setRoute] = useState<RouteKey>(currentRoute());
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [today, setToday] = useState<TodaySnapshot | null>(null);
  const [rebalance, setRebalance] = useState<RebalanceSnapshot | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null);
  const [research, setResearch] = useState<ResearchSummary | null>(null);
  const [tournament, setTournament] = useState<TournamentSnapshot | null>(null);
  const [market, setMarket] = useState<MarketSnapshot | null>(null);
  const [funds, setFunds] = useState<FundsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      if (next === "/market") setMarket(await apiGet<MarketSnapshot>("/api/v2/market"));
      if (next === "/funds") setFunds(await apiGet<FundsSnapshot>("/api/v2/funds"));
      if (next === "/tournament") setTournament(await apiGet<TournamentSnapshot>("/api/v2/tournament"));
      if (next === "/research") setResearch(await apiGet<ResearchSummary>("/api/v2/research/summary"));
    } catch (err) {
      if (next === "/guide") return;
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <AppShell route={route} health={healthState} onNavigate={navigate}>
      {error ? <ErrorPanel message={error} /> : renderPage(route)}
    </AppShell>
  );

  function renderPage(next: RouteKey) {
    if (next === "/today") {
      return today ? <TodayPage data={today} onNavigate={navigate} /> : <LoadingPanel message="正在加载今日行动" />;
    }
    if (next === "/rebalance") {
      return rebalance ? <RebalancePage data={rebalance} /> : <LoadingPanel message="正在加载调仓计划" />;
    }
    if (next === "/portfolio") {
      return portfolio ? <PortfolioPage data={portfolio} /> : <LoadingPanel message="正在加载组合体检" />;
    }
    if (next === "/market") {
      return market ? <MarketPage data={market} /> : <LoadingPanel message="正在加载市场温度计" />;
    }
    if (next === "/funds") {
      return funds ? <FundsPage data={funds} /> : <LoadingPanel message="正在加载基金评估" />;
    }
    if (next === "/health") {
      return health ? <HealthPage data={health} /> : <LoadingPanel message="正在加载数据健康" />;
    }
    if (next === "/tournament") {
      return tournament ? <TournamentPage data={tournament} /> : <LoadingPanel message="正在加载策略竞赛" />;
    }
    if (next === "/guide") {
      return <UserGuidePage />;
    }
    return research ? <ResearchPage data={research} /> : <LoadingPanel message="正在加载研究摘要" />;
  }
}
