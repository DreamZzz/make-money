"""Dashboard-facing aggregation for realized signal outcomes."""
from __future__ import annotations

from typing import Any

import pandas as pd

SUMMARY_COLUMNS = [
    "model_name",
    "strategy_label",
    "strategy_logic",
    "online_scope",
    "trading_role",
    "horizon_days",
    "horizon_label",
    "horizon_meaning",
    "sample_count",
    "pending_count",
    "hit_count",
    "hit_rate",
    "avg_return",
    "avg_alpha_vs_benchmark",
    "median_return",
]
MONTHLY_COLUMNS = [
    "model_name",
    "strategy_label",
    "strategy_logic",
    "online_scope",
    "trading_role",
    "execution_month",
    "horizon_days",
    "horizon_label",
    "horizon_meaning",
    "sample_count",
    "pending_count",
    "hit_count",
    "hit_rate",
    "avg_return",
    "avg_alpha_vs_benchmark",
]
DETAIL_COLUMNS = [
    "signal_id",
    "model_name",
    "model_version",
    "symbol",
    "stock_name",
    "side",
    "horizon_days",
    "signal_date",
    "execution_date",
    "execution_price",
    "outcome_date",
    "outcome_price",
    "return_pct",
    "benchmark_code",
    "benchmark_return_pct",
    "alpha_vs_benchmark",
    "status",
]

STRATEGY_CATALOG: dict[str, dict[str, str]] = {
    "alpha158": {
        "strategy_label": "Alpha158 多因子",
        "strategy_logic": "158 个价量因子 + LightGBM 排序选股；买入排名靠前标的，跌出持仓阈值时卖出。",
        "online_scope": "线上生产模型",
        "trading_role": "会产生 BUY/SELL；依赖 production 预测成功",
    },
    "trend_following": {
        "strategy_label": "趋势跟踪",
        "strategy_logic": "均线趋势 + 通道突破；顺势买入强势标的，趋势破坏时卖出。",
        "online_scope": "线上规则策略",
        "trading_role": "会产生 BUY/SELL",
    },
    "trend": {
        "strategy_label": "趋势跟踪",
        "strategy_logic": "均线趋势 + 通道突破；顺势买入强势标的，趋势破坏时卖出。",
        "online_scope": "线上规则策略",
        "trading_role": "会产生 BUY/SELL",
    },
    "mean_reversion": {
        "strategy_label": "均值回归",
        "strategy_logic": "RSI + 布林带位置；短期超跌时买入，反弹或过热时卖出。",
        "online_scope": "线上规则策略",
        "trading_role": "会产生 BUY/SELL",
    },
    "industry_rotation": {
        "strategy_label": "行业轮动",
        "strategy_logic": "按行业近期动量排序，优先选择强势行业内标的。",
        "online_scope": "线上规则策略",
        "trading_role": "当前主要产生 BUY 候选",
    },
    "value_quality": {
        "strategy_label": "价值质量",
        "strategy_logic": "低估值 + 盈利质量 + 成长稳定性打分；当前作为研究专项观察。",
        "online_scope": "研究观察中",
        "trading_role": "暂不进入主交易流",
    },
}


def load_signal_outcome_snapshot(conn: Any, limit: int = 200) -> dict[str, pd.DataFrame]:
    outcomes = _load_outcome_detail(conn)
    if outcomes.empty:
        return {
            "summary": pd.DataFrame(columns=SUMMARY_COLUMNS),
            "monthly": pd.DataFrame(columns=MONTHLY_COLUMNS),
            "detail": outcomes,
        }
    return {
        "summary": _aggregate_summary(outcomes),
        "monthly": _aggregate_monthly(outcomes),
        "detail": outcomes.head(int(limit)).copy(),
    }


