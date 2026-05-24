"""虚拟账户配置模型。

一个 AccountConfig 描述一套完整可部署配置：订阅哪些模型、用什么基准对标、
以及覆盖在基础应用 config 上的 portfolio.* 参数（套利门槛、配比、风险档等）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.config import _deep_merge, load_config


@dataclass(frozen=True)
class AccountConfig:
    # 订阅的模型集（signal.model_name 过滤）；空 = 订阅全部模型
    models: tuple[str, ...] = ()
    # 对标基准指数
    benchmark_index: str = "000300"
    # 覆盖到基础 config["portfolio"] 上的部分配置（深合并）
    portfolio_overrides: dict[str, Any] = field(default_factory=dict)

    def subscribes(self, model_name: str) -> bool:
        """该账户是否订阅此模型的信号。空订阅集表示接收全部。"""
        if not self.models:
            return True
        return str(model_name) in self.models

    def effective_config(self, base_config: dict[str, Any] | None = None) -> dict[str, Any]:
        """把账户的 portfolio 覆盖深合并到基础 config，得到该账户的有效配置。"""
        base = base_config if base_config is not None else load_config()
        if not self.portfolio_overrides:
            return _deep_merge(base, {})
        return _deep_merge(base, {"portfolio": self.portfolio_overrides})

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": list(self.models),
            "benchmark_index": self.benchmark_index,
            "portfolio": dict(self.portfolio_overrides),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AccountConfig:
        data = data or {}
        models = tuple(str(m) for m in (data.get("models") or []))
        benchmark = str(data.get("benchmark_index") or "000300")
        overrides = data.get("portfolio") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        return cls(models=models, benchmark_index=benchmark, portfolio_overrides=dict(overrides))

    @classmethod
    def from_json(cls, payload: str | None) -> AccountConfig:
        if not payload:
            return cls()
        return cls.from_dict(json.loads(payload))
