"""Global signal arbitration before paper execution.

Strategies produce proposals.  The arbiter is the single place that decides
which proposals are allowed to reach the execution engine.
"""
from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.config import load_config

ARBITER_VERSION = "signal_arbiter_v1"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


@dataclass(frozen=True)
class ArbitrationResult:
    total: int
    accepted: int
    rejected: int


def arbitrate_pending_signals(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
    config: dict | None = None,
) -> ArbitrationResult:
    """Arbitrate active stock signals and persist one decision per signal."""
    config = config or load_config()
    arbiter_cfg = config.get("portfolio", {}).get("signal_arbiter", {})
    if not bool(arbiter_cfg.get("enabled", True)):
        return _accept_all_active(conn, as_of=as_of)

    signals = _load_active_signals(conn, as_of=as_of)
    if signals.empty:
        return ArbitrationResult(total=0, accepted=0, rejected=0)

    qlib = _load_latest_qlib_predictions(conn)
    decisions = _build_decisions(signals, qlib, config)
    _persist_decisions(conn, decisions)
    _apply_rejections_to_signals(conn, decisions)
    accepted = int((decisions["decision"] == ACCEPTED).sum())
    rejected = int((decisions["decision"] == REJECTED).sum())
    logger.info(f"Signal arbiter: accepted={accepted} rejected={rejected} total={len(decisions)}")
    return ArbitrationResult(total=len(decisions), accepted=accepted, rejected=rejected)


def _load_active_signals(conn: duckdb.DuckDBPyConnection, as_of: date | None = None) -> pd.DataFrame:
    params: list[Any] = []
    as_of_filter = ""
    if as_of is not None:
        as_of_filter = "AND CAST(s.signal_ts AS DATE) <= ?"
        params.append(as_of)
    return conn.execute(f"""
        SELECT s.signal_id, s.model_name, s.model_version, s.symbol, s.side, s.signal_ts,
               s.score, s.confidence, COALESCE(si.country, 'CN') AS market
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.executed = FALSE
          AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
          {as_of_filter}
        ORDER BY s.signal_ts ASC, s.model_name, s.symbol
    """, params).fetchdf()


def _load_latest_qlib_predictions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    try:
        return conn.execute("""
            SELECT symbol, prediction_date, rank, confidence, score, model_version
            FROM qlib_predictions
            WHERE model_name = 'alpha158'
              AND mode = 'production_inference'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY prediction_date DESC, selected DESC, rank ASC
            ) = 1
        """).fetchdf()
    except Exception:
        return pd.DataFrame(columns=["symbol", "prediction_date", "rank", "confidence", "score", "model_version"])


def _build_decisions(signals: pd.DataFrame, qlib: pd.DataFrame, config: dict) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    signal_df = signals.copy()
    signal_df["signal_date"] = pd.to_datetime(signal_df["signal_ts"]).dt.date
    signal_df["side_norm"] = signal_df["side"].fillna("").astype(str).str.upper()

    qlib_by_symbol = {
        str(row["symbol"]): row
        for _, row in qlib.iterrows()
    } if not qlib.empty else {}

    for _, row in signal_df.iterrows():
        rows.append(_base_decision(row, qlib_by_symbol.get(str(row["symbol"])), config))

    decisions = pd.DataFrame(rows)
    if decisions.empty:
        return decisions

    _reject_buys_when_symbol_has_sell(decisions)
    _keep_best_per_symbol_side(decisions)
    return decisions


def _base_decision(signal: pd.Series, qlib_row: pd.Series | None, config: dict) -> dict[str, Any]:
    side = str(signal.get("side_norm") or "").upper()
    model_name = str(signal.get("model_name") or "")
    score = _to_float(signal.get("score"))
    confidence = _to_float(signal.get("confidence"))
    priority_score = _priority_score(model_name, side, confidence, score, qlib_row)
    qlib_meta = _qlib_meta(qlib_row)
    common = {
        "decision_id": f"DEC-{uuid.uuid4().hex[:10].upper()}",
        "signal_id": signal.get("signal_id"),
        "decision_date": date.today(),
        "model_name": model_name,
        "model_version": signal.get("model_version"),
        "symbol": signal.get("symbol"),
        "side": side,
        "signal_ts": signal.get("signal_ts"),
        "arbiter_version": ARBITER_VERSION,
        "priority_score": priority_score,
        **qlib_meta,
    }

    if side in {"SELL", "SHORT"}:
        return {
            **common,
            "decision": ACCEPTED,
            "decision_reason": "SELL/风险释放优先通过统一仲裁",
            "consensus_status": "SELL",
        }
    if side != "BUY":
        return {
            **common,
            "decision": REJECTED,
            "decision_reason": f"不支持的信号方向: {side or '-'}",
            "consensus_status": "UNSUPPORTED_SIDE",
        }

    portfolio_cfg = config.get("portfolio", {})
    min_conf = float(portfolio_cfg.get("min_rebalance_buy_confidence", 0.75) or 0.0)
    min_rank_score = float(portfolio_cfg.get("min_rebalance_buy_rank_score", 0.50) or 0.0)
    rank_score = confidence * max(score, 0.0)
    if confidence < min_conf or rank_score < min_rank_score:
        return {
            **common,
            "decision": REJECTED,
            "decision_reason": f"低于全局BUY门槛: confidence={confidence:.2f}, rank_score={rank_score:.2f}",
            "consensus_status": "LOW_SIGNAL_SCORE",
        }

    if model_name == "alpha158":
        return {
            **common,
            "decision": ACCEPTED,
            "decision_reason": "Alpha158 production BUY 通过统一仲裁",
            "consensus_status": "QLIB_HOLDING",
        }

    reject_reason, consensus_status = _rule_buy_consensus_reject_reason(signal, qlib_row, config)
    if reject_reason:
        return {
            **common,
            "decision": REJECTED,
            "decision_reason": reject_reason,
            "consensus_status": consensus_status,
        }
    return {
        **common,
        "decision": ACCEPTED,
        "decision_reason": _accepted_rule_buy_reason(qlib_row),
        "consensus_status": _accepted_consensus_status(qlib_row),
    }


