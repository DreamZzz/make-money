"""Structured Qlib experiment comparison report helpers."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from src.backtest.qlib_runner import _publish_gate_thresholds, score_candidate_grid_row

METRIC_COLUMNS = [
    "annual_return",
    "cumulative_return",
    "annual_volatility",
    "sharpe_ratio",
    "max_drawdown",
    "turnover",
    "benchmark_return",
    "excess_return",
    "ic_mean",
    "icir",
    "rank_ic_mean",
    "rank_ic_positive_rate",
    "primary_benchmark",
    "avg_selected_count",
    "avg_cash_drag",
    "max_actual_position_pct",
]


def add_rolling_ic_columns(daily: pd.DataFrame, windows: tuple[int, ...] = (30, 60, 180)) -> pd.DataFrame:
    """Append rolling IC/RankIC decay columns for Dashboard monitoring."""
    if daily.empty:
        return daily.copy()
    out = daily.copy()
    out["metric_date"] = pd.to_datetime(out["metric_date"])
    out = out.sort_values("metric_date").reset_index(drop=True)
    for metric in ("rank_ic", "ic"):
        if metric not in out.columns:
            continue
        values = pd.to_numeric(out[metric], errors="coerce")
        for window in windows:
            out[f"{metric}_ma{window}"] = values.rolling(window=window, min_periods=1).mean()
    return out


METRIC_HIGHLIGHT_STANDARDS = [
    {
        "metric": "annual_return",
        "label": "年化收益",
        "winner_rule": "越高越好",
        "highlight_standard": "成功实验中该列最高者标记为胜出指标。",
    },
    {
        "metric": "excess_return",
        "label": "主基准超额",
        "winner_rule": "越高越好",
        "highlight_standard": "优先对比 MIXED_EQUAL 等主基准，超额最高说明相对基准贡献最大。",
    },
    {
        "metric": "sharpe_ratio",
        "label": "夏普",
        "winner_rule": "越高越好",
        "highlight_standard": "收益与波动折中后的风险调整收益，成功实验中最高者胜出。",
    },
    {
        "metric": "max_drawdown",
        "label": "最大回撤",
        "winner_rule": "越高越好",
        "highlight_standard": "回撤为负数，越接近 0 代表历史最深下跌越小。",
    },
    {
        "metric": "ic_mean",
        "label": "IC均值",
        "winner_rule": "越高越好",
        "highlight_standard": "预测分数与未来收益的平均相关性，成功实验中最高者胜出。",
    },
    {
        "metric": "icir",
        "label": "ICIR",
        "winner_rule": "越高越好",
        "highlight_standard": "IC 的稳定性指标，成功实验中最高者胜出。",
    },
    {
        "metric": "rank_ic_positive_rate",
        "label": "RankIC正占比",
        "winner_rule": "越高越好",
        "highlight_standard": "RankIC 为正的交易日占比，成功实验中最高者胜出。",
    },
    {
        "metric": "turnover",
        "label": "低换手",
        "winner_rule": "越低越好",
        "highlight_standard": "中长线风格下换手越低越容易落地，成功实验中最低者胜出。",
    },
    {
        "metric": "avg_cash_drag",
        "label": "低现金拖累",
        "winner_rule": "越低越好",
        "highlight_standard": "小资金约束实验中，真实一手成交后闲置现金越少，越接近可执行组合。",
    },
    {
        "metric": "max_actual_position_pct",
        "label": "单票不过度集中",
        "winner_rule": "低于约束上限",
        "highlight_standard": "30万档硬约束 6%，50万档硬约束 5%；超过约束的高价股会被跳过。",
    },
]


METRIC_GLOSSARY = [
    {
        "metric": "annual_return",
        "label": "年化收益",
        "meaning": "把测试期收益折算成一年口径后的收益率。",
        "plain_explanation": "像把不同长度的考试都换算成百分制，方便横向比较赚钱速度。",
        "watch_out": "不能只看它，短期暴涨或高换手也可能把年化拉高。",
    },
    {
        "metric": "excess_return",
        "label": "主基准超额",
        "meaning": "策略收益减去主基准收益，当前主基准优先为 MIXED_EQUAL。",
        "plain_explanation": "不是看它有没有赚钱，而是看它有没有比沪深300/中证500/全A代理混合基准更会赚钱。",
        "watch_out": "超额长期为负，说明模型不如直接买基准。",
    },
    {
        "metric": "sharpe_ratio",
        "label": "夏普",
        "meaning": "单位波动承担下获得的收益，衡量风险调整收益。",
        "plain_explanation": "同样赚钱，路上越不颠簸越好；夏普就是看赚钱是不是比较稳。",
        "watch_out": "收益序列很短时夏普会不稳定。",
    },
    {
        "metric": "max_drawdown",
        "label": "最大回撤",
        "meaning": "测试期内从高点到低点的最大跌幅。",
        "plain_explanation": "最难受的一段最大亏了多少。比如 -20% 就是从阶段高点最多跌了五分之一。",
        "watch_out": "回撤越接近 0 越好，但过低回撤也要检查是否因为交易太少。",
    },
    {
        "metric": "turnover",
        "label": "换手",
        "meaning": "组合持仓被替换的频率，越高越接近频繁交易。",
        "plain_explanation": "它反映模型有多爱折腾。中长线策略里，低换手通常更容易真实执行。",
        "watch_out": "过高换手会放大交易成本和滑点。",
    },
    {
        "metric": "ic_mean",
        "label": "IC均值",
        "meaning": "每日预测分数与后续收益的平均相关性。",
        "plain_explanation": "模型给高分的股票，之后是不是真的更容易涨；正数越大越像有选股能力。",
        "watch_out": "IC 只说明排序能力，不等于交易后一定赚钱。",
    },
    {
        "metric": "icir",
        "label": "ICIR",
        "meaning": "IC 均值除以 IC 波动，衡量预测能力是否稳定。",
        "plain_explanation": "不是偶尔蒙对，而是看模型选股能力是否比较稳定地在线。",
        "watch_out": "默认发布门槛要求 ICIR >= 0.30，但仍要结合主基准超额和回撤。",
    },
    {
        "metric": "rank_ic_positive_rate",
        "label": "RankIC正占比",
        "meaning": "RankIC 大于 0 的日期占比。",
        "plain_explanation": "看模型有多少天方向是对的，像考试里答对题目的比例。",
        "watch_out": "比例高但幅度小，收益也可能不突出。",
    },
    {
        "metric": "selection_score",
        "label": "综合评分",
        "meaning": "候选批跑的综合选优分数，奖励超额和夏普，惩罚回撤与换手。",
        "plain_explanation": "这是给中长线可落地性打的总分，不让高收益但高换手/大回撤的结果轻易胜出。",
        "watch_out": "它是本地定义的选优规则，不是行业统一标准。",
    },
    {
        "metric": "avg_selected_count",
        "label": "平均可持仓数",
        "meaning": "小资金约束回测中，每次调仓真实能买入的一手股票数量均值。",
        "plain_explanation": "不是模型想买多少只，而是在 30/50 万资金、100股一手、单票上限下真的买得起多少只。",
        "watch_out": "数量太少会让组合集中，和 Top-N 理论组合偏离更大。",
    },
    {
        "metric": "avg_cash_drag",
        "label": "现金拖累",
        "meaning": "小资金约束回测中，因为一手/上限/估值过滤而无法投入的平均现金比例。",
        "plain_explanation": "钱放着没买股票的比例。越高，说明组合越难把资金有效配置出去。",
        "watch_out": "现金拖累可能降低波动和回撤，但也会稀释收益。",
    },
    {
        "metric": "max_actual_position_pct",
        "label": "最大单票占比",
        "meaning": "小资金约束回测中，单只股票实际买入金额占账户总资产的最高比例。",
        "plain_explanation": "用来防止一手高价股一下子占掉太大仓位。",
        "watch_out": "超过约束上限的股票会直接跳过，不会进入小资金可执行组合。",
    },
]


def load_experiment_report(conn: Any, limit: int = 100) -> dict[str, Any]:
    """Load and normalize Qlib report data from DuckDB."""
    limit = max(int(limit), 1)
    experiments = conn.execute(f"""
        SELECT experiment_id, run_id, model_name, model_version, mode, status,
               train_start, train_end, valid_start, valid_end, test_start, test_end,
               data_start, data_end, data_symbols, qlib_installed, qlib_data_ready,
               qlib_version, lightgbm_version, config_snapshot, metrics_json,
               error_message, started_at, ended_at
        FROM qlib_experiments
        ORDER BY started_at DESC NULLS LAST
        LIMIT {limit}
    """).fetchdf()
    grid = conn.execute("""
        SELECT source_experiment_id, model_name, mode, top_n, holding_days,
               rebalance_freq, buffer_n, benchmark_name, start_date, end_date,
               constraint_profile, account_capital, max_position_pct,
               cash_reserve_pct, lot_size, avg_selected_count,
               min_selected_count, avg_cash_drag, max_actual_position_pct,
               avg_weight_deviation, skipped_price_cap_avg, skipped_valuation_avg,
               skipped_budget_avg, max_pe_ttm, max_pb,
               annual_return, cumulative_return, annual_volatility, sharpe_ratio,
               max_drawdown, turnover, benchmark_return, excess_return, created_at
        FROM qlib_grid_results
    """).fetchdf()
    candidates = conn.execute(f"""
        SELECT candidate_id, batch_id, experiment_id, model_name, model_family,
               model_variant, status, mode, params_json, grid_json, best_benchmark,
               best_top_n, best_holding_days, best_rebalance_freq, best_buffer_n,
               best_constraint_profile, best_account_capital, avg_selected_count,
               avg_cash_drag, max_actual_position_pct,
               annual_return, sharpe_ratio, max_drawdown, turnover,
               benchmark_return, excess_return, ic_mean, icir, rank_ic_mean,
               rank_ic_positive_rate, score, error_message, started_at, ended_at
        FROM qlib_candidate_results
        ORDER BY started_at DESC NULLS LAST
        LIMIT {limit * 3}
    """).fetchdf()
    return build_experiment_report(experiments, grid, candidates)


def build_experiment_report(
    experiments: pd.DataFrame,
    grid: pd.DataFrame,
    candidates: pd.DataFrame,
) -> dict[str, Any]:
    exp = prepare_experiment_frame(experiments)
    benchmarks = prepare_benchmark_frame(experiments)
    grid_best = prepare_grid_best_frame(grid)
    candidate_results = prepare_candidate_frame(candidates)
    summary = summarize_report(exp, grid_best, candidate_results)
    exp = annotate_experiment_highlights(
        exp,
        summary.get("best_experiment"),
        summary.get("best_candidate"),
    )
    return {
        "experiments": exp,
        "benchmarks": benchmarks,
        "grid_best": grid_best,
        "candidate_results": candidate_results,
        "summary": summary,
        "highlight_standards": get_metric_highlight_standards(),
        "metric_glossary": get_metric_glossary(),
    }


def parse_json_dict(value: Any) -> dict[str, Any]:
    return _json_dict(value)


def get_metric_highlight_standards() -> list[dict[str, str]]:
    return [item.copy() for item in METRIC_HIGHLIGHT_STANDARDS]


def get_metric_glossary() -> list[dict[str, str]]:
    return [item.copy() for item in METRIC_GLOSSARY]


def annotate_experiment_highlights(
    experiments: pd.DataFrame,
    best_experiment: dict[str, Any] | None,
    best_candidate: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if experiments.empty:
        return experiments.copy()
    df = experiments.copy()
    df["is_final_selected"] = False
    df["is_candidate_selected"] = False
    df["winning_metrics"] = ""
    df["highlight_reason"] = ""

    succeeded = df[df["status"] == "SUCCEEDED"].copy()
    if succeeded.empty:
        return df

    winner_labels: dict[Any, list[str]] = {idx: [] for idx in df.index}
    for rule in METRIC_HIGHLIGHT_STANDARDS:
        metric = rule["metric"]
        if metric not in succeeded:
            continue
        values = pd.to_numeric(succeeded[metric], errors="coerce").dropna()
        if values.empty:
            continue
        target = values.min() if rule["winner_rule"] == "越低越好" else values.max()
        winning_index = values[values == target].index
        for idx in winning_index:
            winner_labels.setdefault(idx, []).append(rule["label"])

    best_id = best_experiment.get("experiment_id") if best_experiment else None
    if best_id is not None and "experiment_id" in df:
        df.loc[df["experiment_id"] == best_id, "is_final_selected"] = True
    candidate_best_id = best_candidate.get("experiment_id") if best_candidate else None
    if candidate_best_id is not None and "experiment_id" in df:
        df.loc[df["experiment_id"] == candidate_best_id, "is_candidate_selected"] = True

    for idx, labels in winner_labels.items():
        if labels:
            df.at[idx, "winning_metrics"] = "、".join(labels)

    def reason(row: pd.Series) -> str:
        parts: list[str] = []
        if bool(row.get("is_final_selected")):
            parts.append("实验指标最终选出")
        if bool(row.get("is_candidate_selected")):
            parts.append("候选批跑最终选出")
        if row.get("winning_metrics"):
            parts.append(f"胜出指标：{row.get('winning_metrics')}")
        if not parts and row.get("verdict"):
            parts.append(str(row.get("verdict")))
        return "；".join(parts)

    df["highlight_reason"] = df.apply(reason, axis=1)
    return df


def prepare_experiment_frame(experiments: pd.DataFrame) -> pd.DataFrame:
    if experiments.empty:
        return experiments.copy()

    df = experiments.copy()
    metrics = df["metrics_json"].map(_json_dict) if "metrics_json" in df else pd.Series([{}] * len(df))
    config = df["config_snapshot"].map(_json_dict) if "config_snapshot" in df else pd.Series([{}] * len(df))
    candidate = config.map(lambda item: item.get("candidate", {}) if isinstance(item, dict) else {})

    for key in METRIC_COLUMNS:
        df[key] = metrics.map(lambda item, k=key: item.get(k) if isinstance(item, dict) else None)
    df["candidate_id"] = candidate.map(lambda item: item.get("candidate_id") if isinstance(item, dict) else None)
    df["candidate_batch_id"] = candidate.map(lambda item: item.get("batch_id") if isinstance(item, dict) else None)
    df["candidate_variant"] = candidate.map(lambda item: item.get("model_variant") if isinstance(item, dict) else None)

    started = pd.to_datetime(df.get("started_at"), errors="coerce")
    ended = pd.to_datetime(df.get("ended_at"), errors="coerce")
    df["duration_seconds"] = (ended - started).dt.total_seconds()
    df["verdict"] = df.apply(_experiment_verdict, axis=1)
    return df


def prepare_benchmark_frame(experiments: pd.DataFrame) -> pd.DataFrame:
    if experiments.empty or "metrics_json" not in experiments:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for row in experiments.to_dict("records"):
        metrics = _json_dict(row.get("metrics_json"))
        suite = metrics.get("benchmark_suite") or {}
        if not isinstance(suite, dict):
            continue
        for benchmark_name, values in suite.items():
            if not isinstance(values, dict):
                continue
            rows.append({
                "experiment_id": row.get("experiment_id"),
                "mode": row.get("mode"),
                "status": row.get("status"),
                "model_version": row.get("model_version"),
                "benchmark_name": benchmark_name,
                "benchmark_return": values.get("benchmark_return"),
                "excess_return": values.get("excess_return"),
                "info_ratio": values.get("info_ratio"),
            })
    return pd.DataFrame(rows)


def prepare_grid_best_frame(grid: pd.DataFrame) -> pd.DataFrame:
    if grid.empty:
        return grid.copy()
    df = grid.copy()
    if "constraint_profile" not in df:
        df["constraint_profile"] = "theoretical_equal_weight"
    df["_is_small_account"] = df["constraint_profile"].fillna("").astype(str).str.startswith("small_account")
    df["selection_score"] = df.apply(score_candidate_grid_row, axis=1)
    sort_cols = [
        "source_experiment_id",
        "benchmark_name",
        "_is_small_account",
        "selection_score",
        "excess_return",
        "sharpe_ratio",
    ]
    df = df.sort_values(sort_cols, ascending=[True, True, False, False, False, False], na_position="last")
    return (
        df.groupby(["source_experiment_id", "benchmark_name"], as_index=False)
        .head(1)
        .drop(columns=["_is_small_account"], errors="ignore")
        .reset_index(drop=True)
    )


def prepare_candidate_frame(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    df = candidates.copy()
    df["selection_score"] = df["score"]
    return df.sort_values(
        ["batch_id", "status", "selection_score", "excess_return"],
        ascending=[False, True, False, False],
        na_position="last",
    ).reset_index(drop=True)


def summarize_report(
    experiments: pd.DataFrame,
    grid_best: pd.DataFrame,
    candidates: pd.DataFrame,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "experiment_count": int(len(experiments)),
        "succeeded_count": int((experiments.get("status") == "SUCCEEDED").sum()) if not experiments.empty else 0,
        "failed_count": int((experiments.get("status") == "FAILED").sum()) if not experiments.empty else 0,
        "best_experiment": None,
        "best_grid": None,
        "best_candidate": None,
    }
    succeeded = experiments[experiments["status"] == "SUCCEEDED"].copy() if not experiments.empty else pd.DataFrame()
    if not succeeded.empty:
        succeeded = succeeded.sort_values(
            ["excess_return", "icir", "sharpe_ratio"],
            ascending=[False, False, False],
            na_position="last",
        )
        summary["best_experiment"] = succeeded.iloc[0].to_dict()

    mixed_grid = grid_best[grid_best["benchmark_name"] == "MIXED_EQUAL"].copy() if not grid_best.empty else pd.DataFrame()
    if not mixed_grid.empty:
        mixed_grid = mixed_grid.sort_values(
            ["selection_score", "excess_return", "sharpe_ratio"],
            ascending=[False, False, False],
            na_position="last",
        )
        summary["best_grid"] = mixed_grid.iloc[0].to_dict()

    succeeded_candidates = candidates[
        (candidates["status"] == "SUCCEEDED") & candidates["selection_score"].notna()
    ].copy() if not candidates.empty else pd.DataFrame()
    if not succeeded_candidates.empty:
        succeeded_candidates = succeeded_candidates.sort_values(
            ["selection_score", "excess_return", "sharpe_ratio"],
            ascending=[False, False, False],
            na_position="last",
        )
        summary["best_candidate"] = succeeded_candidates.iloc[0].to_dict()
    return summary


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        if pd.isna(value):
            return {}
    except Exception:
        pass
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _experiment_verdict(row: pd.Series) -> str:
    status = row.get("status")
    if status == "FAILED":
        return "失败"
    if status == "RUNNING":
        return "运行中"
    if status != "SUCCEEDED":
        return "未完成"
    ic = _to_float(row.get("ic_mean"))
    icir = _to_float(row.get("icir"))
    excess = _to_float(row.get("excess_return"))
    max_drawdown = _to_float(row.get("max_drawdown"))
    thresholds = _publish_gate_thresholds()
    if ic <= thresholds["min_ic_mean"] or icir < thresholds["min_icir"]:
        return "IC未过门槛"
    if max_drawdown < thresholds["max_drawdown_floor"]:
        return "回撤过大"
    if excess <= thresholds["min_excess_return"]:
        return "明显跑输基准"
    if excess > 0:
        return "可重点关注"
    return "继续观察"


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default
