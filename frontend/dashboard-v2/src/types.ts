export type HealthState = {
  status: "ok" | "degraded" | "failed" | string;
  label: string;
  blocking?: boolean;
  messages?: string[];
};

export type OperationSummaryData = {
  operation_count: number;
  cash_required: number;
  reserved_cash?: number;
  cash_commitment?: number;
  available_cash_after_reserve?: number;
  available_cash_after_commitment?: number;
  estimated_minutes: number;
  buy_count?: number;
  reduce_count?: number;
  funding_gap?: number;
};

export type CapitalBreakdown = {
  scope?: string;
  scope_label?: string;
  scope_note?: string;
  formula?: string;
  unified_total_value?: number;
  trading_account_total_value?: number;
  trading_position_value?: number;
  cash?: number;
  core_value?: number;
  satellite_value?: number;
  core_budget?: number;
  satellite_budget?: number;
  reserved_cash?: number;
  unreserved_cash?: number;
  core_target_value?: number;
  satellite_target_value?: number;
  cash_target_value?: number;
  core_target_pct?: number;
  satellite_target_pct?: number;
  cash_target_pct?: number;
  reconciliation?: {
    formula?: string;
    computed_total?: number;
    recorded_total?: number;
    delta?: number;
    trading_account_formula?: string;
    trading_account_computed_total?: number;
    trading_account_recorded_total?: number;
    trading_account_delta?: number;
  };
};

