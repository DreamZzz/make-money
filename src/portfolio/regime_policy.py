"""Regime-level portfolio policy for independent macro risk control."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from src.portfolio.allocator import AllocationConfig
from src.research.market_regime import latest_market_regime

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "risk_on": {
        "core_target_pct": 0.50,
        "satellite_target_pct": 0.45,
        "cash_target_pct": 0.05,
        "buy_policy": "normal",
        "allow_new_buys": True,
        "min_buy_confidence": 0.75,
        "action_hint": "正常执行BUY，保持纪律调仓",
    },
    "neutral": {
        "core_target_pct": 0.60,
        "satellite_target_pct": 0.30,
        "cash_target_pct": 0.10,
        "buy_policy": "high_confidence_only",
        "allow_new_buys": True,
        "min_buy_confidence": 0.85,
        "action_hint": "只执行高置信度BUY，保留现金缓冲",
    },
    "defensive": {
        "core_target_pct": 0.70,
        "satellite_target_pct": 0.15,
        "cash_target_pct": 0.15,
        "buy_policy": "restricted",
        "allow_new_buys": True,
        "min_buy_confidence": 0.90,
        "action_hint": "限制新增BUY，优先降低高风险暴露",
    },
    "risk_off": {
        "core_target_pct": 0.70,
        "satellite_target_pct": 0.10,
        "cash_target_pct": 0.20,
        "buy_policy": "sell_only",
        "allow_new_buys": False,
        "min_buy_confidence": 1.0,
        "action_hint": "暂停新增BUY，只允许SELL/减仓和风险释放",
    },
    "crisis": {
        "core_target_pct": 0.50,
        "satellite_target_pct": 0.00,
        "cash_target_pct": 0.50,
        "buy_policy": "sell_only",
        "allow_new_buys": False,
        "min_buy_confidence": 1.0,
        "action_hint": "停止新增BUY，进入危机降仓与现金保护",
    },
    "unknown": {
        "core_target_pct": 0.60,
        "satellite_target_pct": 0.30,
        "cash_target_pct": 0.10,
        "buy_policy": "data_blocked",
        "allow_new_buys": False,
        "min_buy_confidence": 1.0,
        "action_hint": "数据不足，暂停新增BUY并等待下一次收盘确认",
    },
}


@dataclass(frozen=True)
class RegimePolicyConfig:
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def profile_for(self, regime_state: str) -> dict[str, Any]:
        profile = DEFAULT_PROFILES.get(regime_state, DEFAULT_PROFILES["unknown"]).copy()
        profile.update(self.profiles.get(regime_state, {}))
        return _normalize_profile(profile)


@dataclass(frozen=True)
class RegimePolicyDecision:
    regime_state: str
    core_target_pct: float
    satellite_target_pct: float
    cash_target_pct: float
    buy_policy: str
    allow_new_buys: bool
    min_buy_confidence: float
    action_hint: str
    reason: str
    source_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime_state": self.regime_state,
            "core_target_pct": self.core_target_pct,
            "satellite_target_pct": self.satellite_target_pct,
            "cash_target_pct": self.cash_target_pct,
            "buy_policy": self.buy_policy,
            "allow_new_buys": self.allow_new_buys,
            "min_buy_confidence": self.min_buy_confidence,
            "action_hint": self.action_hint,
            "reason": self.reason,
            "source_state": self.source_state,
        }


def derive_regime_policy(
    latest_regime: dict[str, Any] | None,
    config: RegimePolicyConfig | None = None,
) -> RegimePolicyDecision:
    """Convert a latest market-regime snapshot into account-level policy."""
    source = dict(latest_regime or {})
    regime_state = _canonical_state(str(source.get("risk_state") or "unknown"))
    profile = (config or RegimePolicyConfig()).profile_for(regime_state)
    reason = str(source.get("reason") or f"{regime_state} regime policy")
    return RegimePolicyDecision(
        regime_state=regime_state,
        core_target_pct=float(profile["core_target_pct"]),
        satellite_target_pct=float(profile["satellite_target_pct"]),
        cash_target_pct=float(profile["cash_target_pct"]),
        buy_policy=str(profile["buy_policy"]),
        allow_new_buys=bool(profile["allow_new_buys"]),
        min_buy_confidence=float(profile["min_buy_confidence"]),
        action_hint=str(profile["action_hint"]),
        reason=f"{reason}; {profile['action_hint']}",
        source_state=source,
    )


def load_latest_regime_policy(
    conn: Any,
    as_of: date | None = None,
    config: dict[str, Any] | None = None,
) -> RegimePolicyDecision | None:
    """Load the latest benchmark regime from DuckDB and convert it to policy.

    The layer is opt-in. When ``portfolio.regime_policy.enabled`` is false or
    absent, callers get ``None`` and existing production behavior is unchanged.
    """
    policy_cfg = (config or {}).get("portfolio", {}).get("regime_policy", {})
    if not bool(policy_cfg.get("enabled", False)):
        return None

    benchmark = str(policy_cfg.get("benchmark_index") or "000300")
    lookback_days = int(policy_cfg.get("lookback_days", 260) or 260)
    if as_of is None and lookback_days > 0:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM index_daily WHERE index_code = ?",
            [benchmark],
        ).fetchone()
        as_of = row[0] if row and row[0] else None
    params: list[Any] = [benchmark]
    date_filter = ""
    if as_of is not None:
        date_filter = "AND trade_date <= ?"
        params.append(as_of)
    start_filter = ""
    if as_of is not None and lookback_days > 0:
        start_filter = "AND trade_date >= ?"
        params.append(as_of - timedelta(days=lookback_days * 2))

    df = conn.execute(
        f"""
        SELECT index_code, trade_date, close
        FROM index_daily
        WHERE index_code = ?
          {date_filter}
          {start_filter}
        ORDER BY trade_date
        """,
        params,
    ).fetchdf()
    if df.empty:
        snapshot = latest_market_regime(pd.DataFrame(columns=["index_code", "trade_date", "close"]), benchmark=benchmark)
    else:
        snapshot = latest_market_regime(
            df,
            benchmark=benchmark,
            fast_window=int(policy_cfg.get("fast_window", 20) or 20),
            slow_window=int(policy_cfg.get("slow_window", 120) or 120),
            vol_window=int(policy_cfg.get("vol_window", 20) or 20),
            drawdown_window=int(policy_cfg.get("drawdown_window", 120) or 120),
        )
    profile_config = RegimePolicyConfig(profiles=dict(policy_cfg.get("profiles") or {}))
    return derive_regime_policy(snapshot, config=profile_config)


def apply_regime_policy_to_allocation_config(
    base: AllocationConfig,
    decision: RegimePolicyDecision,
) -> AllocationConfig:
    """Apply policy targets to an allocation config without mutating other knobs."""
    return AllocationConfig(
        enabled=base.enabled,
        core_target_pct=decision.core_target_pct,
        satellite_target_pct=decision.satellite_target_pct,
        cash_target_pct=decision.cash_target_pct,
        rebalance_tolerance_pct=base.rebalance_tolerance_pct,
        min_trade_amount=base.min_trade_amount,
        core_cash_priority=True,
    ).normalized()


def _canonical_state(value: str) -> str:
    state = value.lower().replace("-", "_")
    if state in DEFAULT_PROFILES:
        return state
    return "unknown"


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    core = max(float(profile.get("core_target_pct", 0.0) or 0.0), 0.0)
    satellite = max(float(profile.get("satellite_target_pct", 0.0) or 0.0), 0.0)
    cash = max(float(profile.get("cash_target_pct", 0.0) or 0.0), 0.0)
    total = core + satellite + cash
    if total <= 0:
        core, satellite, cash = 0.60, 0.30, 0.10
    elif abs(total - 1.0) > 1e-9:
        core, satellite, cash = core / total, satellite / total, cash / total
    out = profile.copy()
    out["core_target_pct"] = core
    out["satellite_target_pct"] = satellite
    out["cash_target_pct"] = cash
    out["buy_policy"] = str(out.get("buy_policy") or "data_blocked")
    out["allow_new_buys"] = bool(out.get("allow_new_buys", False))
    out["min_buy_confidence"] = float(out.get("min_buy_confidence", 1.0))
    out["action_hint"] = str(out.get("action_hint") or "等待宏观状态确认")
    return out
