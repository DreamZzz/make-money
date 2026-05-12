"""Medium/long-term index fund signal generation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import click
import numpy as np
import pandas as pd
from loguru import logger

from src.index_funds.config import FundWatchItem, get_rules, get_watchlist


ACTION_LABELS = {
    "BUY": "买入",
    "ADD": "加仓",
    "HOLD": "持有",
    "REDUCE": "减仓",
    "PAUSE": "暂停定投",
}


@dataclass(frozen=True)
class IndexFundSignal:
    fund_code: str
    index_code: str
    signal_date: date
    action: str
    target_weight: float
    confidence: float
    thesis: str
    risk_tags: list[str]


def calculate_signal(
    item: FundWatchItem,
    index_df: pd.DataFrame,
    rules: dict,
    current_weight: float | None = None,
) -> IndexFundSignal | None:
    """Create one independent index-fund signal from index trend/percentile and allocation drift."""
    if index_df.empty or "close" not in index_df.columns:
        return None

    state = compute_index_state(index_df, rules)
    if not state:
        return None

    low_p = float(rules.get("low_valuation_percentile", 0.30))
    high_p = float(rules.get("high_valuation_percentile", 0.80))
    threshold = float(rules.get("rebalance_threshold_pct", 0.05))
    min_conf = float(rules.get("min_confidence", 0.45))
    target_weight = float(item.target_weight or 0.0)
    weight = current_weight if current_weight is not None else 0.0
    underweight = target_weight > 0 and weight < target_weight - threshold
    overweight = target_weight > 0 and weight > target_weight + threshold

    percentile = state["price_percentile"]
    trend_weak = state["trend_weak"]
    trend_healthy = state["trend_healthy"]
    action = "HOLD"
    reasons = [
        f"价格分位 {percentile:.0%}",
        f"MA{state['fast_window']}={state['ma_fast']:.2f}",
        f"MA{state['slow_window']}={state['ma_slow']:.2f}",
    ]
    risk_tags = []

    if overweight and (percentile >= high_p or trend_weak):
        action = "REDUCE"
        reasons.append(f"当前权重 {weight:.1%} 高于目标 {target_weight:.1%}")
        risk_tags.append("overweight")
    elif percentile >= high_p:
        action = "PAUSE"
        reasons.append("估值/价格处于高分位，暂停新增")
        risk_tags.append("high_percentile")
    elif trend_weak:
        action = "PAUSE"
        reasons.append("中长期趋势转弱，暂停新增")
        risk_tags.append("trend_weak")
    elif percentile <= low_p and trend_healthy:
        action = "BUY" if weight <= 0 else "ADD"
        reasons.append("低分位且趋势未破坏，适合分批投入")
        risk_tags.append("low_percentile")
    elif underweight and trend_healthy:
        action = "BUY" if weight <= 0 else "ADD"
        reasons.append(f"当前权重 {weight:.1%} 低于目标 {target_weight:.1%}")
        risk_tags.append("underweight")
    else:
        reasons.append("估值、趋势和仓位偏离均未触发动作")

    confidence = signal_confidence(action, percentile, low_p, high_p, trend_weak, underweight, overweight)
    confidence = max(min_conf, confidence) if action != "HOLD" else min(confidence, 0.65)
    thesis = "；".join(reasons)

    return IndexFundSignal(
        fund_code=item.fund_code,
        index_code=item.tracking_index,
        signal_date=state["trade_date"],
        action=action,
        target_weight=target_weight,
        confidence=float(np.clip(confidence, 0.0, 0.95)),
        thesis=thesis,
        risk_tags=risk_tags or ["normal"],
    )


def compute_index_state(index_df: pd.DataFrame, rules: dict) -> dict:
    df = index_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    slow = int(rules.get("trend_ma_slow", 250))
    fast = int(rules.get("trend_ma_fast", 120))
    window_days = int(rules.get("valuation_window_days", 756))
    if len(df) < max(20, min(slow, 60)):
        return {}

    window = df.tail(max(window_days, slow))
    latest = window.iloc[-1]
    closes = window["close"].tail(window_days)
    price_percentile = float((closes <= latest["close"]).mean())
    fast_min_periods = min(fast, max(20, fast // 2))
    slow_min_periods = min(slow, max(40, slow // 2))
    ma_fast = float(window["close"].rolling(fast, min_periods=fast_min_periods).mean().iloc[-1])
    ma_slow = float(window["close"].rolling(slow, min_periods=slow_min_periods).mean().iloc[-1])
    if np.isnan(ma_fast) or np.isnan(ma_slow):
        return {}

    close = float(latest["close"])
    return {
        "trade_date": latest["trade_date"].date(),
        "close": close,
        "price_percentile": price_percentile,
        "ma_fast": ma_fast,
        "ma_slow": ma_slow,
        "fast_window": fast,
        "slow_window": slow,
        "trend_healthy": close >= ma_slow and ma_fast >= ma_slow * 0.98,
        "trend_weak": close < ma_slow or ma_fast < ma_slow,
    }


def signal_confidence(
    action: str,
    percentile: float,
    low_p: float,
    high_p: float,
    trend_weak: bool,
    underweight: bool,
    overweight: bool,
) -> float:
    if action in {"BUY", "ADD"}:
        valuation_strength = max(0.0, low_p - percentile) / max(low_p, 1e-9)
        allocation_bonus = 0.15 if underweight else 0.0
        return 0.55 + 0.25 * valuation_strength + allocation_bonus
    if action in {"REDUCE", "PAUSE"}:
        high_strength = max(0.0, percentile - high_p) / max(1 - high_p, 1e-9)
        trend_bonus = 0.15 if trend_weak else 0.0
        allocation_bonus = 0.10 if overweight else 0.0
        return 0.55 + 0.20 * high_strength + trend_bonus + allocation_bonus
    return 0.50


def signals_to_frame(signals: list[IndexFundSignal]) -> pd.DataFrame:
    rows = []
    for sig in signals:
        rows.append({
            "signal_id": f"IFSIG-{uuid.uuid4().hex[:10].upper()}",
            "fund_code": sig.fund_code,
            "index_code": sig.index_code,
            "signal_date": sig.signal_date,
            "action": sig.action,
            "target_weight": sig.target_weight,
            "confidence": sig.confidence,
            "thesis": sig.thesis,
            "risk_tags": sig.risk_tags,
        })
    return pd.DataFrame(rows)


def generate_signals(persist: bool = True) -> pd.DataFrame:
    """Generate current index fund signals from configured watchlist."""
    from src.data_pipeline.loader import get_connection, init_db
    from src.index_funds.performance import load_current_weights

    rules = get_rules()
    watchlist = get_watchlist()
    if not watchlist:
        return pd.DataFrame()

    conn = get_connection()
    try:
        init_db(conn)
        weights = load_current_weights(conn)
        signals = []
        for item in watchlist:
            idx_df = conn.execute("""
                SELECT trade_date, close
                FROM index_daily
                WHERE index_code = ?
                ORDER BY trade_date
            """, [item.tracking_index]).fetchdf()
            current_weight = weights.get(item.fund_code, 0.0) if item.fund_code else 0.0
            signal = calculate_signal(item, idx_df, rules, current_weight=current_weight)
            if signal:
                signals.append(signal)
        df = signals_to_frame(signals)
        if persist and not df.empty:
            latest_dates = df["signal_date"].dropna().unique().tolist()
            for signal_date in latest_dates:
                conn.execute("DELETE FROM index_fund_signals WHERE signal_date = ?", [signal_date])
            conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_index_fund_signals AS SELECT * FROM df")
            conn.execute("""
                INSERT INTO index_fund_signals (
                    signal_id, fund_code, index_code, signal_date, action,
                    target_weight, confidence, thesis, risk_tags
                )
                SELECT signal_id, fund_code, index_code, signal_date, action,
                       target_weight, confidence, thesis, risk_tags
                FROM _tmp_index_fund_signals
            """)
            logger.info(f"Generated {len(df)} index fund signals")
        return df
    finally:
        conn.close()


@click.group()
def cli():
    pass


@cli.command()
def generate():
    result = generate_signals(persist=True)
    if result.empty:
        click.echo("No index fund signals generated")
    else:
        click.echo(result[["signal_date", "fund_code", "index_code", "action", "confidence", "thesis"]].to_string(index=False))


if __name__ == "__main__":
    cli()
