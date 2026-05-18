export function formatCurrency(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

export function formatPercent(value: unknown, digits = 1): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${(number * 100).toFixed(digits)}%`;
}

export function formatNumber(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function formatDateTime(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const normalized = raw.replace("T", " ");
  return normalized.slice(0, 16);
}

export const FIELD_LABELS: Record<string, string> = {
  action: "动作",
  action_hint: "说明",
  attempted: "尝试数",
  avg_cost: "成本价",
  avg_alpha_vs_benchmark: "平均超额",
  avg_return: "平均收益",
  benchmark_value: "基准占比",
  cash_effect: "现金影响",
  confidence: "置信度",
  coverage: "覆盖率",
  coverage_status: "覆盖状态",
  current_price: "现价",
  current_value: "当前金额",
  covered_display: "已覆盖/总数",
  duration_seconds: "耗时",
  decision_use: "用途",
  ended_at: "结束时间",
  error_message: "错误信息",
  expected_cash: "预计现金",
  failed: "失败数",
  field: "字段",
  field_label: "字段",
  fund_code: "基金",
  hit_count: "命中数",
  hit_rate: "命中率",
  holding_days: "已持有天数",
  horizon_days: "跟踪周期",
  industry: "行业",
  instrument_id: "标的",
  label: "任务",
  job_name: "任务",
  latest_signal_count: "最近信号数",
  latest_signal_side: "最近信号",
  last_ended_at: "最近结束",
  last_result: "最近结果",
  last_run_date: "最近执行日",
  last_started_at: "最近开始",
  market: "市场",
  market_cap: "总市值",
  market_value: "持仓市值",
  message: "说明",
  mode: "模式",
  model_name: "模型",
  model_version: "模型版本",
  name: "名称",
  next_ready_date: "下次成熟日期",
  next_due_at: "下次应执行",
  operation: "操作",
  pb: "PB",
  pb_coverage: "PB覆盖率",
  pe_coverage: "PE覆盖率",
  pe_ttm: "PE(TTM)",
  pending_count: "待成熟数",
  pnl: "浮盈亏",
  pnl_pct: "浮盈亏率",
  position_count: "持仓数",
  qlib_confidence: "Qlib置信度",
  qlib_prediction_date: "Qlib预测日期",
  qlib_rank: "Qlib排名",
  qlib_score: "Qlib分数",
  ready_count: "已成熟数",
  sample_count: "样本数",
  schedule_alignment: "时间判断",
  schedule_note: "时间说明",
  side_count: "方向数",
  sides: "信号方向",
  signal_count: "信号数",
  scope: "范围",
  scope_label: "范围",
  source: "数据源",
  started_at: "开始时间",
  status: "状态",
  status_label: "任务状态",
  strategy_name: "策略",
  symbol: "标的",
  target_value: "目标金额",
  threshold: "阈值",
  top5_weight: "Top5权重",
  total_count: "总样本数",
  trade_date: "日期",
  trigger: "触发时间",
  watchdog_status: "调度状态",
  watchdog_status_label: "调度状态",
  scheduled_time: "计划时间",
  plist_status: "配置状态",
  script: "执行脚本",
  updated: "更新数",
  updated_at: "更新时间",
  value: "当前占比",
  weight: "仓位",
  weight_change_7d: "7日仓位变化",
  weight_change_20d: "20日仓位变化",
};

const CURRENCY_FIELDS = new Set([
  "budget_consumption",
  "budget_delta",
  "cash",
  "cash_effect",
  "cost_amount",
  "current_value",
  "expected_cash",
  "funding_gap",
  "market_value",
  "net_contribution",
  "one_lot_cash",
  "pnl",
  "position_value",
  "target_value",
  "total_value",
]);

const PERCENT_FIELDS = new Set([
  "avg_alpha_vs_benchmark",
  "avg_return",
  "benchmark_value",
  "confidence",
  "coverage",
  "core_drift_pct",
  "core_target_pct",
  "daily_return",
  "drawdown",
  "hit_rate",
  "max_drawdown",
  "pb_coverage",
  "pe_coverage",
  "pnl_pct",
  "satellite_drift_pct",
  "satellite_target_pct",
  "target_weight",
  "top5_weight",
  "value",
  "weight",
  "weight_change_7d",
  "weight_change_20d",
  "qlib_confidence",
]);

const INTEGER_FIELDS = new Set([
  "attempted",
  "failed",
  "hit_count",
  "holding_days",
  "horizon_days",
  "latest_signal_count",
  "pending_count",
  "position_count",
  "quantity",
  "qlib_rank",
  "ready_count",
  "sample_count",
  "side_count",
  "signal_count",
  "total_count",
  "updated",
]);

const PRICE_FIELDS = new Set(["avg_cost", "close", "current_price", "nav", "pb", "pe_ttm"]);
const DECIMAL_FIELDS = new Set(["ic", "icir", "rank_ic", "sharpe_ratio", "sortino_ratio", "info_ratio"]);
const DATE_FIELDS = new Set(["created_at", "ended_at", "published_at", "signal_ts", "started_at", "trade_date", "updated_at"]);

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] || field;
}

export function formatInstrumentLabel(row: Record<string, unknown>, key = "symbol"): string {
  const code = text(row[key] ?? row.symbol ?? row.fund_code ?? row.instrument_id);
  const displayName = row.display_name ? String(row.display_name) : "";
  const instrumentType = String(row.instrument_type || "").toLowerCase();
  if (instrumentType === "sleeve" || code === "core" || code === "satellite") {
    return displayName || String(row.instrument_name || code);
  }
  if (displayName && (displayName.includes(code) || displayName === code)) return displayName;
  const name = row.instrument_name || row.name || row.fund_name;
  if (name && String(name) !== code) return `${String(name)}（${code}）`;
  return code;
}

export function translateAction(action: unknown): string {
  const value = String(action || "").toUpperCase();
  const labels: Record<string, string> = {
    ADD: "加仓",
    BUY: "买入",
    HOLD: "持有",
    PAUSE: "暂缓",
    REDUCE: "减仓",
    SELL: "卖出",
  };
  return labels[value] || text(action);
}

export function translateBudgetAction(action: unknown): string {
  const value = String(action || "").toUpperCase();
  if (value === "ADD" || value === "BUY") return "预留预算";
  if (value === "REDUCE" || value === "SELL") return "释放预算";
  return translateAction(action);
}

export function translateSide(side: unknown): string {
  const raw = String(side || "");
  if (raw.includes(",")) return raw.split(",").map(translateSide).join(" / ");
  return translateAction(raw);
}

export function translateSleeve(sleeve: unknown): string {
  const value = String(sleeve || "").toLowerCase();
  if (value === "core") return "Core 基金";
  if (value === "satellite") return "Satellite 个股";
  return text(sleeve);
}

export function translateInstrumentType(type: unknown): string {
  const value = String(type || "").toLowerCase();
  const labels: Record<string, string> = {
    index_fund: "指数基金",
    sleeve: "资金池",
    stock: "股票",
    stock_strategy: "股票策略",
  };
  return labels[value] || text(type);
}

export function translateStatus(status: unknown): string {
  const value = String(status || "");
  const labels: Record<string, string> = {
    ACTIVE: "有效",
    FAILED: "失败",
    MISSED: "已错过",
    RUNNING: "运行中",
    SUCCEEDED: "成功",
    WAITING: "等待窗口",
    UNKNOWN: "未知",
    failed: "失败",
    ok: "正常",
    degraded: "需确认",
  };
  return labels[value] || text(status);
}

export function formatValueForField(field: string, value: unknown, row: Record<string, unknown> = {}): string {
  if (value === null || value === undefined || value === "") return "-";
  if (["symbol", "fund_code", "instrument_id"].includes(field)) return formatInstrumentLabel(row, field);
  if (field === "action") return translateAction(value);
  if (field === "sides" || field === "side" || field === "latest_signal_side") return translateSide(value);
  if (field === "sleeve") return translateSleeve(value);
  if (field === "instrument_type") return translateInstrumentType(value);
  if (field === "status") return translateStatus(value);
  if (field === "duration_seconds") return formatDurationSeconds(value);
  if (CURRENCY_FIELDS.has(field)) return formatCurrency(value);
  if (PERCENT_FIELDS.has(field)) return formatPercent(value);
  if (INTEGER_FIELDS.has(field)) return formatNumber(value, 0);
  if (PRICE_FIELDS.has(field)) return formatNumber(value, field === "nav" ? 4 : 2);
  if (DECIMAL_FIELDS.has(field)) return formatNumber(value, 4);
  if (DATE_FIELDS.has(field) || field.endsWith("_date")) return formatDateTime(value);
  if (typeof value === "number") return formatNumber(value, 2);
  if (typeof value === "object") return JSON.stringify(value);
  return text(value);
}

function formatDurationSeconds(value: unknown): string {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return remain > 0 ? `${minutes} 分 ${remain} 秒` : `${minutes} 分钟`;
}

export function text(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

export function cssStatus(status?: string): string {
  if (status === "ok" || status === "SUCCEEDED") return "ok";
  if (status === "failed" || status === "FAILED" || status === "error") return "failed";
  return "degraded";
}
