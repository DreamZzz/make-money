"""R3: earnings context modifier 注入 signal_arbiter。

不污染 signals 表,只在 arbitrate_pending_signals 决策完成后调整:
- priority_score += weight * impact_score (SELL 翻号: 利空对 SELL 加分)
- decision_reason 追加 " | earnings:POSITIVE(+0.18)" 可审计字符串
- 可选 buy_negative_block: BUY+NEGATIVE 直接改 REJECTED
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd

# 决策枚举(与 arbiter.py 保持一致)
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


def load_recent_earnings_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of: date | None = None,
    lookback_days: int = 30,
) -> dict[str, dict[str, Any]]:
    """symbol → 最近一条 earnings_alerts 的关键字段。

    返回 dict 每行: {sentiment, impact_score, event_date, event_type, headline}
    """
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='earnings_alerts'"
    ).fetchone():
        return {}
    rows = conn.execute(
        """
        SELECT symbol, sentiment, impact_score, event_date, event_type, headline
        FROM earnings_alerts
        WHERE event_date >= ? AND event_date <= ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol ORDER BY event_date DESC
        ) = 1
        """,
        [start, as_of],
    ).fetchall()
    return {r[0]: {"sentiment": r[1], "impact_score": float(r[2] or 0.0),
                    "event_date": r[3], "event_type": r[4], "headline": r[5]}
            for r in rows}


def apply_earnings_modifier(
    decisions: pd.DataFrame,
    context: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> pd.DataFrame:
    """根据 earnings context 调整 ACCEPTED 决策的 priority_score 和 decision_reason。

    config 字段:
      - weight: priority_score 调整系数(默认 0.15)
      - buy_negative_block: BUY + NEGATIVE 是否直接 REJECT(默认 False, 首版不拒)
    """
    if decisions.empty or not context:
        return decisions
    weight = float(config.get("weight", 0.15))
    buy_block = bool(config.get("buy_negative_block", False))

    df = decisions.copy()
    if "priority_score" not in df.columns:
        df["priority_score"] = 0.0
    if "decision_reason" not in df.columns:
        df["decision_reason"] = ""

    for idx, row in df.iterrows():
        symbol = row.get("symbol")
        if symbol not in context:
            continue
        ctx = context[symbol]
        sentiment = ctx["sentiment"]
        impact = float(ctx["impact_score"])
        side = str(row.get("side") or "").upper()

        # 优先处理 buy_negative_block: BUY + NEGATIVE → REJECT
        decision = str(row.get("decision") or "")
        if (buy_block and decision == ACCEPTED and side == "BUY"
                and sentiment == "NEGATIVE"):
            df.at[idx, "decision"] = REJECTED
            df.at[idx, "decision_reason"] = (
                str(row.get("decision_reason") or "")
                + f" | earnings:NEGATIVE(impact={impact:+.2f})拒收 BUY"
            )
            continue

        # 只对 ACCEPTED 调分;REJECTED 仅追加可追溯字符串
        if decision == ACCEPTED:
            # SELL/SHORT 时翻号(利空 → SELL 加分)
            sign = -1 if side in {"SELL", "SHORT"} else 1
            adj = sign * weight * impact
            df.at[idx, "priority_score"] = float(row["priority_score"]) + adj
            df.at[idx, "decision_reason"] = (
                str(row.get("decision_reason") or "")
                + f" | earnings:{sentiment}({adj:+.3f})"
            )
        else:
            # REJECTED 也加 trace 信息(仅追加 reason,不动 score)
            df.at[idx, "decision_reason"] = (
                str(row.get("decision_reason") or "")
                + f" | earnings:{sentiment}(impact={impact:+.2f})"
            )

    return df
