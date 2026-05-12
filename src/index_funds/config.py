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

