"""晋级闸门：选最优账户指导实盘，含选择偏差守卫。

并行账户只缩短"相对排名"时间，不缩短"统计验证"时间：N 选 1 的冠军大概率偏向
最走运者。所以冠军除了独立通过门槛，还需明显优于亚军（超额高出一个缓冲），
否则只标记为"领先候选"而非"可指导实盘"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from src.accounts.leaderboard import AccountMetrics, compute_account_metrics
from src.accounts.registry import list_accounts, mark_real_candidate, set_status


@dataclass(frozen=True)
class PromotionThresholds:
    min_sample_days: int = 252          # 至少约 1 年可比战绩
    min_closed_trades: int = 100        # go-live：>=100 已结算交易（样本充分）
    min_excess_return: float = 0.0      # 年化超额为正
    min_sharpe: float = 0.5
    max_drawdown_floor: float = -0.30   # 回撤不差于 -30%
    min_info_ratio: float = 0.3
    selection_bias_excess_buffer: float = 0.03  # 冠军超额需比亚军高出 >=3pp


@dataclass
class PromotionEvaluation:
    account_id: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "eligible": self.eligible,
                "reasons": self.reasons, "metrics": self.metrics}


def evaluate_account_promotion(
    metrics: AccountMetrics,
    thresholds: PromotionThresholds | None = None,
) -> PromotionEvaluation:
    t = thresholds or PromotionThresholds()
    reasons: list[str] = []
    if metrics.sample_days < t.min_sample_days:
        reasons.append(f"样本天数 {metrics.sample_days} < {t.min_sample_days}")
    if metrics.ready_outcomes < t.min_closed_trades:
        reasons.append(f"已结算交易 {metrics.ready_outcomes} < {t.min_closed_trades}")
    if metrics.excess_return is None or metrics.excess_return < t.min_excess_return:
        reasons.append(f"年化超额 {_fmt(metrics.excess_return)} < {t.min_excess_return:.2%}")
    if metrics.sharpe_ratio < t.min_sharpe:
        reasons.append(f"Sharpe {metrics.sharpe_ratio:.2f} < {t.min_sharpe:.2f}")
    if metrics.max_drawdown < t.max_drawdown_floor:
        reasons.append(f"最大回撤 {metrics.max_drawdown:.2%} 差于 {t.max_drawdown_floor:.2%}")
    if metrics.info_ratio is None or metrics.info_ratio < t.min_info_ratio:
        reasons.append(f"信息比率 {_fmt(metrics.info_ratio)} < {t.min_info_ratio:.2f}")
    return PromotionEvaluation(metrics.account_id, not reasons, reasons, metrics.to_dict())


def evaluate_tournament(
    conn: duckdb.DuckDBPyConnection,
    window_label: str = "replay",
    thresholds: PromotionThresholds | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """评估全部账户，返回排名、合格者与（经选择偏差守卫的）推荐冠军。"""
    t = thresholds or PromotionThresholds()
    evals: list[PromotionEvaluation] = []
    for account in list_accounts(conn, status=status):
        m = compute_account_metrics(conn, account, window_label=window_label)
        if m is None:
            evals.append(PromotionEvaluation(account.account_id, False, ["无回放/NAV数据"]))
            continue
        evals.append(evaluate_account_promotion(m, t))

    eligible = [e for e in evals if e.eligible]
    eligible.sort(key=lambda e: e.metrics.get("excess_return") or -1e9, reverse=True)

    winner = None
    selection_note = ""
    if len(eligible) == 1:
        winner = eligible[0].account_id
        selection_note = "唯一合格账户"
    elif len(eligible) >= 2:
        top, second = eligible[0], eligible[1]
        gap = (top.metrics.get("excess_return") or 0) - (second.metrics.get("excess_return") or 0)
        if gap >= t.selection_bias_excess_buffer:
            winner = top.account_id
            selection_note = f"冠军超额领先亚军 {gap:.2%} >= {t.selection_bias_excess_buffer:.2%}"
        else:
            selection_note = (
                f"冠军仅领先亚军 {gap:.2%} < {t.selection_bias_excess_buffer:.2%}，"
                "差距可能为噪声，暂不晋级（选择偏差守卫）"
            )

    return {
        "window_label": window_label,
        "ranking": [e.to_dict() for e in sorted(
            evals, key=lambda e: e.metrics.get("excess_return") or -1e9, reverse=True)],
        "eligible_count": len(eligible),
        "recommended_winner": winner,
        "selection_note": selection_note,
    }


def promote_account(conn: duckdb.DuckDBPyConnection, account_id: str) -> None:
    """标记账户为实盘指导候选并置 PROMOTED。"""
    mark_real_candidate(conn, account_id, True)
    set_status(conn, account_id, "PROMOTED")


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2%}"
