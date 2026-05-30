"""Configuration helpers for the independent index-fund module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FundWatchItem:
    fund_code: str
    name: str
    fund_type: str
    tracking_index: str
    tracking_index_name: str
    market: str
    currency: str
    target_weight: float
    enabled: bool = True
    # E1: 基金分类,决定评估策略
    # equity_index = 纯权益指数基金/ETF,适用 price_pct/MA/M4 RS 轮动
    # balanced    = 股债混合,不适用纯权益评估;tracking_index 仅作参考
    # qdii        = 海外指数(港股/美股),适用权益评估但 stale 容忍度更高
    # bond / other
    category: str = "equity_index"
    # E1: 用户意图
    # active   = 系统主动驱动调仓 (进 M4 RS 池)
    # exited   = 已清仓,残留仓位不再主动管理 (不进 M4 / 不算 delta_amount)
    # watching = 仅观察,不进 M4
    intent: str = "active"


def load_index_fund_config() -> dict[str, Any]:
    from src.config import load_config

    return load_config().get("index_funds", {})


def get_rules(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_index_fund_config()
    return cfg.get("rules", {})


def get_watchlist(config: dict[str, Any] | None = None, active_only: bool = True) -> list[FundWatchItem]:
    cfg = config if config is not None else load_index_fund_config()
    items = []
    for raw in cfg.get("watchlist", []):
        enabled = bool(raw.get("enabled", True))
        if active_only and not enabled:
            continue
        tracking_index = str(raw.get("tracking_index") or "").strip()
        if not tracking_index:
            continue
        target_weight = float(raw.get("target_weight") or 0.0)
        items.append(
            FundWatchItem(
                fund_code=str(raw.get("fund_code") or "").strip(),
                name=str(raw.get("name") or raw.get("fund_code") or tracking_index),
                fund_type=str(raw.get("fund_type") or "ETF").upper(),
                tracking_index=tracking_index,
                tracking_index_name=str(raw.get("tracking_index_name") or tracking_index),
                market=str(raw.get("market") or "CN"),
                currency=str(raw.get("currency") or "CNY"),
                target_weight=max(target_weight, 0.0),
                enabled=enabled,
                category=str(raw.get("category") or "equity_index").lower(),
                intent=str(raw.get("intent") or "active").lower(),
            )
        )
    return items


def watchlist_to_frame(items: list[FundWatchItem]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(
            columns=[
                "fund_code",
                "name",
                "fund_type",
                "tracking_index",
                "tracking_index_name",
                "market",
                "currency",
                "target_weight",
                "enabled",
            ]
        )
    return pd.DataFrame([item.__dict__ for item in items])

