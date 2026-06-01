import { useState } from "react";

import { apiPost } from "../api";

type Props = {
  open: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
  // 预填:点击某支基金卡片"录入今日快照"时,把 fund_code 带进来
  prefillFundCode?: string;
};

type Mode = "simple" | "broker_json";

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; id: string }
  | { kind: "error"; message: string };

// broker JSON 推荐字段(与 src/funds/evaluation._parse_snapshot_note 对齐)
const BROKER_RECOMMENDED_KEYS = [
  "source", "captured_at", "nav_date",
  "market_value", "latest_nav", "cost_price_display",
  "holding_pnl", "holding_return_pct", "day_return_pct",
  "yesterday_pnl", "holding_days",
];

const TODAY_ISO = new Date().toISOString().slice(0, 10);

export function SnapshotForm({ open, onClose, onSubmitted, prefillFundCode }: Props) {
  const [mode, setMode] = useState<Mode>("simple");
  const [fundCode, setFundCode] = useState(prefillFundCode || "");
  const [snapshotDate, setSnapshotDate] = useState(TODAY_ISO);
  const [shares, setShares] = useState("");
  const [costAmount, setCostAmount] = useState("");
  const [noteText, setNoteText] = useState("");
  const [brokerJson, setBrokerJson] = useState("");
  const [brokerParsed, setBrokerParsed] = useState<Record<string, unknown> | null>(null);
  const [brokerErrors, setBrokerErrors] = useState<string[]>([]);
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  if (!open) return null;

  function reset() {
    setFundCode(prefillFundCode || "");
    setSnapshotDate(TODAY_ISO);
    setShares("");
    setCostAmount("");
    setNoteText("");
    setBrokerJson("");
    setBrokerParsed(null);
    setBrokerErrors([]);
    setState({ kind: "idle" });
  }

  function validateBrokerJson(raw: string): { parsed: Record<string, unknown> | null; errors: string[] } {
    if (!raw.trim()) return { parsed: null, errors: ["请粘贴 JSON 内容"] };
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      return { parsed: null, errors: [`JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`] };
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { parsed: null, errors: ["JSON 必须是对象 {}"] };
    }
    const errors: string[] = [];
    const recognized = BROKER_RECOMMENDED_KEYS.filter((k) => k in parsed);
    if (recognized.length === 0) {
      errors.push("未识别任何 broker 推荐字段(market_value / latest_nav / holding_pnl 等)");
    }
    return { parsed, errors };
  }

  function handleParseBroker() {
    const { parsed, errors } = validateBrokerJson(brokerJson);
    setBrokerErrors(errors);
    setBrokerParsed(parsed);
    if (parsed) {
      // 自动从 broker 字段反推 shares + cost_amount(尽力)
      const mv = Number(parsed.market_value);
      const nav = Number(parsed.latest_nav);
      const costPrice = Number(parsed.cost_price_display);
      if (mv > 0 && nav > 0 && !shares) {
        setShares((mv / nav).toFixed(2));
      }
      if (shares && costPrice > 0 && !costAmount) {
        setCostAmount((Number(shares) * costPrice).toFixed(2));
      } else if (mv > 0 && nav > 0 && costPrice > 0 && !costAmount) {
        const inferredShares = mv / nav;
        setCostAmount((inferredShares * costPrice).toFixed(2));
      }
      const navDate = parsed.nav_date as string | undefined;
      if (typeof navDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(navDate)) {
        setSnapshotDate(navDate);
      }
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!fundCode.trim()) {
      setState({ kind: "error", message: "fund_code 不能为空" });
      return;
    }
    if (!shares || Number(shares) <= 0) {
      setState({ kind: "error", message: "shares 必须 > 0" });
      return;
    }
    let finalNote = noteText;
    if (mode === "broker_json") {
      if (!brokerParsed) {
        setState({ kind: "error", message: "请先点击 解析 JSON,确认无误" });
        return;
      }
      if (brokerErrors.length > 0) {
        setState({ kind: "error", message: brokerErrors[0] });
        return;
      }
      finalNote = brokerJson.trim();
    }
    const payload = {
      snapshot_date: snapshotDate,
      fund_code: fundCode.trim(),
      shares: Number(shares),
      cost_amount: Number(costAmount || 0),
      note: finalNote,
    };
    setState({ kind: "submitting" });
    try {
      const r = await apiPost<{ id: string; status: string }>("/api/v2/index-fund-snapshots", payload);
      setState({ kind: "success", id: r.id });
      onSubmitted?.();
      // 1.5s 后自动关闭
      setTimeout(() => { onClose(); reset(); }, 1500);
    } catch (err) {
      setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  return (
    <div className="modal-backdrop" style={backdropStyle} onClick={onClose}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
        <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>录入持仓快照</h2>
          <button type="button" onClick={onClose} style={closeBtnStyle}>×</button>
        </header>

        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <button type="button" onClick={() => setMode("simple")}
                  style={tabStyle(mode === "simple")}>
            简易手填
          </button>
          <button type="button" onClick={() => setMode("broker_json")}
                  style={tabStyle(mode === "broker_json")}>
            broker JSON 粘贴
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <Field label="fund_code" required>
            <input value={fundCode} onChange={(e) => setFundCode(e.target.value)}
                   placeholder="例如 013308" style={inputStyle} />
          </Field>
          <Field label="snapshot_date">
            <input type="date" value={snapshotDate} onChange={(e) => setSnapshotDate(e.target.value)}
                   style={inputStyle} />
          </Field>

          {mode === "simple" ? (
            <>
              <Field label="shares 份额" required>
                <input type="number" step="0.01" value={shares} onChange={(e) => setShares(e.target.value)}
                       placeholder="48831.82" style={inputStyle} />
              </Field>
              <Field label="cost_amount 累计成本(元)">
                <input type="number" step="0.01" value={costAmount} onChange={(e) => setCostAmount(e.target.value)}
                       placeholder="90000" style={inputStyle} />
              </Field>
              <Field label="note 备注(可选)">
                <input value={noteText} onChange={(e) => setNoteText(e.target.value)}
                       placeholder="例如 5-30 加仓后" style={inputStyle} />
              </Field>
            </>
          ) : (
            <>
              <div style={{ marginBottom: 10, padding: 10, borderRadius: 4,
                            background: "rgba(96,165,250,0.05)", borderLeft: "3px solid var(--accent, #60a5fa)",
                            fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
                把券商截图 OCR 出来的 JSON 粘到下面,系统会自动用 broker 真值替代 shares×nav。
                推荐字段:<code style={{ fontSize: 11 }}>{BROKER_RECOMMENDED_KEYS.join(", ")}</code>
              </div>
              <Field label="broker JSON" required>
                <textarea value={brokerJson} onChange={(e) => setBrokerJson(e.target.value)}
                          rows={8} placeholder='{"source":"manual_broker_screenshot","captured_at":"2026-05-30 19:33","market_value":105088.64,"latest_nav":1.1636,...}'
                          style={{ ...inputStyle, fontFamily: "var(--font-mono)", fontSize: 12 }} />
              </Field>
              <button type="button" onClick={handleParseBroker} style={parseBtnStyle}>
                解析 JSON 并预填
              </button>
              {brokerErrors.length > 0 ? (
                <div style={errorBlock}>
                  {brokerErrors.map((e, i) => <div key={i}>⚠ {e}</div>)}
                </div>
              ) : null}
              {brokerParsed ? (
                <div style={{ marginTop: 8, padding: 8, borderRadius: 4,
                              background: "rgba(74,222,128,0.08)", borderLeft: "3px solid var(--positive, #4ade80)",
                              fontSize: 11, fontFamily: "var(--font-mono)" }}>
                  ✓ 解析成功 · 识别到 {BROKER_RECOMMENDED_KEYS.filter((k) => k in brokerParsed).length} 个 broker 字段
                  {(brokerParsed.market_value as number) ? ` · 市值 ¥${Number(brokerParsed.market_value).toLocaleString()}` : ""}
                  {(brokerParsed.holding_return_pct as number) !== undefined
                    ? ` · 收益 ${(Number(brokerParsed.holding_return_pct) * 100).toFixed(2)}%` : ""}
                </div>
              ) : null}
              <Field label="shares 份额(从 JSON 自动反推,可改)" required>
                <input type="number" step="0.01" value={shares} onChange={(e) => setShares(e.target.value)}
                       style={inputStyle} />
              </Field>
              <Field label="cost_amount 累计成本(可改)">
                <input type="number" step="0.01" value={costAmount} onChange={(e) => setCostAmount(e.target.value)}
                       style={inputStyle} />
              </Field>
            </>
          )}

          {state.kind === "error" ? <div style={errorBlock}>提交失败: {state.message}</div> : null}
          {state.kind === "success" ? (
            <div style={{ ...errorBlock, background: "rgba(74,222,128,0.08)",
                          borderLeftColor: "var(--positive, #4ade80)", color: "var(--positive, #4ade80)" }}>
              ✓ 已落库 · id={state.id}
            </div>
          ) : null}

          <div style={{ display: "flex", gap: 8, marginTop: 18, justifyContent: "flex-end" }}>
            <button type="button" onClick={() => { reset(); onClose(); }} style={cancelBtnStyle}>取消</button>
            <button type="submit" disabled={state.kind === "submitting"} style={submitBtnStyle}>
              {state.kind === "submitting" ? "提交中..." : "提交快照"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: "block", fontSize: 12, color: "var(--muted)", marginBottom: 4,
                      fontFamily: "var(--font-mono)" }}>
        {label}{required ? <span style={{ color: "var(--negative, #ff6b6b)" }}> *</span> : null}
      </label>
      {children}
    </div>
  );
}

const backdropStyle: React.CSSProperties = {
  position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
  display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
};

const modalStyle: React.CSSProperties = {
  background: "var(--surface)", color: "var(--ink)",
  padding: "20px 24px", borderRadius: 8, width: "92%", maxWidth: 540,
  maxHeight: "90vh", overflow: "auto",
  border: "1px solid var(--line-strong)",
  boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
};

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 10px", fontSize: 13,
  background: "var(--surface-elevated, rgba(255,255,255,0.04))",
  border: "1px solid var(--line)", borderRadius: 4, color: "var(--ink)",
  fontFamily: "var(--font-mono)",
};

const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: "6px 14px", fontSize: 13, fontFamily: "var(--font-mono)",
  background: active ? "var(--surface-elevated, rgba(96,165,250,0.08))" : "transparent",
  border: active ? "1px solid var(--accent, #60a5fa)" : "1px solid var(--line)",
  borderRadius: 4, color: active ? "var(--accent, #60a5fa)" : "var(--muted)",
  cursor: "pointer",
});

