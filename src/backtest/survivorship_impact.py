"""Survivorship-bias impact diagnostics for persisted Qlib predictions."""
from __future__ import annotations

import argparse
import math
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.qlib_runner import (
    MODEL_NAME,
    _load_price_frame,
    align_benchmark_to_strategy_periods,
    compute_periodic_metrics,
    load_benchmark_suite,
    simulate_topn_open,
)
from src.config import PROJECT_ROOT
from src.data_pipeline.index_membership import load_index_member_history
from src.data_pipeline.loader import get_connection, init_db

DEFAULT_INDEX_CODES = ("000300", "000905")


def _normalize_predictions(pred: pd.DataFrame) -> pd.DataFrame:
    if pred.empty:
        return pd.DataFrame(columns=["datetime", "instrument", "score"])
    clean = pred.copy()
    clean["datetime"] = pd.to_datetime(clean["datetime"])
    clean["instrument"] = clean["instrument"].astype(str)
    return clean.sort_values(["datetime", "instrument"]).reset_index(drop=True)


def _normalize_membership(membership: pd.DataFrame, index_codes: Iterable[str]) -> pd.DataFrame:
    columns = ["index_code", "symbol", "start_date", "end_date", "source"]
    if membership.empty:
        return pd.DataFrame(columns=columns)
    codes = {str(code) for code in index_codes}
    clean = membership.copy()
    clean["index_code"] = clean["index_code"].astype(str)
    clean["symbol"] = clean["symbol"].astype(str)
    clean["start_date"] = pd.to_datetime(clean["start_date"]).dt.normalize()
    clean["end_date"] = pd.to_datetime(clean["end_date"]).dt.normalize()
    return clean[clean["index_code"].isin(codes)].reset_index(drop=True)


