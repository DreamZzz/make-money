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
from src.portfolio.regime_policy import load_latest_regime_policy

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

    baselines = _consensus_baselines(config)
    regime_policy = load_latest_regime_policy(conn, as_of=as_of, config=config)
    baseline_predictions = _load_latest_baseline_predictions(conn, baselines)
    # B2: 加载当前持仓集合，让无持仓 SELL 在套利层前置标记为 REJECTED
    try:
        from src.portfolio.current_holdings import load_current_position_symbols
        current_holdings = set(load_current_position_symbols(conn, as_of=as_of))
    except Exception:
        current_holdings = set()
    decisions = _build_decisions(
        signals, baseline_predictions, config, baselines,
        regime_policy=regime_policy, current_holdings=current_holdings,
    )
    # R3: earnings context modifier(可关）
    earnings_cfg = arbiter_cfg.get("earnings_modifier", {})
    if bool(earnings_cfg.get("enabled", False)):
        try:
            from src.financials.arbiter_modifier import (
                apply_earnings_modifier,
                load_recent_earnings_context,
            )
            ctx = load_recent_earnings_context(
                conn, as_of=as_of,
                lookback_days=int(earnings_cfg.get("lookback_days", 30)),
            )
            decisions = apply_earnings_modifier(decisions, ctx, earnings_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"earnings modifier 异常,跳过: {exc}")
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


def _consensus_baselines(config: dict) -> list[str]:
    arbiter_cfg = config.get("portfolio", {}).get("signal_arbiter", {})
    raw = arbiter_cfg.get("consensus_baselines", ["alpha158"])
    if raw is None:
        return ["alpha158"]
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _empty_baseline_predictions() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "model_name",
        "symbol",
        "prediction_date",
        "rank",
        "confidence",
        "score",
        "model_version",
    ])


def _load_latest_baseline_predictions(
    conn: duckdb.DuckDBPyConnection,
    model_names: list[str],
    as_of: date | None = None,
) -> pd.DataFrame:
    """加载共识基准的最新预测。

    ``as_of`` 给定时只取 prediction_date <= as_of 的预测（历史回放防 look-ahead）；
    默认 None 取全部最新预测（前向日常调用）。
    """
    if not model_names:
        return _empty_baseline_predictions()
    placeholders = ",".join(["?"] * len(model_names))
    params: list[Any] = list(model_names)
    as_of_filter = ""
    if as_of is not None:
        as_of_filter = "AND qp.prediction_date <= ?"
        params.append(as_of)
    try:
        return conn.execute(
            f"""
            WITH production AS (
                SELECT model_name, model_version
                FROM qlib_model_registry
                WHERE status = 'production'
                  AND model_name IN ({placeholders})
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY model_name
                    ORDER BY published_at DESC NULLS LAST, created_at DESC NULLS LAST
                ) = 1
            ),
            candidate_predictions AS (
                SELECT qp.model_name, qp.symbol, qp.prediction_date, qp.rank, qp.confidence,
                       qp.score, qp.model_version, qp.selected
                FROM qlib_predictions qp
                JOIN production p
                  ON p.model_name = qp.model_name
                 AND p.model_version = qp.model_version
                WHERE qp.mode = 'production_inference'
                  {as_of_filter}
            )
            SELECT model_name, symbol, prediction_date, rank, confidence, score, model_version
            FROM candidate_predictions
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY model_name, symbol ORDER BY prediction_date DESC, selected DESC, rank ASC
            ) = 1
            """,
            params,
        ).fetchdf()
    except Exception:
        return _empty_baseline_predictions()


