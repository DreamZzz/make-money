export type HealthState = {
  status: "ok" | "degraded" | "failed" | string;
  label: string;
  blocking?: boolean;
  messages?: string[];
};

export type OperationSummaryData = {
  operation_count: number;
  cash_required: number;
  estimated_minutes: number;
  buy_count?: number;
  reduce_count?: number;
  funding_gap?: number;
};

export type RebalanceItem = {
  sleeve?: string;
  instrument_type?: string;
  instrument_id: string;
  instrument_name?: string;
  display_name?: string;
  action: string;
  current_value?: number;
  target_value?: number;
  budget_delta?: number;
  execution_mode?: string;
  expected_cash?: number;
  cash_effect?: number;
  budget_consumption?: number;
  priority?: number;
  reason?: string;
  bucket_reason?: string;
};

export type RebalanceGroups = {
  budget?: RebalanceItem[];
  executable: RebalanceItem[];
  confirm: RebalanceItem[];
  deferred: RebalanceItem[];
};

export type SatelliteCandidateRow = {
  symbol: string;
  name?: string;
  display_name?: string;
  one_lot_cash: number;
  confidence?: number;
  score?: number;
  rank_score?: number;
  model_name?: string;
  signal_count?: number;
  budget?: number;
  budget_gap?: number;
  budget_status?: "covered" | "over_budget" | string;
  budget_status_label?: string;
  execution_status?: "executable_candidate" | "budget_blocked" | "below_threshold" | string;
  execution_status_label?: string;
  passes_execution_threshold?: boolean;
  decision?: string;
};

export type SatelliteCandidateContext = {
  budget: number;
  base_budget?: number;
  sell_release_estimate?: number;
  candidate_count: number;
  covered_count: number;
  over_budget_count: number;
  executable_count?: number;
  budget_blocked_count?: number;
  threshold_blocked_count?: number;
  max_one_lot_cash?: number;
  decision_hint?: string;
  thresholds?: {
    min_confidence?: number;
    min_rank_score?: number;
  };
  rows: SatelliteCandidateRow[];
};

export type SellSignalRow = {
  symbol: string;
  name?: string;
  display_name?: string;
  strategy_name?: string;
  quantity?: number;
  market_value?: number;
  market?: string;
  estimated_release_cash?: number;
  pnl?: number;
  pnl_pct?: number;
  confidence?: number;
  score?: number;
  model_name?: string;
  signal_count?: number;
  signal_date?: string;
  decision?: string;
};

export type RiskAlert = {
  level?: "ok" | "info" | "warning" | "error" | string;
  label?: string;
  title?: string;
  metric?: string;
  message?: string;
  detail?: string;
  value?: number;
  threshold?: number;
  severity?: string;
  severity_reason?: string;
  suggested_actions?: string[];
  affected_holdings?: Record<string, unknown>[];
};

export type TodaySnapshot = {
  trade_date: string | null;
  health: HealthState;
  account: Record<string, unknown>;
  operation_summary: OperationSummaryData;
  blockers: RiskAlert[];
  next_action: { label: string; href?: string; enabled?: boolean };
  evidence: Record<string, unknown>;
};

export type RebalanceSnapshot = {
  plan_id: string | null;
  plan_date: string | null;
  summary: OperationSummaryData;
  groups: RebalanceGroups;
  sell_signals?: SellSignalRow[];
  conflicts: Record<string, unknown>[];
  one_lot_gaps?: Record<string, unknown>[];
  satellite_candidates?: SatelliteCandidateContext;
  evidence: Record<string, unknown>;
};

export type PortfolioSnapshot = {
  account: Record<string, unknown>;
  holdings: Record<string, unknown>[];
  risk_alerts: RiskAlert[];
  exposure: {
    industry: Record<string, unknown>[];
    size: Record<string, unknown>[];
    summary: Record<string, unknown>;
    insights?: Record<string, unknown>[];
  };
  signal_outcomes: {
    summary: Record<string, unknown>[];
    monthly?: Record<string, unknown>[];
    detail?: Record<string, unknown>[];
    state?: {
      status?: string;
      message?: string;
      ready_count?: number;
      pending_count?: number;
      total_count?: number;
      next_ready_date?: string | null;
    };
  };
  evidence?: Record<string, unknown>;
};

export type HealthSnapshot = HealthState & {
  latest_quote_date: string | null;
  data_sources: Record<string, unknown>[];
  field_coverage: Record<string, unknown>[];
  scheduled_jobs: Record<string, unknown>[];
  scheduled_job_history: Record<string, unknown>[];
  qlib: Record<string, unknown>;
  latest_job: Record<string, unknown> | null;
  failure_diagnostic: Record<string, unknown> | null;
};

export type ResearchSummary = {
  production_model: Record<string, unknown> | null;
  recent_experiments: Record<string, unknown>[];
  ic: Record<string, unknown>;
  portana: Record<string, unknown>;
  legacy_streamlit?: { label: string; url: string };
};