def filter_predictions_by_static_universe(
    pred: pd.DataFrame,
    membership: pd.DataFrame,
    index_codes: Iterable[str] = DEFAULT_INDEX_CODES,
    as_of: date | str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Keep predictions whose symbols are active constituents on one static date."""
    clean_pred = _normalize_predictions(pred)
    if clean_pred.empty:
        return clean_pred
    clean_membership = _normalize_membership(membership, index_codes)
    if clean_membership.empty:
        return clean_pred.iloc[0:0].copy()

    query_date = pd.to_datetime(as_of or clean_pred["datetime"].max()).normalize()
    active = clean_membership[
        (clean_membership["start_date"] <= query_date)
        & (clean_membership["end_date"].isna() | (clean_membership["end_date"] >= query_date))
    ]
    active_symbols = set(active["symbol"].astype(str))
    return clean_pred[clean_pred["instrument"].isin(active_symbols)].reset_index(drop=True)


def filter_predictions_by_point_in_time_universe(
    pred: pd.DataFrame,
    membership: pd.DataFrame,
    index_codes: Iterable[str] = DEFAULT_INDEX_CODES,
) -> pd.DataFrame:
    """Keep predictions whose symbols belonged to the target index on that prediction date."""
    clean_pred = _normalize_predictions(pred)
    if clean_pred.empty:
        return clean_pred
    clean_membership = _normalize_membership(membership, index_codes)
    if clean_membership.empty:
        return clean_pred.iloc[0:0].copy()

    work = clean_pred.copy()
    work["_row_id"] = range(len(work))
    work["_prediction_date"] = work["datetime"].dt.normalize()
    merged = work.merge(
        clean_membership,
        left_on="instrument",
        right_on="symbol",
        how="inner",
    )
    if merged.empty:
        return clean_pred.iloc[0:0].copy()
    active = merged[
        (merged["start_date"] <= merged["_prediction_date"])
        & (merged["end_date"].isna() | (merged["end_date"] >= merged["_prediction_date"]))
    ]
    if active.empty:
        return clean_pred.iloc[0:0].copy()
    row_ids = active["_row_id"].drop_duplicates().sort_values()
    return clean_pred.iloc[row_ids.to_list()].reset_index(drop=True)


def summarize_prediction_filter(
    original: pd.DataFrame,
    static_filtered: pd.DataFrame,
    pit_filtered: pd.DataFrame,
) -> dict[str, Any]:
    original = _normalize_predictions(original)
    static_filtered = _normalize_predictions(static_filtered)
    pit_filtered = _normalize_predictions(pit_filtered)

    def avg_candidates(df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        return float(df.groupby("datetime")["instrument"].nunique().mean())

    def dates(df: pd.DataFrame) -> int:
        return int(df["datetime"].nunique()) if not df.empty else 0

    def symbols(df: pd.DataFrame) -> int:
        return int(df["instrument"].nunique()) if not df.empty else 0

    return {
        "original_rows": int(len(original)),
        "static_rows": int(len(static_filtered)),
        "point_in_time_rows": int(len(pit_filtered)),
        "original_dates": dates(original),
        "static_dates": dates(static_filtered),
        "point_in_time_dates": dates(pit_filtered),
        "original_symbols": symbols(original),
        "static_symbols": symbols(static_filtered),
        "point_in_time_symbols": symbols(pit_filtered),
        "original_avg_candidates_per_date": avg_candidates(original),
        "static_avg_candidates_per_date": avg_candidates(static_filtered),
        "point_in_time_avg_candidates_per_date": avg_candidates(pit_filtered),
        "static_row_coverage": float(len(static_filtered) / len(original)) if len(original) else 0.0,
        "point_in_time_row_coverage": float(len(pit_filtered) / len(original)) if len(original) else 0.0,
    }


def _fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _fmt_pp(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f} pp"


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _metric(metrics: dict[str, Any], key: str) -> Any:
    return metrics.get(key) if metrics else None


def format_survivorship_report(
    experiment: dict[str, Any],
    filter_summary: dict[str, Any],
    static_metrics: dict[str, Any],
    pit_metrics: dict[str, Any],
) -> str:
    annual_bias = None
    excess_bias = None
    if _metric(static_metrics, "annual_return") is not None and _metric(pit_metrics, "annual_return") is not None:
        annual_bias = float(static_metrics["annual_return"]) - float(pit_metrics["annual_return"])
    if _metric(static_metrics, "excess_return") is not None and _metric(pit_metrics, "excess_return") is not None:
        excess_bias = float(static_metrics["excess_return"]) - float(pit_metrics["excess_return"])

    if annual_bias is None:
        verdict = "样本不足，无法给出静态池偏差方向。"
    elif annual_bias > 0:
        verdict = f"静态池乐观偏差约 {_fmt_pp(annual_bias)} 年化收益；后续评估应优先采用 point-in-time universe。"
    elif annual_bias < 0:
        verdict = f"本样本静态池未抬高收益，反而低于 PIT 池 {_fmt_pp(abs(annual_bias))}；但 PIT 口径仍更接近真实可交易约束。"
    else:
        verdict = "本样本静态池与 PIT 池年化收益持平；PIT 口径仍应作为默认回测口径。"

    rows = [
        "| 指标 | 当前静态成分池 | 历史 PIT 成分池 | 静态 - PIT |",
        "|---|---:|---:|---:|",
        (
            "| 年化收益 | "
            f"{_fmt_pct(_metric(static_metrics, 'annual_return'))} | "
            f"{_fmt_pct(_metric(pit_metrics, 'annual_return'))} | "
            f"{_fmt_pp(annual_bias)} |"
        ),
        (
            "| 年化超额收益 | "
            f"{_fmt_pct(_metric(static_metrics, 'excess_return'))} | "
            f"{_fmt_pct(_metric(pit_metrics, 'excess_return'))} | "
            f"{_fmt_pp(excess_bias)} |"
        ),
        (
            "| Sharpe | "
            f"{_fmt_num(_metric(static_metrics, 'sharpe_ratio'))} | "
            f"{_fmt_num(_metric(pit_metrics, 'sharpe_ratio'))} | "
            f"{_fmt_num((static_metrics.get('sharpe_ratio') or 0) - (pit_metrics.get('sharpe_ratio') or 0))} |"
        ),
        (
            "| 最大回撤 | "
            f"{_fmt_pct(_metric(static_metrics, 'max_drawdown'))} | "
            f"{_fmt_pct(_metric(pit_metrics, 'max_drawdown'))} | "
            f"{_fmt_pp((static_metrics.get('max_drawdown') or 0) - (pit_metrics.get('max_drawdown') or 0))} |"
        ),
        (
            "| 年化换手 | "
            f"{_fmt_num(_metric(static_metrics, 'turnover'))} | "
            f"{_fmt_num(_metric(pit_metrics, 'turnover'))} | "
            f"{_fmt_num((static_metrics.get('turnover') or 0) - (pit_metrics.get('turnover') or 0))} |"
        ),
    ]

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    benchmark = static_metrics.get("benchmark_name") or pit_metrics.get("benchmark_name") or "n/a"
    return "\n".join([
        "# Survivorship Impact Report v2",
        "",
        f"> 生成时间：{generated}",
        f"> 实验：`{experiment.get('experiment_id')}` / `{experiment.get('model_version')}`",
        "",
        "## 结论",
        "",
        verdict,
        "",
        "## 回放口径",
        "",
        f"- 区间：{experiment.get('start')} ~ {experiment.get('end')}",
        f"- 策略参数：top_n={experiment.get('top_n')}，holding_days={experiment.get('holding_days')}，rebalance={experiment.get('rebalance_freq')}，buffer_n={experiment.get('buffer_n')}",
        f"- 基准：{benchmark}",
        "- 静态池：以回测结束日仍在 `000300/000905` 的股票为全区间股票池。",
        "- PIT 池：每个 prediction date 按 `index_member_history.start_date/end_date` 动态过滤。",
        "- 数据限制：当前免费历史成分来自 Baostock 月度快照，能消除最严重的“只看当前成分”偏差，但调整生效日近似到月末，不等价于付费源的官方日级历史成分。",
        "",
        "## 指标对比",
        "",
        *rows,
        "",
        "## 预测截面过滤诊断",
        "",
        "| 指标 | 原始预测 | 当前静态成分池 | 历史 PIT 成分池 |",
        "|---|---:|---:|---:|",
        f"| rows | {filter_summary.get('original_rows', 0):,} | {filter_summary.get('static_rows', 0):,} | {filter_summary.get('point_in_time_rows', 0):,} |",
        f"| dates | {filter_summary.get('original_dates', 0):,} | {filter_summary.get('static_dates', 0):,} | {filter_summary.get('point_in_time_dates', 0):,} |",
        f"| symbols | {filter_summary.get('original_symbols', 0):,} | {filter_summary.get('static_symbols', 0):,} | {filter_summary.get('point_in_time_symbols', 0):,} |",
        f"| avg candidates/date | {_fmt_num(filter_summary.get('original_avg_candidates_per_date'))} | {_fmt_num(filter_summary.get('static_avg_candidates_per_date'))} | {_fmt_num(filter_summary.get('point_in_time_avg_candidates_per_date'))} |",
        "",
        "## 执行建议",
        "",
        "- 后续候选模型 PK、参数网格搜索和 production promotion 应默认使用 PIT universe。",
        "- 若要做正式上线前审计，再补一版官方日级历史成分；半年验证期内，本报告口径优先满足“免费数据源、可持续更新”的原则。",
        "",
    ])


def _date_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.to_datetime(value).date().isoformat()


def _load_experiment_spec(conn: Any, experiment_id: str | None) -> dict[str, Any]:
    where = "r.model_name = ?"
    params: list[Any] = [MODEL_NAME]
    if experiment_id:
        where += " AND r.experiment_id = ?"
        params.append(experiment_id)
    else:
        where += " AND r.status = 'production'"

    row = conn.execute(f"""
        SELECT r.experiment_id, r.model_version, e.test_start, e.test_end,
               c.best_top_n, c.best_holding_days, c.best_rebalance_freq, c.best_buffer_n
        FROM qlib_model_registry r
        LEFT JOIN qlib_experiments e ON e.experiment_id = r.experiment_id
        LEFT JOIN qlib_candidate_results c
          ON c.experiment_id = r.experiment_id
         AND c.status = 'SUCCEEDED'
        WHERE {where}
        ORDER BY r.published_at DESC NULLS LAST, c.ended_at DESC NULLS LAST, r.created_at DESC
        LIMIT 1
    """, params).fetchone()
    if not row:
        target = experiment_id or "production"
        raise ValueError(f"找不到 Qlib {target} 模型记录")

    bounds = conn.execute("""
        SELECT MIN(prediction_date), MAX(prediction_date)
        FROM qlib_predictions
        WHERE experiment_id = ?
    """, [row[0]]).fetchone()
    start = _date_str(row[2]) or _date_str(bounds[0])
    end = _date_str(row[3]) or _date_str(bounds[1])
    top_n = int(row[4] or 15)
    holding_days = int(row[5] or 15)
    rebalance_freq = str(row[6] or "monthly")
    buffer_n = int(row[7] or math.ceil(top_n * 1.5))
    return {
        "experiment_id": row[0],
        "model_version": row[1],
        "start": start,
        "end": end,
        "top_n": top_n,
        "holding_days": holding_days,
        "rebalance_freq": rebalance_freq,
        "buffer_n": buffer_n,
    }


def _load_predictions(conn: Any, experiment_id: str, start: str, end: str) -> pd.DataFrame:
    pred = conn.execute("""
        SELECT prediction_date AS datetime, symbol AS instrument, score
        FROM qlib_predictions
        WHERE experiment_id = ?
          AND prediction_date >= ?
          AND prediction_date <= ?
        ORDER BY prediction_date, symbol
    """, [experiment_id, start, end]).fetchdf()
    return _normalize_predictions(pred)


def _compute_strategy_metrics(
    pred: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int,
    holding_days: int,
    rebalance_freq: str,
    buffer_n: int,
) -> dict[str, Any]:
    returns = simulate_topn_open(
        pred,
        prices,
        top_n=top_n,
        holding_days=holding_days,
        rebalance_freq=rebalance_freq,
        buffer_n=buffer_n,
    )
    if returns.empty:
        return {}

    benchmarks = load_benchmark_suite()
    benchmark_name = "MIXED_EQUAL" if "MIXED_EQUAL" in benchmarks else next(iter(benchmarks), None)
    aligned = pd.Series(dtype=float)
    if benchmark_name:
        aligned = align_benchmark_to_strategy_periods(
            benchmarks[benchmark_name],
            pd.DatetimeIndex(returns.index),
            holding_days=holding_days,
            rebalance_freq=rebalance_freq,
        )
    metrics = compute_periodic_metrics(
        returns,
        benchmark_returns=aligned,
        periods_per_year=int(returns.attrs.get("periods_per_year", 252)),
        turnover=returns.attrs.get("turnover"),
    )
    metrics["benchmark_name"] = benchmark_name
    metrics["period_count"] = int(len(returns))
    return metrics


def run_survivorship_impact(
    experiment_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    output: Path | None = None,
    index_codes: Iterable[str] = DEFAULT_INDEX_CODES,
) -> dict[str, Any]:
    conn = get_connection()
    try:
        init_db(conn)
        spec = _load_experiment_spec(conn, experiment_id)
        if start:
            spec["start"] = start
        if end:
            spec["end"] = end
        if not spec.get("start") or not spec.get("end"):
            raise ValueError("实验缺少可用 prediction date 区间")
        pred = _load_predictions(conn, spec["experiment_id"], spec["start"], spec["end"])
        membership = load_index_member_history(conn, index_codes)
    finally:
        conn.close()

    if pred.empty:
        raise ValueError(f"实验没有可用预测: {spec['experiment_id']} {spec['start']}~{spec['end']}")
    if membership.empty:
        raise ValueError("index_member_history 为空，无法生成幸存者偏差报告")

    static_pred = filter_predictions_by_static_universe(pred, membership, index_codes, as_of=spec["end"])
    pit_pred = filter_predictions_by_point_in_time_universe(pred, membership, index_codes)
    filter_summary = summarize_prediction_filter(pred, static_pred, pit_pred)

    symbols = sorted(set(static_pred["instrument"]).union(set(pit_pred["instrument"])))
    price_end = (pd.to_datetime(spec["end"]) + pd.Timedelta(days=int(spec["holding_days"]) + 30)).date().isoformat()
    prices = _load_price_frame(symbols, spec["start"], price_end)
    if prices.empty:
        raise ValueError("daily_price 中缺少回放价格数据")

    static_metrics = _compute_strategy_metrics(
        static_pred,
        prices,
        top_n=int(spec["top_n"]),
        holding_days=int(spec["holding_days"]),
        rebalance_freq=str(spec["rebalance_freq"]),
        buffer_n=int(spec["buffer_n"]),
    )
    pit_metrics = _compute_strategy_metrics(
        pit_pred,
        prices,
        top_n=int(spec["top_n"]),
        holding_days=int(spec["holding_days"]),
        rebalance_freq=str(spec["rebalance_freq"]),
        buffer_n=int(spec["buffer_n"]),
    )
    report = format_survivorship_report(spec, filter_summary, static_metrics, pit_metrics)

    out_path = output or PROJECT_ROOT / "docs" / "survivorship_impact_v2.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return {
        "output": str(out_path),
        "experiment": spec,
        "filter_summary": filter_summary,
        "static_metrics": static_metrics,
        "point_in_time_metrics": pit_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate static-vs-PIT universe survivorship impact report.")
    parser.add_argument("--experiment-id", default=None, help="Qlib experiment id. Defaults to current production model.")
    parser.add_argument("--start", default=None, help="Override prediction start date, YYYY-MM-DD.")
    parser.add_argument("--end", default=None, help="Override prediction end date, YYYY-MM-DD.")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "survivorship_impact_v2.md",
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--index-codes",
        default="000300,000905",
        help="Comma-separated index codes used for PIT membership filtering.",
    )
    args = parser.parse_args(argv)
    result = run_survivorship_impact(
        experiment_id=args.experiment_id,
        start=args.start,
        end=args.end,
        output=args.output,
        index_codes=tuple(code.strip() for code in args.index_codes.split(",") if code.strip()),
    )
    print(f"Wrote {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