def _build_decisions(
    signals: pd.DataFrame,
    baseline_predictions: pd.DataFrame,
    config: dict,
    baselines: list[str],
    regime_policy: Any | None = None,
    current_holdings: set[str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    signal_df = signals.copy()
    signal_df["signal_date"] = pd.to_datetime(signal_df["signal_ts"]).dt.date
    signal_df["side_norm"] = signal_df["side"].fillna("").astype(str).str.upper()

    baseline_order = {model_name: idx for idx, model_name in enumerate(baselines)}
    baseline_by_symbol: dict[str, list[pd.Series]] = {}
    if not baseline_predictions.empty:
        sorted_predictions = baseline_predictions.assign(
            _baseline_order=baseline_predictions["model_name"].map(baseline_order).fillna(len(baseline_order)),
        ).sort_values(["symbol", "_baseline_order"], kind="mergesort")
        for _, row in sorted_predictions.iterrows():
            baseline_by_symbol.setdefault(str(row["symbol"]), []).append(row)

    held = current_holdings if current_holdings is not None else set()
    for _, row in signal_df.iterrows():
        rows.append(_base_decision(
            row,
            baseline_by_symbol.get(str(row["symbol"]), []),
            config,
            baselines,
            regime_policy=regime_policy,
            current_holdings=held,
        ))

    decisions = pd.DataFrame(rows)
    if decisions.empty:
        return decisions

    _reject_buys_when_symbol_has_sell(decisions)
    _keep_best_per_symbol_side(decisions)
    return decisions


def _base_decision(  # noqa: PLR0913
    signal: pd.Series,
    baseline_rows: list[pd.Series],
    config: dict,
    baselines: list[str],
    regime_policy: Any | None = None,
    current_holdings: set[str] | None = None,
) -> dict[str, Any]:
    side = str(signal.get("side_norm") or "").upper()
    model_name = str(signal.get("model_name") or "")
    score = _to_float(signal.get("score"))
    confidence = _to_float(signal.get("confidence"))
    signal_id = signal.get("signal_id")
    baseline_set = set(baselines)
    default_baseline_row = baseline_rows[0] if baseline_rows else None

    def common(qlib_row: pd.Series | None = default_baseline_row) -> dict[str, Any]:
        priority_score = _priority_score(model_name, side, confidence, score, qlib_row, baseline_set)
        return {
            "decision_id": f"DEC-{signal_id}-{ARBITER_VERSION}",
            "signal_id": signal_id,
            "decision_date": date.today(),
            "model_name": model_name,
            "model_version": signal.get("model_version"),
            "symbol": signal.get("symbol"),
            "side": side,
            "signal_ts": signal.get("signal_ts"),
            "arbiter_version": ARBITER_VERSION,
            "priority_score": priority_score,
            **_qlib_meta(qlib_row),
        }

    if side in {"SELL", "SHORT"}:
        # B2: 无持仓的 SELL 前置标记为 REJECTED("无需执行"),减少 paper_engine 噪音
        if current_holdings is not None and str(signal.get("symbol") or "") not in current_holdings:
            return {
                **common(),
                "decision": REJECTED,
                "decision_reason": "当前无持仓，SELL 无需执行（已在套利层前置过滤）",
                "consensus_status": "NO_HOLDING_SKIP",
            }
        return {
            **common(),
            "decision": ACCEPTED,
            "decision_reason": "SELL/风险释放优先通过统一仲裁",
            "consensus_status": "SELL",
        }
    if side != "BUY":
        return {
            **common(),
            "decision": REJECTED,
            "decision_reason": f"不支持的信号方向: {side or '-'}",
            "consensus_status": "UNSUPPORTED_SIDE",
        }

    regime_reject_reason = _regime_buy_reject_reason(regime_policy, confidence)
    if regime_reject_reason:
        return {
            **common(),
            "decision": REJECTED,
            "decision_reason": regime_reject_reason,
            "consensus_status": "MACRO_BLOCK",
        }

    portfolio_cfg = config.get("portfolio", {})
    # 跨模型 confidence 不可比：alpha158 生产模型的 confidence 是排名分位型分数
    # （均值约 0.67），用为规则策略校准的 0.75 门槛会过滤掉生产模型自己已精选的
    # top-N。baseline-self 信号的选择本身即共识，这里只保留一个低位安全门槛。
    if model_name in baseline_set:
        min_conf = float(portfolio_cfg.get("min_baseline_buy_confidence", 0.55) or 0.0)
        min_rank_score = float(portfolio_cfg.get("min_baseline_buy_rank_score", 0.30) or 0.0)
    else:
        min_conf = float(portfolio_cfg.get("min_rebalance_buy_confidence", 0.75) or 0.0)
        min_rank_score = float(portfolio_cfg.get("min_rebalance_buy_rank_score", 0.50) or 0.0)
    rank_score = confidence * max(score, 0.0)
    if confidence < min_conf or rank_score < min_rank_score:
        return {
            **common(),
            "decision": REJECTED,
            "decision_reason": f"低于全局BUY门槛: confidence={confidence:.2f}, rank_score={rank_score:.2f}",
            "consensus_status": "LOW_SIGNAL_SCORE",
        }

    if model_name in baseline_set:
        return {
            **common(),
            "decision": ACCEPTED,
            "decision_reason": f"{_baseline_label(model_name)} production BUY 通过统一仲裁",
            "consensus_status": "BASELINE_SELF",
        }

    if not baselines:
        return {
            **common(None),
            "decision": ACCEPTED,
            "decision_reason": "规则BUY通过统一仲裁；未配置共识基准",
            "consensus_status": "NO_BASELINE_REQUIRED",
        }

    consensus_row, reject_reason, consensus_status = _rule_buy_consensus(
        signal,
        baseline_rows,
        config,
        baselines,
    )
    if reject_reason:
        return {
            **common(consensus_row),
            "decision": REJECTED,
            "decision_reason": reject_reason,
            "consensus_status": consensus_status,
        }
    return {
        **common(consensus_row),
        "decision": ACCEPTED,
        "decision_reason": _accepted_rule_buy_reason(consensus_row),
        "consensus_status": _accepted_consensus_status(consensus_row),
    }


def _rule_buy_consensus(
    signal: pd.Series,
    baseline_rows: list[pd.Series],
    config: dict,
    baselines: list[str],
) -> tuple[pd.Series | None, str | None, str]:
    arbiter_cfg = config.get("portfolio", {}).get("signal_arbiter", {})
    if not baseline_rows:
        if bool(arbiter_cfg.get("block_when_missing", True)):
            baseline_text = ", ".join(baselines)
            return None, f"Qlib共识不可用: 无 {baseline_text} production 预测，规则BUY不进入执行", "MISSING"
        return None, None, "MISSING_ALLOWED"

    signal_date = _as_date(signal.get("signal_date"))
    max_stale_days = int(arbiter_cfg.get("max_prediction_stale_days", 3) or 0)
    max_rank = int(arbiter_cfg.get("max_rule_buy_rank", 500) or 500)
    min_confidence = float(arbiter_cfg.get("min_rule_buy_confidence", 0.45) or 0.0)
    freshest_stale_row: pd.Series | None = None
    first_divergent_row: pd.Series | None = None

    for baseline_row in baseline_rows:
        pred_date = _as_date(baseline_row.get("prediction_date"))
        stale_days = max((signal_date - pred_date).days, 0) if pred_date and signal_date else 9999
        if stale_days > max_stale_days:
            freshest_stale_row = baseline_row if freshest_stale_row is None else freshest_stale_row
            continue

        rank = _to_int(baseline_row.get("rank"))
        confidence = _to_float(baseline_row.get("confidence"))
        if rank is not None and rank <= max_rank and confidence >= min_confidence:
            return baseline_row, None, "CONSENSUS"
        first_divergent_row = baseline_row if first_divergent_row is None else first_divergent_row

    if first_divergent_row is not None:
        rank = _to_int(first_divergent_row.get("rank"))
        confidence = _to_float(first_divergent_row.get("confidence"))
        rank_text = "-" if rank is None else str(rank)
        label = _baseline_label(str(first_divergent_row.get("model_name") or "Qlib"))
        return (
            first_divergent_row,
            f"Qlib共识不足: {label} rank={rank_text} > {max_rank} 或 confidence={confidence:.2f} < {min_confidence:.2f}",
            "DIVERGENCE",
        )

    stale_row = freshest_stale_row or baseline_rows[0]
    pred_date = _as_date(stale_row.get("prediction_date"))
    stale_days = max((signal_date - pred_date).days, 0) if pred_date and signal_date else 9999
    label = _baseline_label(str(stale_row.get("model_name") or "Qlib"))
    return (
        stale_row,
        f"Qlib共识过期: {label} prediction_date={pred_date}, signal_date={signal_date}, stale_days={stale_days}",
        "STALE",
    )


def _accepted_rule_buy_reason(qlib_row: pd.Series | None) -> str:
    if qlib_row is None:
        return "规则BUY通过统一仲裁；Qlib缺失但配置允许"
    rank = _to_int(qlib_row.get("rank"))
    confidence = _to_float(qlib_row.get("confidence"))
    label = _baseline_label(str(qlib_row.get("model_name") or "Qlib"))
    return f"规则BUY通过统一仲裁；{label} rank={rank}, confidence={confidence:.2f}"


def _accepted_consensus_status(qlib_row: pd.Series | None) -> str:
    if qlib_row is None:
        return "MISSING_ALLOWED"
    rank = _to_int(qlib_row.get("rank")) or 999999
    return "CONSENSUS" if rank <= 100 else "NEUTRAL"


def _regime_buy_reject_reason(regime_policy: Any | None, confidence: float) -> str | None:
    if regime_policy is None:
        return None
    regime_state = str(getattr(regime_policy, "regime_state", "unknown") or "unknown")
    action_hint = str(getattr(regime_policy, "action_hint", "") or "")
    if not bool(getattr(regime_policy, "allow_new_buys", False)):
        return f"宏观风控暂停BUY: {regime_state}; {action_hint}"
    min_confidence = float(getattr(regime_policy, "min_buy_confidence", 0.0) or 0.0)
    if confidence < min_confidence:
        return (
            f"宏观风控提高BUY门槛: {regime_state}; "
            f"confidence={confidence:.2f} < {min_confidence:.2f}; {action_hint}"
        )
    return None


def _baseline_label(model_name: str) -> str:
    return "Alpha158" if model_name == "alpha158" else model_name


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


def _priority_score(
    model_name: str,
    side: str,
    confidence: float,
    score: float,
    qlib_row: pd.Series | None,
    baseline_set: set[str] | None = None,
) -> float:
    if side in {"SELL", "SHORT"}:
        return 2.0 + confidence
    model_bonus = 0.2 if model_name in (baseline_set or {"alpha158"}) else 0.0
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
