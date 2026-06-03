"""R2: value_quality 信号生成 + 落 signals 表(研究观察用)。

调 src/research/strategies/value_quality.py 的 compute_value_quality_scores +
generate_signals → 写 signals 表。confidence 默认 0.5-0.65,自然被现有
min_rebalance_buy_confidence=0.75 门槛拦掉,符合"研究观察不进 BUY 执行"。
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import duckdb
import pandas as pd
from loguru import logger

from src.financials.universe import load_earnings_universe
from src.research.strategies.value_quality import (
    compute_value_quality_scores,
    generate_signals,
    load_fundamentals_snapshot,
)

MODEL_NAME_FILTER = "value_quality"
DEFAULT_TOP_N = 20
DEFAULT_MIN_SCORE = 0.60
DEFAULT_HOLDING_DAYS = 20


def generate_and_persist_value_quality_signals(
    conn: duckdb.DuckDBPyConnection,
    *,
    top_n: int = DEFAULT_TOP_N,
    min_score: float = DEFAULT_MIN_SCORE,
    holding_days: int = DEFAULT_HOLDING_DAYS,
    as_of: date | None = None,
    universe_filter: bool = True,
) -> int:
    """生成 value_quality 信号 + 落 signals 表。返回新写入条数。"""
    snapshot = load_fundamentals_snapshot(conn, as_of=as_of)
    if snapshot.empty:
        logger.info("value_quality: fundamentals_snapshot 为空,跳过")
        return 0

    if universe_filter:
        universe = set(load_earnings_universe(conn))
        if universe:
            snapshot = snapshot[snapshot["symbol"].isin(universe)]
            if snapshot.empty:
                logger.info("value_quality: universe 内无 fundamental 覆盖,跳过")
                return 0

    scored = compute_value_quality_scores(snapshot)
    if scored.empty:
        logger.info("value_quality: 评分为空,跳过")
        return 0

    signals_df = generate_signals(
        scored, top_n=top_n, min_score=min_score,
        expected_holding_days=holding_days,
    )
    if signals_df.empty:
        logger.info("value_quality: 生成 signals 为空,跳过")
        return 0

    # 同一天同模型同 symbol 去重,避免重复写
    today_d = as_of or date.today()
    existing = conn.execute(
        "SELECT symbol FROM signals WHERE model_name = ? "
        "AND DATE(signal_ts) = ?",
        [MODEL_NAME_FILTER, today_d],
    ).fetchall()
    existing_set = {r[0] for r in existing}
    signals_df = signals_df[~signals_df["symbol"].isin(existing_set)]
    if signals_df.empty:
        return 0

    # 转 signals 表行
    rows = []
    for r in signals_df.itertuples(index=False):
        rows.append({
            "signal_id": f"VQ-{uuid.uuid4().hex[:12].upper()}",
            "model_name": str(r.model_name),
            "model_version": str(r.model_version),
            "symbol": str(r.symbol),
            "signal_ts": pd.Timestamp(r.signal_ts),
            "horizon": str(r.horizon),
            "score": float(r.score),
            "side": str(r.side),
            "confidence": float(r.confidence),
            "expected_holding_days": int(r.expected_holding_days),
            "max_position_pct": float(r.max_position_pct),
            "thesis": str(r.thesis),
            "risk_tags": list(r.risk_tags),
            "executed": False,
            "status": "ACTIVE",
            "expires_at": today_d + timedelta(days=holding_days),
        })
    df = pd.DataFrame(rows)  # noqa: F841 - DuckDB 引用
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_vq AS SELECT * FROM df")
    conn.execute(
        """
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts, horizon,
            score, side, confidence, expected_holding_days, max_position_pct,
            thesis, risk_tags, executed, status, expires_at
        )
        SELECT signal_id, model_name, model_version, symbol, signal_ts, horizon,
               score, side, confidence, expected_holding_days, max_position_pct,
               thesis, risk_tags, executed, status, expires_at
        FROM _tmp_vq
        """
    )
    return len(df)
