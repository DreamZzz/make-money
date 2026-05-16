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
  executable: RebalanceItem[];
  confirm: RebalanceItem[];
  deferred: RebalanceItem[];
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
};

export type TodaySnapshot = {
  trade_date: string | null;
  health: HealthState;
  account: Record<string, unknown>;
  operation_summary: OperationSummaryData;
  blockers: RiskAlert[];
  next_action: { label: string; href?: string; job_key?: string; enabled?: boolean };
  evidence: Record<string, unknown>;
};

export type RebalanceSnapshot = {
  plan_id: string | null;
  plan_date: string | null;
  summary: OperationSummaryData;
  groups: RebalanceGroups;
  conflicts: Record<string, unknown>[];
  one_lot_gaps?: Record<string, unknown>[];
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
  };
  signal_outcomes: {
    summary: Record<string, unknown>[];
    monthly?: Record<string, unknown>[];
    detail?: Record<string, unknown>[];
  };
  evidence?: Record<string, unknown>;
};

export type HealthSnapshot = HealthState & {
  latest_quote_date: string | null;
  data_sources: Record<string, unknown>[];
  field_coverage: Record<string, unknown>[];
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