def _load_outcome_detail(conn: Any) -> pd.DataFrame:
    df = conn.execute("""
        SELECT
            so.signal_id,
            so.model_name,
            so.model_version,
            so.symbol,
            COALESCE(si.name, so.symbol) AS stock_name,
            so.side,
            so.horizon_days,
            so.signal_date,
            so.execution_date,
            so.execution_price,
            so.outcome_date,
            so.outcome_price,
            so.return_pct,
            so.benchmark_code,
            so.benchmark_return_pct,
            so.alpha_vs_benchmark,
            so.status
        FROM signal_outcomes so
        LEFT JOIN stock_info si ON so.symbol = si.symbol
        ORDER BY so.execution_date DESC, so.signal_id DESC, so.horizon_days
    """).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    df["model_name"] = df["model_name"].fillna("unknown")
    df["status"] = df["status"].fillna("PENDING").str.upper()
    df["horizon_days"] = pd.to_numeric(df["horizon_days"], errors="coerce").fillna(0).astype(int)
    df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce")
    df["benchmark_return_pct"] = pd.to_numeric(df["benchmark_return_pct"], errors="coerce")
    df["alpha_vs_benchmark"] = pd.to_numeric(df["alpha_vs_benchmark"], errors="coerce")
    df["execution_date"] = pd.to_datetime(df["execution_date"]).dt.date
    df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.date
    df["outcome_date"] = pd.to_datetime(df["outcome_date"], errors="coerce").dt.date
    return df[DETAIL_COLUMNS]


def _aggregate_summary(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (model_name, horizon_days), group in detail.groupby(["model_name", "horizon_days"], dropna=False):
        rows.append(_aggregate_group(group, {
            "model_name": model_name,
            **_strategy_metadata(str(model_name)),
            "horizon_days": int(horizon_days),
            **_horizon_metadata(int(horizon_days)),
        }, include_median=True))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values(
        ["model_name", "horizon_days"]
    ).reset_index(drop=True)


def _aggregate_monthly(detail: pd.DataFrame) -> pd.DataFrame:
    df = detail.copy()
    df["execution_month"] = pd.to_datetime(df["execution_date"]).dt.to_period("M").dt.to_timestamp().dt.date
    rows = []
    for (model_name, execution_month, horizon_days), group in df.groupby(
        ["model_name", "execution_month", "horizon_days"],
        dropna=False,
    ):
        rows.append(_aggregate_group(group, {
            "model_name": model_name,
            **_strategy_metadata(str(model_name)),
            "execution_month": execution_month,
            "horizon_days": int(horizon_days),
            **_horizon_metadata(int(horizon_days)),
        }, include_median=False))
    return pd.DataFrame(rows, columns=MONTHLY_COLUMNS).sort_values(
        ["execution_month", "model_name", "horizon_days"],
        ascending=[False, True, True],
    ).reset_index(drop=True)


def _aggregate_group(group: pd.DataFrame, keys: dict[str, Any], include_median: bool) -> dict[str, Any]:
    ready = group[group["status"] == "READY"].copy()
    returns = pd.to_numeric(ready["return_pct"], errors="coerce").dropna()
    alpha = pd.to_numeric(ready["alpha_vs_benchmark"], errors="coerce").dropna()
    sample_count = int(len(returns))
    hit_count = int((returns > 0).sum())
    row = {
        **keys,
        "sample_count": sample_count,
        "pending_count": int((group["status"] == "PENDING").sum()),
        "hit_count": hit_count,
        "hit_rate": float(hit_count / sample_count) if sample_count else 0.0,
        "avg_return": float(returns.mean()) if sample_count else 0.0,
        "avg_alpha_vs_benchmark": float(alpha.mean()) if len(alpha) else 0.0,
    }
    if include_median:
        row["median_return"] = float(returns.median()) if sample_count else 0.0
    return row


def _strategy_metadata(model_name: str) -> dict[str, str]:
    return STRATEGY_CATALOG.get(model_name, {
        "strategy_label": model_name or "未知策略",
        "strategy_logic": "未登记策略说明；请补充策略目录。",
        "online_scope": "未知",
        "trading_role": "需人工确认是否进入交易流",
    })


def _horizon_metadata(horizon_days: int) -> dict[str, str]:
    label = f"T+{horizon_days}"
    return {
        "horizon_label": label,
        "horizon_meaning": f"成交后 {horizon_days} 个交易日的信号效果跟踪；这是收益观察窗口，不是另一个模型。",
    }
