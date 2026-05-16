export function formatCurrency(value: unknown): string {
  const number = Number(value ?? 0);
  return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

export function formatPercent(value: unknown): string {
  const number = Number(value ?? 0);
  return `${(number * 100).toFixed(1)}%`;
}

export function formatNumber(value: unknown, digits = 2): string {
  const number = Number(value ?? 0);
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
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