export type RegimePolicy = {
  status?: "ok" | "not_applied" | "unavailable" | string;
  as_of_date?: string | null;
  regime_key?: string | null;
  regime_label?: string;
  stance?: string;
  application_state?: "advisory_only" | "applied_to_plan" | "not_applied" | string;
  buy_mode?: "normal" | "reduced" | "paused" | string;
  satellite_budget_multiplier?: number | null;
  signal_threshold_adjustment?: string | null;
  reason_summary?: string;
  source?: string;
  evidence?: Record<string, unknown>;
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
  target_position_cash?: number;
  rounded_qty?: number;
  execution_value?: number;
  fee?: number;
  required_cash?: number;
  execution_price?: number;
  confidence?: number;
  score?: number;
  rank_score?: number;
  model_name?: string;
  signal_count?: number;
  budget?: number;
  budget_gap?: number;
  budget_status?: "covered" | "over_budget" | string;
  budget_status_label?: string;
  execution_status?: "executable_candidate" | "budget_blocked" | "cash_blocked" | "lot_blocked" | "below_threshold" | string;
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
  lot_blocked_count?: number;
  cash_blocked_count?: number;
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

export type TodayMarket = {
  state: {
    stage?: string;
    stage_score?: number;
    heat_score?: number;
    pe_pct_10y?: number | null;
    summary?: string;
  } | null;
  exposure: { target_exposure?: number; action?: string; advice?: string } | null;
  allocation: Array<{ fund_code: string; index_name?: string; weight?: number; rs_rank?: number }>;
  current_exposure: number | null;
  target_exposure: number | null;
  exposure_gap: number | null;
  satellite_shadow_signals: number;
};

export type TodaySnapshot = {
  trade_date: string | null;
  health: HealthState;
  account: Record<string, unknown>;
  capital?: CapitalBreakdown;
  market?: TodayMarket;
  regime_policy?: RegimePolicy;
  operation_summary: OperationSummaryData;
  blockers: RiskAlert[];
  next_action: { label: string; href?: string; enabled?: boolean };
  evidence: Record<string, unknown>;
};

export type RebalanceSnapshot = {
  plan_id: string | null;
  plan_date: string | null;
  capital?: CapitalBreakdown;
  regime_policy?: RegimePolicy;
  summary: OperationSummaryData;
  budget_reason?: {
    status: "over_target" | "available" | "at_target" | "cash_short";
    headline: string;
    advice: string;
    current?: number;
    target?: number;
    over?: number;
  } | null;
  groups: RebalanceGroups;
  sell_signals?: SellSignalRow[];
  conflicts: Record<string, unknown>[];
  one_lot_gaps?: Record<string, unknown>[];
  satellite_candidates?: SatelliteCandidateContext;
  evidence: Record<string, unknown>;
};

export type PortfolioSnapshot = {
  account: Record<string, unknown>;
  capital?: CapitalBreakdown;
  regime_policy?: RegimePolicy;
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

export type DataHealthDomain = {
  market: string;
  operation: string;
  effective_status: "decidable" | "backup_active" | "degraded" | "failed" | string;
  headline: string;
  primary_source?: string | null;
  primary_status?: string | null;
  ok_sources: string[];
  partial_sources: string[];
  failed_sources: string[];
  is_critical: boolean;
  sources: Array<Record<string, unknown> & {
    source: string;
    effective_source_status: "ok" | "partial" | "failed" | "unknown" | string;
    update_ratio: number;
  }>;
};

export type DataHealthSummary = {
  as_of: string | null;
  overall: {
    today_decidable: boolean;
    status: "decidable" | "backup_active" | "degraded" | "failed" | "no_data" | string;
    headline: string;
    blockers: string[];
  };
  domains: DataHealthDomain[];
};

export type HealthSnapshot = HealthState & {
  latest_quote_date: string | null;
  data_health_summary?: DataHealthSummary | null;
  data_sources: Record<string, unknown>[];
  field_coverage: Record<string, unknown>[];
  scheduled_jobs: Record<string, unknown>[];
  scheduled_job_history: Record<string, unknown>[];
  regime_policy?: RegimePolicy;
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

export type TournamentSnapshot = {
  accounts: Array<{
    account_id: string;
    name: string;
    description?: string;
    initial_capital: number;
    status: string;
    is_real_candidate: boolean;
    models: string[];
    benchmark_index: string;
  }>;
  leaderboard: Array<Record<string, unknown>>;
  tournament: {
    ranking: Array<Record<string, unknown>>;
    eligible_count: number;
    recommended_winner: string | null;
    selection_note: string;
  };
  nav_curves: Record<string, Array<{ date: string | null; nav: number | null }>>;
  error?: string;
};

export type FundEvaluation = {
  eval_date: string | null;
  fund_code: string;
  fund_name: string | null;
  tracking_index: string | null;
  tracking_index_name: string | null;
  snapshot_date: string | null;
  snapshot_stale_days: number | null;
  shares: number | null;
  cost_amount: number | null;
  nav: number | null;
  nav_date: string | null;
  nav_stale_days: number | null;
  current_value: number | null;
  return_amount: number | null;
  return_pct: number | null;
  price_pct: number | null;
  ma_fast: number | null;
  ma_slow: number | null;
  trend_healthy: boolean | null;
  trend_weak: boolean | null;
  target_weight_m4: number | null;
  equity_exposure: number | null;
  target_value: number | null;
  target_account_weight: number | null;
  current_weight: number | null;
  current_account_weight: number | null;
  drift_pct: number | null;
  delta_amount: number | null;
  delta_shares: number | null;
  action: string;
  confidence: number;
  thesis: string;
  risk_tags: string[];
  account_total_value: number | null;
};

export type FundsSnapshot = {
  eval_date: string | null;
  account_total_value: number | null;
  equity_exposure: number | null;
  core_total_target_value: number;
  core_total_current_value: number;
  core_total_delta_amount: number;
  overall_advice: { headline: string; actions: string[] };
  funds: FundEvaluation[];
  error?: string;
};

export type MarketSnapshot = {
  market_state: {
    trade_date?: string;
    stage?: string;
    stage_score?: number;
    heat_score?: number;
    breadth_above_ma50?: number | null;
    breadth_above_ma200?: number | null;
    advance_ratio?: number | null;
    new_high_low_ratio?: number | null;
    volume_ratio?: number | null;
    pe_pct_10y?: number | null;
    pb_pct_10y?: number | null;
    rs_leader?: string | null;
    relative_strength?: Record<string, number>;
    summary?: string;
  } | null;
  exposure: {
    stage?: string;
    base_exposure?: number;
    valuation_adj?: number;
    breadth_adj?: number;
    heat_adj?: number;
    target_exposure?: number;
    action?: string;
    advice?: string;
  } | null;
  allocation: Array<Record<string, unknown>>;
  history?: Array<{ date: string | null; stage_score: number | null; heat_score: number | null }>;
  error?: string;
};