def _rule_buy_consensus_reject_reason(
    signal: pd.Series,
    qlib_row: pd.Series | None,
    config: dict,
) -> tuple[str | None, str]:
    arbiter_cfg = config.get("portfolio", {}).get("signal_arbiter", {})
    if qlib_row is None:
        if bool(arbiter_cfg.get("block_when_missing", True)):
            return "Qlib共识不可用: 无 alpha158 production 预测，规则BUY不进入执行", "MISSING"
        return None, "MISSING_ALLOWED"

    pred_date = _as_date(qlib_row.get("prediction_date"))
    signal_date = _as_date(signal.get("signal_date"))
    max_stale_days = int(arbiter_cfg.get("max_prediction_stale_days", 3) or 0)
    stale_days = max((signal_date - pred_date).days, 0) if pred_date and signal_date else 9999
    if stale_days > max_stale_days:
        return (
            f"Qlib共识过期: prediction_date={pred_date}, signal_date={signal_date}, stale_days={stale_days}",
            "STALE",
        )

    rank = _to_int(qlib_row.get("rank"))
    confidence = _to_float(qlib_row.get("confidence"))
    max_rank = int(arbiter_cfg.get("max_rule_buy_rank", 500) or 500)
    min_confidence = float(arbiter_cfg.get("min_rule_buy_confidence", 0.45) or 0.0)
    if rank is None or rank > max_rank or confidence < min_confidence:
        rank_text = "-" if rank is None else str(rank)
        return (
            f"Qlib共识不足: rank={rank_text} > {max_rank} 或 confidence={confidence:.2f} < {min_confidence:.2f}",
            "DIVERGENCE",
        )
    return None, "CONSENSUS"


def _accepted_rule_buy_reason(qlib_row: pd.Series | None) -> str:
    if qlib_row is None:
        return "规则BUY通过统一仲裁；Qlib缺失但配置允许"
    rank = _to_int(qlib_row.get("rank"))
    confidence = _to_float(qlib_row.get("confidence"))
    return f"规则BUY通过统一仲裁；Alpha158 rank={rank}, confidence={confidence:.2f}"


def _accepted_consensus_status(qlib_row: pd.Series | None) -> str:
    if qlib_row is None:
        return "MISSING_ALLOWED"
    rank = _to_int(qlib_row.get("rank")) or 999999
    return "CONSENSUS" if rank <= 100 else "NEUTRAL"


def _reject_buys_when_symbol_has_sell(decisions: pd.DataFrame) -> None:
    sell_symbols = set(decisions.loc[
        (decisions["side"].isin(["SELL", "SHORT"])) & (decisions["decision"] == ACCEPTED),
        "symbol",
    ].astype(str))
    if not sell_symbols:
        return
    mask = (
        decisions["symbol"].astype(str).isin(sell_symbols)
        & decisions["side"].eq("BUY")
        & decisions["decision"].eq(ACCEPTED)
    )
    decisions.loc[mask, "decision"] = REJECTED
    decisions.loc[mask, "decision_reason"] = "同标的存在SELL风险释放信号，BUY被统一仲裁拒绝"
    decisions.loc[mask, "consensus_status"] = "CONFLICT_SELL"


