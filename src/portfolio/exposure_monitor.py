"""Portfolio exposure calculations for the satellite stock sleeve."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

DEFAULT_BENCHMARK_INDEX = "000300"
UNKNOWN_INDUSTRY = "未知行业"
UNKNOWN_SIZE = "未知市值"


@dataclass(frozen=True)
class ExposureRiskThresholds:
    max_position_weight: float = 0.15
    max_industry_weight: float = 0.30
    max_top5_weight: float = 0.70
    max_unknown_industry_weight: float = 0.05
    min_pe_coverage: float = 0.80
    min_pb_coverage: float = 0.80

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> ExposureRiskThresholds:
        raw = config or {}
        return cls(
            max_position_weight=_safe_float(raw.get("max_position_weight_warn", cls.max_position_weight)),
            max_industry_weight=_safe_float(raw.get("max_industry_weight_warn", cls.max_industry_weight)),
            max_top5_weight=_safe_float(raw.get("max_top5_weight_warn", cls.max_top5_weight)),
            max_unknown_industry_weight=_safe_float(
                raw.get("max_unknown_industry_weight_warn", cls.max_unknown_industry_weight)
            ),
            min_pe_coverage=_safe_float(raw.get("min_pe_coverage", raw.get("min_pe_coverage_warn", cls.min_pe_coverage))),
            min_pb_coverage=_safe_float(raw.get("min_pb_coverage", raw.get("min_pb_coverage_warn", cls.min_pb_coverage))),
        )


def load_exposure_snapshot(
    conn: Any,
    benchmark_index: str = DEFAULT_BENCHMARK_INDEX,
    as_of: date | None = None,
    thresholds: ExposureRiskThresholds | dict[str, Any] | None = None,
) -> dict[str, pd.DataFrame]:
    holdings = load_current_stock_holdings(conn, as_of=as_of)
    benchmark = load_benchmark_members(conn, benchmark_index=benchmark_index, as_of=as_of)
    return compute_exposure_snapshot(holdings, benchmark, thresholds=thresholds)


def load_current_stock_holdings(conn: Any, as_of: date | None = None) -> pd.DataFrame:
    price_date_filter = ""
    if as_of is not None:
        price_date_filter = "AND trade_date <= ?"

    from src.portfolio.current_holdings import current_positions_cte

    current_positions, position_params = current_positions_cte(as_of=as_of)
    price_params = [as_of] if as_of is not None else []
    return conn.execute(f"""
        WITH {current_positions},
        latest_price AS (
            SELECT symbol, pe_ttm, pb
            FROM daily_price
            WHERE 1 = 1
              {price_date_filter}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        )
        SELECT
            p.strategy_name,
            p.trade_date,
            p.symbol,
            p.quantity,
            p.current_price,
            p.market_value,
            COALESCE(si.name, p.symbol) AS name,
            COALESCE(si.industry, si.sector, ?) AS industry,
            si.sector,
            si.market_cap,
            lp.pe_ttm,
            lp.pb
        FROM current_positions p
        LEFT JOIN stock_info si ON p.symbol = si.symbol
        LEFT JOIN latest_price lp ON p.symbol = lp.symbol
        ORDER BY p.market_value DESC, p.symbol
    """, [*position_params, *price_params, UNKNOWN_INDUSTRY]).fetchdf()


def load_benchmark_members(
    conn: Any,
    benchmark_index: str = DEFAULT_BENCHMARK_INDEX,
    as_of: date | None = None,
) -> pd.DataFrame:
    if as_of is not None:
        effective_date = as_of
    else:
        effective_date = conn.execute("""
            SELECT COALESCE(MAX(start_date), CURRENT_DATE)
            FROM index_member_history
            WHERE index_code = ?
        """, [benchmark_index]).fetchone()[0]
    return conn.execute("""
        SELECT
            imh.symbol,
            COALESCE(si.name, imh.symbol) AS name,
            COALESCE(si.industry, si.sector, ?) AS industry,
            si.sector,
            si.market_cap
        FROM index_member_history imh
        LEFT JOIN stock_info si ON imh.symbol = si.symbol
        WHERE imh.index_code = ?
          AND imh.start_date <= ?
          AND (imh.end_date IS NULL OR imh.end_date >= ?)
        ORDER BY imh.symbol
    """, [UNKNOWN_INDUSTRY, benchmark_index, effective_date, effective_date]).fetchdf()


def compute_exposure_snapshot(
    holdings: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    thresholds: ExposureRiskThresholds | dict[str, Any] | None = None,
) -> dict[str, pd.DataFrame]:
    positions = _aggregate_positions(holdings)
    if positions.empty:
        return {
            "positions": positions,
            "industry": _empty_industry_frame(),
            "size": _empty_size_frame(),
            "summary": _summary_frame(),
            "warnings": _empty_warning_frame(),
        }

    total_value = float(positions["market_value"].sum())
    positions["weight"] = positions["market_value"] / total_value if total_value > 0 else 0.0
    positions["size_bucket"] = positions["market_cap"].map(size_bucket)

    benchmark_weights = _benchmark_industry_weights(benchmark)
    industry = _industry_exposure(positions, benchmark_weights)
    size = _size_exposure(positions)
    summary = _summary_frame(
        position_count=len(positions),
        total_value=total_value,
        top1_weight=float(positions["weight"].max()),
        top5_weight=float(positions["weight"].head(5).sum()),
        max_industry_weight=float(industry["weight"].max()) if not industry.empty else 0.0,
        unknown_industry_weight=float(
            industry.loc[industry["industry"] == UNKNOWN_INDUSTRY, "weight"].sum()
        ) if not industry.empty else 0.0,
        weighted_pe_ttm=_weighted_metric(positions, "pe_ttm"),
        weighted_pb=_weighted_metric(positions, "pb"),
        pe_coverage=_metric_coverage(positions, "pe_ttm"),
        pb_coverage=_metric_coverage(positions, "pb"),
    )
    return {
        "positions": positions,
        "industry": industry,
        "size": size,
        "summary": summary,
        "warnings": evaluate_exposure_warnings(summary, thresholds),
    }


def evaluate_exposure_warnings(
    summary: pd.DataFrame | pd.Series | dict[str, Any],
    thresholds: ExposureRiskThresholds | dict[str, Any] | None = None,
) -> pd.DataFrame:
    if isinstance(summary, pd.DataFrame):
        if summary.empty or int(summary.iloc[0].get("position_count") or 0) <= 0:
            return _empty_warning_frame()
        values = summary.iloc[0].to_dict()
    elif isinstance(summary, pd.Series):
        if int(summary.get("position_count") or 0) <= 0:
            return _empty_warning_frame()
        values = summary.to_dict()
    else:
        if int(summary.get("position_count") or 0) <= 0:
            return _empty_warning_frame()
        values = dict(summary)

    cfg = _coerce_thresholds(thresholds)
    checks = [
        _max_check("top1_weight", "最大单票", values.get("top1_weight"), cfg.max_position_weight),
        _max_check("max_industry_weight", "最大行业", values.get("max_industry_weight"), cfg.max_industry_weight),
        _max_check("top5_weight", "Top5集中度", values.get("top5_weight"), cfg.max_top5_weight),
        _max_check(
            "unknown_industry_weight",
            "未知行业占比",
            values.get("unknown_industry_weight"),
            cfg.max_unknown_industry_weight,
        ),
        _min_check("pe_coverage", "PE覆盖率", values.get("pe_coverage"), cfg.min_pe_coverage),
        _min_check("pb_coverage", "PB覆盖率", values.get("pb_coverage"), cfg.min_pb_coverage),
    ]
    return pd.DataFrame(checks, columns=_warning_columns())


def size_bucket(market_cap: Any) -> str:
    value = _safe_float(market_cap)
    if value <= 0:
        return UNKNOWN_SIZE
    if value >= 2000:
        return "超大盘"
    if value >= 500:
        return "大盘"
    if value >= 100:
        return "中盘"
    return "小盘"


def _aggregate_positions(holdings: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty:
        return pd.DataFrame(columns=[
            "symbol", "name", "industry", "sector", "market_cap", "pe_ttm", "pb", "market_value", "strategies"
        ])
    df = holdings.copy()
    df["market_value"] = pd.to_numeric(df.get("market_value", 0), errors="coerce").fillna(0.0)
    df = df[df["market_value"] > 0].copy()
    if df.empty:
        return _aggregate_positions(pd.DataFrame())
    df["industry"] = df.get("industry", UNKNOWN_INDUSTRY).fillna(UNKNOWN_INDUSTRY).replace("", UNKNOWN_INDUSTRY)
    if "name" not in df:
        df["name"] = df["symbol"]
    if "sector" not in df:
        df["sector"] = ""
    df["market_cap"] = pd.to_numeric(df.get("market_cap", 0), errors="coerce").fillna(0.0)
    df["pe_ttm"] = pd.to_numeric(df.get("pe_ttm", 0), errors="coerce")
    df["pb"] = pd.to_numeric(df.get("pb", 0), errors="coerce")
    grouped = df.groupby("symbol", as_index=False).agg({
        "name": "first",
        "industry": "first",
        "sector": "first",
        "market_cap": "first",
        "pe_ttm": "first",
        "pb": "first",
        "market_value": "sum",
        "strategy_name": lambda values: ",".join(sorted(set(str(v) for v in values if pd.notna(v)))),
    })
    grouped = grouped.rename(columns={"strategy_name": "strategies"})
    return grouped.sort_values(["market_value", "symbol"], ascending=[False, True]).reset_index(drop=True)


def _benchmark_industry_weights(benchmark: pd.DataFrame | None) -> dict[str, float]:
    if benchmark is None or benchmark.empty:
        return {}
    df = benchmark.copy()
    df["industry"] = df.get("industry", UNKNOWN_INDUSTRY).fillna(UNKNOWN_INDUSTRY).replace("", UNKNOWN_INDUSTRY)
    df["market_cap"] = pd.to_numeric(df.get("market_cap", 0), errors="coerce").fillna(0.0)
    total_market_cap = float(df["market_cap"].sum())
    if total_market_cap > 0:
        df["_benchmark_weight"] = df["market_cap"] / total_market_cap
    else:
        df["_benchmark_weight"] = 1.0 / len(df)
    weights = df.groupby("industry")["_benchmark_weight"].sum()
    return {str(industry): float(weight) for industry, weight in weights.items()}


def _industry_exposure(positions: pd.DataFrame, benchmark_weights: dict[str, float]) -> pd.DataFrame:
    rows = positions.groupby("industry", as_index=False).agg(
        market_value=("market_value", "sum"),
        weight=("weight", "sum"),
        position_count=("symbol", "nunique"),
    )
    benchmark_rows = [
        {"industry": industry, "market_value": 0.0, "weight": 0.0, "position_count": 0}
        for industry in benchmark_weights
        if industry not in set(rows["industry"])
    ]
    if benchmark_rows:
        rows = pd.concat([rows, pd.DataFrame(benchmark_rows)], ignore_index=True)
    rows["benchmark_weight"] = rows["industry"].map(benchmark_weights).fillna(0.0)
    rows["relative_weight"] = rows["weight"] - rows["benchmark_weight"]
    return rows.sort_values(["weight", "relative_weight", "industry"], ascending=[False, False, True]).reset_index(drop=True)


def _size_exposure(positions: pd.DataFrame) -> pd.DataFrame:
    rows = positions.groupby("size_bucket", as_index=False).agg(
        market_value=("market_value", "sum"),
        weight=("weight", "sum"),
        position_count=("symbol", "nunique"),
    )
    order = {"超大盘": 0, "大盘": 1, "中盘": 2, "小盘": 3, UNKNOWN_SIZE: 4}
    rows["_order"] = rows["size_bucket"].map(order).fillna(99)
    return rows.sort_values(["_order", "weight"], ascending=[True, False]).drop(columns=["_order"]).reset_index(drop=True)


def _weighted_metric(positions: pd.DataFrame, column: str) -> float:
    values = pd.to_numeric(positions.get(column, 0), errors="coerce")
    valid = values.notna() & (values > 0)
    covered_weight = positions.loc[valid, "weight"].sum()
    if covered_weight <= 0:
        return 0.0
    return float((values[valid] * positions.loc[valid, "weight"]).sum() / covered_weight)


def _metric_coverage(positions: pd.DataFrame, column: str) -> float:
    values = pd.to_numeric(positions.get(column, 0), errors="coerce")
    valid = values.notna() & (values > 0)
    return float(positions.loc[valid, "weight"].sum())


def _summary_frame(**values: Any) -> pd.DataFrame:
    defaults = {
        "position_count": 0,
        "total_value": 0.0,
        "top1_weight": 0.0,
        "top5_weight": 0.0,
        "max_industry_weight": 0.0,
        "unknown_industry_weight": 0.0,
        "weighted_pe_ttm": 0.0,
        "weighted_pb": 0.0,
        "pe_coverage": 0.0,
        "pb_coverage": 0.0,
    }
    defaults.update(values)
    return pd.DataFrame([defaults])


def _empty_industry_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "industry", "market_value", "weight", "position_count", "benchmark_weight", "relative_weight"
    ])


def _empty_size_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["size_bucket", "market_value", "weight", "position_count"])


def _empty_warning_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_warning_columns())


def _warning_columns() -> list[str]:
    return ["metric", "label", "value", "threshold", "status", "severity", "detail"]


def _coerce_thresholds(thresholds: ExposureRiskThresholds | dict[str, Any] | None) -> ExposureRiskThresholds:
    if isinstance(thresholds, ExposureRiskThresholds):
        return thresholds
    if isinstance(thresholds, dict):
        return ExposureRiskThresholds.from_config(thresholds)
    return ExposureRiskThresholds()


def _max_check(metric: str, label: str, value: Any, threshold: float) -> dict[str, Any]:
    value_float = _safe_float(value)
    threshold_float = _safe_float(threshold)
    status = "WARN" if value_float > threshold_float else "OK"
    return {
        "metric": metric,
        "label": label,
        "value": value_float,
        "threshold": threshold_float,
        "status": status,
        "severity": "WARN" if status == "WARN" else "OK",
        "detail": f"{label} {value_float:.1%}，上限 {threshold_float:.1%}",
    }


def _min_check(metric: str, label: str, value: Any, threshold: float) -> dict[str, Any]:
    value_float = _safe_float(value)
    threshold_float = _safe_float(threshold)
    status = "WARN" if value_float < threshold_float else "OK"
    return {
        "metric": metric,
        "label": label,
        "value": value_float,
        "threshold": threshold_float,
        "status": status,
        "severity": "WARN" if status == "WARN" else "OK",
        "detail": f"{label} {value_float:.1%}，下限 {threshold_float:.1%}",
    }


def _safe_float(value: Any) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