const parseBtnStyle: React.CSSProperties = {
  padding: "5px 12px", fontSize: 12, fontFamily: "var(--font-mono)",
  background: "transparent", border: "1px solid var(--line-strong)",
  borderRadius: 4, color: "var(--accent, #60a5fa)", cursor: "pointer",
  marginBottom: 8,
};

const errorBlock: React.CSSProperties = {
  marginTop: 8, padding: 8, borderRadius: 4,
  background: "rgba(255,107,107,0.08)", borderLeft: "3px solid var(--negative, #ff6b6b)",
  color: "var(--negative, #ff6b6b)", fontSize: 12, lineHeight: 1.6, fontFamily: "var(--font-mono)",
};

const submitBtnStyle: React.CSSProperties = {
  padding: "8px 18px", fontSize: 13,
  background: "var(--accent, #60a5fa)", color: "#000",
  border: "none", borderRadius: 4, cursor: "pointer", fontWeight: 600,
};

const cancelBtnStyle: React.CSSProperties = {
  padding: "8px 18px", fontSize: 13,
  background: "transparent", color: "var(--muted)",
  border: "1px solid var(--line)", borderRadius: 4, cursor: "pointer",
};

const closeBtnStyle: React.CSSProperties = {
  background: "transparent", border: "none", fontSize: 24, color: "var(--muted)",
  cursor: "pointer", lineHeight: 1, padding: 0,
};