def _keep_best_per_symbol_side(decisions: pd.DataFrame) -> None:
    active = decisions[
        decisions["decision"].eq(ACCEPTED)
        & decisions["side"].eq("BUY")
    ].copy()
    if active.empty:
        return
    active["_side_group"] = active["side"].replace({"SHORT": "SELL"})
    active = active.sort_values(
        ["symbol", "_side_group", "priority_score", "signal_ts"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    keep_ids = set(active.drop_duplicates(["symbol", "_side_group"], keep="first")["signal_id"])
    duplicate_mask = (
        decisions["decision"].eq(ACCEPTED)
        & decisions["side"].eq("BUY")
        & ~decisions["signal_id"].isin(keep_ids)
    )
    decisions.loc[duplicate_mask, "decision"] = REJECTED
    decisions.loc[duplicate_mask, "decision_reason"] = "同标的同方向已有更高优先级信号通过统一仲裁"
    decisions.loc[duplicate_mask, "consensus_status"] = "DUPLICATE_LOWER_PRIORITY"


def _persist_decisions(conn: duckdb.DuckDBPyConnection, decisions: pd.DataFrame) -> None:
    cols = [
        "decision_id", "signal_id", "decision_date", "model_name", "model_version",
        "symbol", "side", "signal_ts", "decision", "decision_reason",
        "consensus_status", "arbiter_version", "qlib_prediction_date", "qlib_rank",
        "qlib_confidence", "priority_score",
    ]
    insert_df = decisions[cols].copy()
    conn.register("_tmp_signal_decisions", insert_df)
    conn.execute("""
        INSERT OR REPLACE INTO signal_decisions (
            decision_id, signal_id, decision_date, model_name, model_version,
            symbol, side, signal_ts, decision, decision_reason,
            consensus_status, arbiter_version, qlib_prediction_date, qlib_rank,
            qlib_confidence, priority_score, updated_at
        )
        SELECT decision_id, signal_id, decision_date, model_name, model_version,
               symbol, side, signal_ts, decision, decision_reason,
               consensus_status, arbiter_version, qlib_prediction_date, qlib_rank,
               qlib_confidence, priority_score, CURRENT_TIMESTAMP
        FROM _tmp_signal_decisions
    """)
    conn.unregister("_tmp_signal_decisions")


def _apply_rejections_to_signals(conn: duckdb.DuckDBPyConnection, decisions: pd.DataFrame) -> None:
    rejected = decisions[decisions["decision"] == REJECTED][["signal_id", "decision_reason"]].copy()
    if rejected.empty:
        return
    conn.register("_tmp_rejected_signal_decisions", rejected)
    conn.execute("""
        UPDATE signals s
        SET executed = TRUE,
            status = 'NO_ACTION',
            status_reason = d.decision_reason,
            updated_at = CURRENT_TIMESTAMP
        FROM _tmp_rejected_signal_decisions d
        WHERE s.signal_id = d.signal_id
          AND s.executed = FALSE
          AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
    """)
    conn.unregister("_tmp_rejected_signal_decisions")


def _accept_all_active(conn: duckdb.DuckDBPyConnection, as_of: date | None = None) -> ArbitrationResult:
    signals = _load_active_signals(conn, as_of=as_of)
    if signals.empty:
        return ArbitrationResult(total=0, accepted=0, rejected=0)
    rows = []
    for _, row in signals.iterrows():
        rows.append({
            "decision_id": f"DEC-{uuid.uuid4().hex[:10].upper()}",
            "signal_id": row["signal_id"],
            "decision_date": date.today(),
            "model_name": row["model_name"],
            "model_version": row.get("model_version"),
            "symbol": row["symbol"],
            "side": row.get("side"),
            "signal_ts": row["signal_ts"],
            "decision": ACCEPTED,
            "decision_reason": "signal_arbiter.disabled: 直接通过",
            "consensus_status": "DISABLED",
            "arbiter_version": ARBITER_VERSION,
            "qlib_prediction_date": None,
            "qlib_rank": None,
            "qlib_confidence": None,
            "priority_score": _to_float(row.get("confidence")),
        })
    _persist_decisions(conn, pd.DataFrame(rows))
    return ArbitrationResult(total=len(rows), accepted=len(rows), rejected=0)


def _qlib_meta(qlib_row: pd.Series | None) -> dict[str, Any]:
    if qlib_row is None:
        return {"qlib_prediction_date": None, "qlib_rank": None, "qlib_confidence": None}
    return {
        "qlib_prediction_date": _as_date(qlib_row.get("prediction_date")),
        "qlib_rank": _to_int(qlib_row.get("rank")),
        "qlib_confidence": _to_float(qlib_row.get("confidence")),
    }


def _priority_score(model_name: str, side: str, confidence: float, score: float, qlib_row: pd.Series | None) -> float:
    if side in {"SELL", "SHORT"}:
        return 2.0 + confidence
    model_bonus = 0.2 if model_name == "alpha158" else 0.0
    qlib_bonus = 0.0
    rank = _to_int(qlib_row.get("rank")) if qlib_row is not None else None
    if rank is not None and rank > 0:
        qlib_bonus = max(0.0, (500 - min(rank, 500)) / 500) * 0.2
    return confidence + max(score, 0.0) * 0.1 + model_bonus + qlib_bonus


def _to_float(value: Any) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _to_int(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(value)
    except Exception:
        return None


def _as_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def main() -> int:
    parser = argparse.ArgumentParser(description="Arbitrate active stock signals before execution.")
    parser.add_argument("--as-of", help="Only arbitrate signals up to this date (YYYY-MM-DD).")
    args = parser.parse_args()

    from src.data_pipeline.loader import get_connection, init_db

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    conn = get_connection()
    try:
        init_db(conn)
        result = arbitrate_pending_signals(conn, as_of=as_of)
        print(f"Signal arbiter: accepted={result.accepted} rejected={result.rejected} total={result.total}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
