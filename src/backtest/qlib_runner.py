"""
Qlib Alpha158 experiment runner.

The Qlib workflow is intentionally split into research experiments and manual
production publishing. Experiments persist predictions and diagnostics; only a
published production model can write daily Alpha158 signals.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pickle
import platform
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from src.backtest.results import compute_metrics, load_benchmark_returns, save_backtest_result
from src.config import PROJECT_ROOT, load_config
from src.portfolio.execution_guards import check_open_tradeable

MODEL_NAME = "alpha158"
DEFAULT_TOP_N = 50
_QLIB_INITIALIZED = False
DEFAULT_SMALL_ACCOUNT_PROFILES = [
    {
        "account_capital": 300_000,
        "max_position_pct": 0.06,
        "cash_reserve_pct": 0.05,
        "lot_size": 100,
        "max_pe_ttm": 120.0,
        "max_pb": None,
    },
    {
        "account_capital": 500_000,
        "max_position_pct": 0.05,
        "cash_reserve_pct": 0.05,
        "lot_size": 100,
        "max_pe_ttm": 120.0,
        "max_pb": None,
    },
]


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _experiment_id(mode: str) -> str:
    return f"QLIB-{mode.upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def _model_version(experiment_id: str) -> str:
    return f"alpha158-{experiment_id.split('-')[-2].lower()}-{experiment_id.split('-')[-1].lower()}"


def _qlib_import_status() -> dict[str, Any]:
    status = {
        "qlib_installed": False,
        "qlib_version": None,
        "lightgbm_version": None,
        "python_version": platform.python_version(),
        "error": None,
    }
    try:
        import qlib  # type: ignore

        status["qlib_installed"] = True
        status["qlib_version"] = getattr(qlib, "__version__", "unknown")
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status

    try:
        import lightgbm  # type: ignore

        status["lightgbm_version"] = getattr(lightgbm, "__version__", "unknown")
    except Exception as exc:
        status["lightgbm_version"] = f"unavailable: {type(exc).__name__}"
    return status


def _qlib_data_status(market: str = "cn") -> dict[str, Any]:
    root = PROJECT_ROOT / "qlib_data" / f"{market}_data"
    calendar = root / "calendars" / "day.txt"
    instruments_dir = root / "instruments"
    features_dir = root / "features"
    ready = calendar.exists() and instruments_dir.exists() and features_dir.exists()
    return {
        "market": market,
        "path": str(root),
        "calendar_exists": calendar.exists(),
        "instruments_exists": instruments_dir.exists(),
        "features_exists": features_dir.exists(),
        "ready": ready,
    }


def qlib_status(market: str = "cn") -> dict[str, Any]:
    from src.data_pipeline.loader import get_connection, init_db

    env = _qlib_import_status()
    data = _qlib_data_status(market)
    conn = get_connection()
    try:
        init_db(conn)
        db_stats = conn.execute("""
            SELECT MIN(trade_date) AS data_start,
                   MAX(trade_date) AS data_end,
                   COUNT(DISTINCT symbol) AS symbols,
                   COUNT(*) AS rows
            FROM daily_price
            WHERE symbol IN (SELECT symbol FROM stock_info WHERE country = 'CN')
        """).fetchone()
        production = conn.execute("""
            SELECT model_version, experiment_id, published_at
            FROM qlib_model_registry
            WHERE model_name = ? AND status = 'production'
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """, [MODEL_NAME]).fetchone()
    finally:
        conn.close()

    return {
        **env,
        "qlib_data_ready": data["ready"],
        "qlib_data": data,
        "data_start": db_stats[0] if db_stats else None,
        "data_end": db_stats[1] if db_stats else None,
        "data_symbols": db_stats[2] if db_stats else 0,
        "data_rows": db_stats[3] if db_stats else 0,
        "production_model": production[0] if production else None,
        "production_experiment_id": production[1] if production else None,
        "production_published_at": production[2] if production else None,
    }


def _load_lgb_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    params = {
        "loss": "mse",
        "learning_rate": 0.05,
        "num_leaves": 256,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "num_boost_round": 500,
        "early_stopping_rounds": 50,
        "verbosity": -1,
        "seed": 42,
    }
    if overrides:
        params.update({k: v for k, v in overrides.items() if v is not None})
    return params


def _segments_from_config(test_end: str | None = None) -> dict[str, str]:
    cfg = load_config().get("qlib", {})
    return {
        "train_start": cfg.get("train_start", "2019-01-01"),
        "train_end": cfg.get("train_end", "2022-12-31"),
        "valid_start": cfg.get("valid_start", "2023-01-01"),
        "valid_end": cfg.get("valid_end", "2023-12-31"),
        "test_start": cfg.get("test_start", "2024-01-01"),
        "test_end": test_end or cfg.get("test_end") or date.today().strftime("%Y-%m-%d"),
    }


def _insert_experiment(
    experiment_id: str,
    mode: str,
    status: str,
    segments: dict[str, str],
    log_path: str | None = None,
    lgb_params: dict[str, Any] | None = None,
    candidate_spec: dict[str, Any] | None = None,
) -> None:
    from src.data_pipeline.loader import get_connection, init_db

    env = qlib_status("cn")
    cfg = load_config()
    row = {
        "experiment_id": experiment_id,
        "model_name": MODEL_NAME,
        "mode": mode,
        "status": status,
        "market": "CN",
        "train_start": segments.get("train_start"),
        "train_end": segments.get("train_end"),
        "valid_start": segments.get("valid_start"),
        "valid_end": segments.get("valid_end"),
        "test_start": segments.get("test_start"),
        "test_end": segments.get("test_end"),
        "data_start": env.get("data_start"),
        "data_end": env.get("data_end"),
        "data_symbols": env.get("data_symbols"),
        "qlib_installed": env.get("qlib_installed"),
        "qlib_data_ready": env.get("qlib_data_ready"),
        "python_version": env.get("python_version"),
        "qlib_version": env.get("qlib_version"),
        "lightgbm_version": env.get("lightgbm_version"),
        "config_snapshot": _json({
            "qlib": cfg.get("qlib", {}),
            "lgb": _load_lgb_params(lgb_params),
            "candidate": candidate_spec or {},
        }),
        "log_path": log_path,
    }
    conn = get_connection()
    try:
        init_db(conn)
        conn.register("_tmp_qlib_experiment_df", pd.DataFrame([row]))
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_qlib_experiment AS SELECT * FROM _tmp_qlib_experiment_df")
        conn.execute("""
            INSERT OR REPLACE INTO qlib_experiments (
                experiment_id, model_name, mode, status, market,
                train_start, train_end, valid_start, valid_end, test_start, test_end,
                data_start, data_end, data_symbols, qlib_installed, qlib_data_ready,
                python_version, qlib_version, lightgbm_version, config_snapshot, log_path
            )
            SELECT experiment_id, model_name, mode, status, market,
                   train_start, train_end, valid_start, valid_end, test_start, test_end,
                   data_start, data_end, data_symbols, qlib_installed, qlib_data_ready,
                   python_version, qlib_version, lightgbm_version, config_snapshot, log_path
            FROM _tmp_qlib_experiment
        """)
    finally:
        conn.close()


def _finish_experiment(
    experiment_id: str,
    status: str,
    metrics: dict[str, Any] | None = None,
    run_id: str | None = None,
    model_version: str | None = None,
    error_message: str | None = None,
) -> None:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("""
            UPDATE qlib_experiments
            SET status = ?,
                metrics_json = ?,
                run_id = COALESCE(?, run_id),
                model_version = COALESCE(?, model_version),
                error_message = ?,
                ended_at = CURRENT_TIMESTAMP
            WHERE experiment_id = ?
        """, [status, _json(metrics), run_id, model_version, error_message, experiment_id])
    finally:
        conn.close()


def train_alpha158(
    train_start: str = "2019-01-01",
    train_end: str = "2022-12-31",
    valid_start: str = "2023-01-01",
    valid_end: str = "2023-12-31",
    test_start: str = "2024-01-01",
    test_end: str | None = None,
    market: str = "csi800",
    model_output_path: Path | None = None,
    lgb_params: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Train Alpha158 + LightGBM and return predictions plus optional model path."""
    if test_end is None:
        test_end = date.today().strftime("%Y-%m-%d")

    env = _qlib_import_status()
    if not env["qlib_installed"]:
        raise RuntimeError(f"Qlib 未安装或不可导入：{env.get('error')}")
    if not _qlib_data_status("cn")["ready"]:
        raise RuntimeError("Qlib CN 数据未就绪，请先运行 prepare-data")

    import qlib  # type: ignore
    from qlib.contrib.data.handler import Alpha158  # type: ignore
    from qlib.contrib.model.gbdt import LGBModel  # type: ignore
    from qlib.data.dataset import DatasetH  # type: ignore

    global _QLIB_INITIALIZED
    if not _QLIB_INITIALIZED:
        qlib.init(provider_uri=str(PROJECT_ROOT / "qlib_data" / "cn_data"), region="cn")
        _QLIB_INITIALIZED = True
    handler = Alpha158(
        start_time=train_start,
        end_time=test_end,
        fit_start_time=train_start,
        fit_end_time=train_end,
        instruments=market,
    )
    dataset = DatasetH(
        handler=handler,
        segments={
            "train": (train_start, train_end),
            "valid": (valid_start, valid_end),
            "test": (test_start, test_end),
        },
    )
    model = LGBModel(**_load_lgb_params(lgb_params))
    logger.info(f"Training Alpha158 {train_start}~{train_end}, test {test_start}~{test_end}")
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    df = pred.reset_index()
    df.columns = ["datetime", "instrument", "score"]
    df["datetime"] = pd.to_datetime(df["datetime"])

    saved_path = None
    if model_output_path is not None:
        model_output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(model_output_path, "wb") as fh:
            pickle.dump(model, fh)
        saved_path = str(model_output_path)
    return df, saved_path


def _load_price_frame(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    from src.data_pipeline.loader import get_connection

    if not symbols:
        return pd.DataFrame()
    ph = ",".join(["?"] * len(symbols))
    conn = get_connection(read_only=True)
    try:
        prices = conn.execute(f"""
            SELECT symbol, trade_date, open, close, pre_close, pe_ttm, pb, is_st, is_suspended
            FROM daily_price
            WHERE symbol IN ({ph})
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY trade_date, symbol
        """, [*symbols, start, end]).fetchdf()
    finally:
        conn.close()
    if not prices.empty:
        prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    return prices


def compute_daily_ic(pred: pd.DataFrame, prices: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """Compute daily Pearson IC and Rank IC against forward close returns."""
    if pred.empty or prices.empty:
        return pd.DataFrame()
    close = prices.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    fwd = close.pct_change(horizon).shift(-horizon)
    scores = pred.pivot_table(index="datetime", columns="instrument", values="score", aggfunc="mean").sort_index()

    rows = []
    for dt in scores.index.intersection(fwd.index):
        s = scores.loc[dt].dropna()
        r = fwd.loc[dt].dropna()
        common = s.index.intersection(r.index)
        if len(common) < 10:
            continue
        s_common = s[common]
        r_common = r[common]
        top = s_common.nlargest(max(1, int(len(s_common) * 0.2))).index
        bottom = s_common.nsmallest(max(1, int(len(s_common) * 0.2))).index
        ic = s_common.corr(r_common)
        rank_ic = s_common.rank().corr(r_common.rank())
        rows.append({
            "metric_date": pd.to_datetime(dt).date(),
            "prediction_count": int(len(common)),
            "ic": float(ic) if pd.notna(ic) else None,
            "rank_ic": float(rank_ic) if pd.notna(rank_ic) else None,
            "top_return": float(r_common.loc[top].mean()),
            "bottom_return": float(r_common.loc[bottom].mean()),
            "spread_return": float(r_common.loc[top].mean() - r_common.loc[bottom].mean()),
        })
    return pd.DataFrame(rows)


def _open_tradeable_mask(prices: pd.DataFrame, market: str = "CN") -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str)
    for col, default in {"pre_close": None, "is_st": False, "is_suspended": False}.items():
        if col not in df.columns:
            df[col] = default
    df["_open_tradeable"] = df.apply(
        lambda row: check_open_tradeable(
            row.get("open"),
            row.get("pre_close"),
            market=market,
            is_st=bool(row.get("is_st") or False),
            is_suspended=bool(row.get("is_suspended") or False),
        ).tradeable,
        axis=1,
    )
    return (
        df.pivot_table(index="trade_date", columns="symbol", values="_open_tradeable", aggfunc="last")
        .sort_index()
        .fillna(False)
        .astype(bool)
    )


def _round_trip_tradeable(prices: pd.DataFrame, holding_days: int, market: str = "CN") -> pd.DataFrame:
    tradeable = _open_tradeable_mask(prices, market=market)
    if tradeable.empty:
        return pd.DataFrame()
    return (tradeable.shift(-1).fillna(False) & tradeable.shift(-(holding_days + 1)).fillna(False)).astype(bool)


def simulate_topn_t1_open(
    pred: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int = DEFAULT_TOP_N,
    commission_rate: float = 0.00025,
    stamp_duty_rate: float = 0.001,
    market: str = "CN",
) -> pd.Series:
    """T signal -> T+1 open entry -> T+2 open exit, equal-weight Top-N."""
    if pred.empty or prices.empty:
        return pd.Series(dtype=float)
    open_px = prices.pivot(index="trade_date", columns="symbol", values="open").sort_index()
    open_returns = open_px.pct_change().shift(-2)
    tradeable = _round_trip_tradeable(prices, holding_days=1, market=market)

    daily_returns = []
    prev_holdings: set[str] = set()
    turnover_values = {}
    cost_rate = commission_rate + commission_rate + stamp_duty_rate
    for dt, group in pred.groupby("datetime"):
        dt = pd.to_datetime(dt)
        if dt not in open_returns.index:
            continue
        symbols = group.nlargest(top_n, "score")["instrument"].astype(str).tolist()
        available = [
            s for s in symbols
            if s in open_returns.columns
            and pd.notna(open_returns.loc[dt, s])
            and s in tradeable.columns
            and dt in tradeable.index
            and bool(tradeable.loc[dt, s])
        ]
        if not available:
            continue
        gross = float(open_returns.loc[dt, available].mean())
        current_holdings = set(available)
        turnover = 1.0
        if prev_holdings:
            turnover = len(current_holdings.symmetric_difference(prev_holdings)) / max(len(current_holdings | prev_holdings), 1)
        turnover_values[dt] = turnover
        prev_holdings = current_holdings
        daily_returns.append((dt, gross - cost_rate * turnover))
    returns = pd.Series([r for _, r in daily_returns], index=pd.to_datetime([dt for dt, _ in daily_returns]))
    returns.attrs["turnover"] = float(pd.Series(turnover_values).mean() * 252) if turnover_values else None
    return returns


def simulate_topn_open(
    pred: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int,
    holding_days: int,
    rebalance_freq: str = "daily",
    buffer_n: int | None = None,
    commission_rate: float = 0.00025,
    stamp_duty_rate: float = 0.001,
    market: str = "CN",
) -> pd.Series:
    """T signal -> T+1 open entry, hold N trading days, equal-weight with optional turnover buffer."""
    if pred.empty or prices.empty:
        return pd.Series(dtype=float)
    if holding_days < 1:
        raise ValueError("holding_days must be >= 1")

    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    open_px = prices.pivot(index="trade_date", columns="symbol", values="open").sort_index()
    forward_returns = open_px.shift(-(holding_days + 1)) / open_px.shift(-1) - 1
    tradeable = _round_trip_tradeable(prices, holding_days=holding_days, market=market)

    groups = []
    for dt, group in pred.groupby("datetime"):
        groups.append((pd.to_datetime(dt), group))
    if rebalance_freq in {"weekly", "monthly"}:
        sampled = {}
        period_freq = "W-FRI" if rebalance_freq == "weekly" else "M"
        for dt, group in groups:
            sampled[dt.to_period(period_freq)] = (dt, group)
        groups = [sampled[k] for k in sorted(sampled)]
    elif rebalance_freq != "daily":
        raise ValueError("rebalance_freq must be daily, weekly, or monthly")

    current_holdings: set[str] = set()
    turnover_values: dict[pd.Timestamp, float] = {}
    daily_returns = []
    cost_rate = commission_rate + commission_rate + stamp_duty_rate
    buffer_n = max(buffer_n or top_n, top_n)

    for dt, group in groups:
        if dt not in forward_returns.index:
            continue
        ranked = group.sort_values("score", ascending=False)["instrument"].astype(str).tolist()
        ranked = [
            s for s in ranked
            if s in forward_returns.columns
            and pd.notna(forward_returns.loc[dt, s])
            and s in tradeable.columns
            and dt in tradeable.index
            and bool(tradeable.loc[dt, s])
        ]
        if not ranked:
            continue
        keep_pool = set(ranked[:buffer_n])
        kept = [s for s in current_holdings if s in keep_pool]
        for sym in ranked:
            if len(kept) >= top_n:
                break
            if sym not in kept:
                kept.append(sym)
        new_holdings = set(kept[:top_n])
        if not new_holdings:
            continue

        if current_holdings:
            turnover = len(new_holdings.symmetric_difference(current_holdings)) / max(len(new_holdings | current_holdings), 1)
        else:
            turnover = 1.0
        gross_period = float(forward_returns.loc[dt, list(new_holdings)].mean())
        if rebalance_freq == "daily" and gross_period > -1:
            gross = (1 + gross_period) ** (1 / holding_days) - 1
        else:
            gross = gross_period
        daily_returns.append((dt, gross - cost_rate * turnover))
        turnover_values[dt] = turnover
        current_holdings = new_holdings

    returns = pd.Series([r for _, r in daily_returns], index=pd.to_datetime([dt for dt, _ in daily_returns]))
    periods_per_year = {"daily": 252, "weekly": 52, "monthly": 12}[rebalance_freq]
    returns.attrs["turnover"] = float(pd.Series(turnover_values).mean() * periods_per_year) if turnover_values else None
    returns.attrs["periods_per_year"] = periods_per_year
    return returns


def _small_account_profile_name(account_capital: float) -> str:
    return f"small_account_{int(round(float(account_capital) / 1000))}k"


def simulate_topn_open_constrained(
    pred: pd.DataFrame,
    prices: pd.DataFrame,
    top_n: int,
    holding_days: int,
    rebalance_freq: str = "daily",
    buffer_n: int | None = None,
    account_capital: float = 300_000,
    max_position_pct: float = 0.06,
    cash_reserve_pct: float = 0.05,
    lot_size: int = 100,
    commission_rate: float = 0.00025,
    stamp_duty_rate: float = 0.001,
    max_pe_ttm: float | None = None,
    max_pb: float | None = None,
    market: str = "CN",
) -> pd.Series:
    """T signal -> T+1 open execution with account size, lot and concentration constraints."""
    if pred.empty or prices.empty:
        return pd.Series(dtype=float)
    if holding_days < 1:
        raise ValueError("holding_days must be >= 1")
    if account_capital <= 0:
        raise ValueError("account_capital must be positive")
    if not 0 < max_position_pct <= 1:
        raise ValueError("max_position_pct must be in (0, 1]")

    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    open_px = prices.pivot(index="trade_date", columns="symbol", values="open").sort_index()
    forward_returns = open_px.shift(-(holding_days + 1)) / open_px.shift(-1) - 1
    entry_prices = open_px.shift(-1)
    tradeable = _round_trip_tradeable(prices, holding_days=holding_days, market=market)
    pe_frame = (
        prices.pivot(index="trade_date", columns="symbol", values="pe_ttm").sort_index()
        if "pe_ttm" in prices.columns else pd.DataFrame()
    )
    pb_frame = (
        prices.pivot(index="trade_date", columns="symbol", values="pb").sort_index()
        if "pb" in prices.columns else pd.DataFrame()
    )

    groups = [(pd.to_datetime(dt), group) for dt, group in pred.groupby("datetime")]
    if rebalance_freq in {"weekly", "monthly"}:
        sampled = {}
        period_freq = "W-FRI" if rebalance_freq == "weekly" else "M"
        for dt, group in groups:
            sampled[dt.to_period(period_freq)] = (dt, group)
        groups = [sampled[k] for k in sorted(sampled)]
    elif rebalance_freq != "daily":
        raise ValueError("rebalance_freq must be daily, weekly, or monthly")

    buffer_n = max(buffer_n or top_n, top_n)
    investable_cash = account_capital * max(0.0, 1.0 - cash_reserve_pct)
    target_slot_value = investable_cash / max(top_n, 1)
    max_position_value = account_capital * max_position_pct
    cost_rate = commission_rate + commission_rate + stamp_duty_rate

    current_holdings: set[str] = set()
    daily_returns = []
    turnover_values: dict[pd.Timestamp, float] = {}
    selected_counts = []
    cash_drags = []
    max_weights = []
    weight_deviations = []
    skipped_price_caps = []
    skipped_valuations = []
    skipped_budget_counts = []
    skipped_untradeable_counts = []

    for dt, group in groups:
        if dt not in forward_returns.index or dt not in entry_prices.index:
            continue
        ranked = group.sort_values("score", ascending=False)["instrument"].astype(str).tolist()
        ranked = [
            s for s in ranked
            if s in forward_returns.columns
            and s in entry_prices.columns
            and pd.notna(forward_returns.loc[dt, s])
            and pd.notna(entry_prices.loc[dt, s])
        ]
        if not ranked:
            continue

        keep_pool = set(ranked[:buffer_n])
        ordered = [s for s in current_holdings if s in keep_pool]
        ordered.extend([s for s in ranked if s not in ordered])

        positions: dict[str, float] = {}
        spent = 0.0
        skipped_price_cap = 0
        skipped_valuation = 0
        skipped_budget = 0
        skipped_untradeable = 0

        for sym in ordered:
            if len(positions) >= top_n:
                break
            if (
                sym not in tradeable.columns
                or dt not in tradeable.index
                or not bool(tradeable.loc[dt, sym])
            ):
                skipped_untradeable += 1
                continue
            price = float(entry_prices.loc[dt, sym])
            if price <= 0 or not np.isfinite(price):
                continue

            if max_pe_ttm is not None and not pe_frame.empty and dt in pe_frame.index and sym in pe_frame.columns:
                pe = pe_frame.loc[dt, sym]
                if pd.notna(pe) and float(pe) > max_pe_ttm:
                    skipped_valuation += 1
                    continue
            if max_pb is not None and not pb_frame.empty and dt in pb_frame.index and sym in pb_frame.columns:
                pb = pb_frame.loc[dt, sym]
                if pd.notna(pb) and float(pb) > max_pb:
                    skipped_valuation += 1
                    continue

            lot_value = price * lot_size
            if lot_value > max_position_value + 1e-9:
                skipped_price_cap += 1
                continue

            desired_value = min(max_position_value, max(target_slot_value, lot_value))
            quantity = int(desired_value // lot_value) * lot_size
            if quantity <= 0:
                continue
            order_value = quantity * price
            entry_fee = order_value * commission_rate
            if spent + order_value + entry_fee > investable_cash + 1e-9:
                skipped_budget += 1
                continue
            positions[sym] = order_value
            spent += order_value + entry_fee

        if not positions:
            continue

        new_holdings = set(positions)
        if current_holdings:
            turnover = len(new_holdings.symmetric_difference(current_holdings)) / max(len(new_holdings | current_holdings), 1)
        else:
            turnover = 1.0
        invested_weight = sum(positions.values()) / account_capital
        gross_period = float(sum(
            (value / account_capital) * float(forward_returns.loc[dt, sym])
            for sym, value in positions.items()
        ))
        net_period = gross_period - cost_rate * turnover * invested_weight
        if rebalance_freq == "daily" and net_period > -1:
            net = (1 + net_period) ** (1 / holding_days) - 1
        else:
            net = net_period

        ideal_weight = (1.0 - cash_reserve_pct) / max(top_n, 1)
        weights = [value / account_capital for value in positions.values()]
        daily_returns.append((dt, net))
        turnover_values[dt] = turnover
        selected_counts.append(len(positions))
        cash_drags.append(max(0.0, 1.0 - invested_weight))
        max_weights.append(max(weights))
        weight_deviations.append(float(np.mean([abs(w - ideal_weight) for w in weights])) if weights else 0.0)
        skipped_price_caps.append(skipped_price_cap)
        skipped_valuations.append(skipped_valuation)
        skipped_budget_counts.append(skipped_budget)
        skipped_untradeable_counts.append(skipped_untradeable)
        current_holdings = new_holdings

    returns = pd.Series([r for _, r in daily_returns], index=pd.to_datetime([dt for dt, _ in daily_returns]))
    periods_per_year = {"daily": 252, "weekly": 52, "monthly": 12}[rebalance_freq]
    returns.attrs["turnover"] = float(pd.Series(turnover_values).mean() * periods_per_year) if turnover_values else None
    returns.attrs["periods_per_year"] = periods_per_year
    returns.attrs["constraint_profile"] = _small_account_profile_name(account_capital)
    returns.attrs["account_capital"] = float(account_capital)
    returns.attrs["max_position_pct"] = float(max_position_pct)
    returns.attrs["cash_reserve_pct"] = float(cash_reserve_pct)
    returns.attrs["lot_size"] = int(lot_size)
    returns.attrs["avg_selected_count"] = float(np.mean(selected_counts)) if selected_counts else 0.0
    returns.attrs["min_selected_count"] = int(min(selected_counts)) if selected_counts else 0
    returns.attrs["avg_cash_drag"] = float(np.mean(cash_drags)) if cash_drags else None
    returns.attrs["max_actual_position_pct"] = float(max(max_weights)) if max_weights else None
    returns.attrs["avg_weight_deviation"] = float(np.mean(weight_deviations)) if weight_deviations else None
    returns.attrs["skipped_price_cap_avg"] = float(np.mean(skipped_price_caps)) if skipped_price_caps else 0.0
    returns.attrs["skipped_valuation_avg"] = float(np.mean(skipped_valuations)) if skipped_valuations else 0.0
    returns.attrs["skipped_budget_avg"] = float(np.mean(skipped_budget_counts)) if skipped_budget_counts else 0.0
    returns.attrs["skipped_untradeable_avg"] = float(np.mean(skipped_untradeable_counts)) if skipped_untradeable_counts else 0.0
    returns.attrs["max_pe_ttm"] = float(max_pe_ttm) if max_pe_ttm is not None else None
    returns.attrs["max_pb"] = float(max_pb) if max_pb is not None else None
    return returns


def compute_periodic_metrics(
    returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.03,
    turnover: float | None = None,
) -> dict[str, Any]:
    returns = pd.Series(returns).dropna().sort_index()
    if returns.empty:
        return {}
    cumulative = (1 + returns).cumprod()
    years = max(len(returns) / periods_per_year, 1 / periods_per_year)
    cumulative_return = float(cumulative.iloc[-1] - 1)
    annual_return = float(cumulative.iloc[-1] ** (1 / years) - 1)
    annual_vol = float(returns.std() * np.sqrt(periods_per_year)) if len(returns) > 1 else 0.0
    sharpe = float((annual_return - risk_free_rate) / annual_vol) if annual_vol > 0 else 0.0
    peak = cumulative.expanding().max()
    drawdown = (cumulative - peak) / peak

    benchmark_return = None
    info_ratio = None
    excess_return = None
    if benchmark_returns is not None and not benchmark_returns.empty:
        bench = pd.Series(benchmark_returns).dropna().sort_index()
        common = returns.index.intersection(bench.index)
        if len(common) > 1:
            bench_common = bench.loc[common]
            bench_cum = (1 + bench_common).cumprod()
            bench_years = max(len(bench_common) / periods_per_year, 1 / periods_per_year)
            benchmark_return = float(bench_cum.iloc[-1] ** (1 / bench_years) - 1)
            active = returns.loc[common] - bench_common
            active_vol = float(active.std() * np.sqrt(periods_per_year))
            info_ratio = float(active.mean() * periods_per_year / active_vol) if active_vol > 0 else 0.0
            excess_return = annual_return - benchmark_return
    return {
        "start_date": pd.to_datetime(returns.index.min()).date(),
        "end_date": pd.to_datetime(returns.index.max()).date(),
        "annual_return": annual_return,
        "cumulative_return": cumulative_return,
        "annual_volatility": annual_vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": float(drawdown.min()),
        "turnover": float(turnover) if turnover is not None else None,
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
        "info_ratio": info_ratio,
    }


def align_benchmark_to_strategy_periods(
    benchmark_returns: pd.Series,
    signal_dates: pd.DatetimeIndex,
    holding_days: int,
    rebalance_freq: str,
) -> pd.Series:
    if benchmark_returns.empty or len(signal_dates) == 0:
        return pd.Series(dtype=float)
    nav = (1 + benchmark_returns.dropna().sort_index()).cumprod()
    period = nav.shift(-(holding_days + 1)) / nav.shift(-1) - 1
    aligned = period.reindex(pd.to_datetime(signal_dates)).dropna()
    if rebalance_freq == "daily":
        aligned = aligned.map(lambda x: (1 + x) ** (1 / holding_days) - 1 if x > -1 else x)
    return aligned


def _valid_rebalance_horizon(rebalance_freq: str, holding_days: int) -> bool:
    """Avoid overlapping periodic windows in the simple single-cohort grid backtest."""
    return not (rebalance_freq == "weekly" and holding_days > 5)


def load_all_stock_equal_weight_returns() -> pd.Series:
    """Proxy for 中证全指 when the real index is not available locally."""
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    try:
        df = conn.execute("""
            SELECT dp.trade_date, dp.symbol, dp.close
            FROM daily_price dp
            JOIN stock_info si ON dp.symbol = si.symbol
            WHERE si.country = 'CN'
            ORDER BY dp.trade_date, dp.symbol
        """).fetchdf()
    finally:
        conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close = df.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    return close.pct_change(fill_method=None).mean(axis=1).dropna()


def load_benchmark_suite() -> dict[str, pd.Series]:
    bench_300 = load_benchmark_returns("000300")
    bench_500 = load_benchmark_returns("000905")
    all_proxy = load_all_stock_equal_weight_returns()
    suite = {
        "000300": bench_300,
        "000905": bench_500,
        "ALL_EQ_PROXY": all_proxy,
    }
    common = bench_300.index.intersection(bench_500.index).intersection(all_proxy.index)
    if len(common) > 1:
        suite["MIXED_EQUAL"] = pd.concat(
            [bench_300.loc[common], bench_500.loc[common], all_proxy.loc[common]],
            axis=1,
        ).mean(axis=1)
    return {k: v.dropna() for k, v in suite.items() if v is not None and not v.empty}


def _parse_int_grid(value: str) -> list[int]:
    result: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = [int(x.strip()) for x in part.split("-", 1)]
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def _parse_float_grid(value: str) -> list[float]:
    result: list[float] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        result.append(float(part))
    return result


def small_account_profiles(
    capitals: list[float] | None = None,
    max_pe_ttm: float | None = 120.0,
    max_pb: float | None = None,
) -> list[dict[str, Any]]:
    profiles = []
    if capitals is None:
        base_profiles = DEFAULT_SMALL_ACCOUNT_PROFILES
    else:
        base_profiles = [{"account_capital": capital} for capital in capitals]
    for base in base_profiles:
        capital = base["account_capital"]
        capital = float(capital)
        max_position_pct = float(base.get("max_position_pct") or (0.06 if capital <= 300_000 else 0.05))
        profiles.append({
            "account_capital": capital,
            "max_position_pct": max_position_pct,
            "cash_reserve_pct": float(base.get("cash_reserve_pct") or 0.05),
            "lot_size": int(base.get("lot_size") or 100),
            "max_pe_ttm": max_pe_ttm if max_pe_ttm is not None else base.get("max_pe_ttm"),
            "max_pb": max_pb if max_pb is not None else base.get("max_pb"),
        })
    return profiles


def evaluate_parameter_grid(
    experiment_id: str,
    top_ns: list[int],
    holding_days: list[int],
    rebalance_freqs: list[str],
    buffer_mult: float = 1.5,
    account_profiles: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        meta = conn.execute("""
            SELECT experiment_id, mode, model_name
            FROM qlib_experiments
            WHERE experiment_id = ?
        """, [experiment_id]).fetchone()
        if not meta:
            raise ValueError(f"找不到实验: {experiment_id}")
        pred = conn.execute("""
            SELECT prediction_date AS datetime, symbol AS instrument, score
            FROM qlib_predictions
            WHERE experiment_id = ?
            ORDER BY prediction_date, symbol
        """, [experiment_id]).fetchdf()
    finally:
        conn.close()
    if pred.empty:
        raise ValueError(f"实验没有预测截面: {experiment_id}")

    pred["datetime"] = pd.to_datetime(pred["datetime"])
    symbols = pred["instrument"].astype(str).unique().tolist()
    start = pred["datetime"].min().strftime("%Y-%m-%d")
    end = (pred["datetime"].max() + pd.Timedelta(days=max(holding_days) + 10)).strftime("%Y-%m-%d")
    prices = _load_price_frame(symbols, start, end)
    benchmarks = load_benchmark_suite()
    rows = []
    for top_n in top_ns:
        buffer_n = int(np.ceil(top_n * buffer_mult))
        for h in holding_days:
            for freq in rebalance_freqs:
                if not _valid_rebalance_horizon(freq, h):
                    continue
                returns = simulate_topn_open(
                    pred,
                    prices,
                    top_n=top_n,
                    holding_days=h,
                    rebalance_freq=freq,
                    buffer_n=buffer_n,
                )
                if returns.empty:
                    continue
                for bench_name, bench_returns in benchmarks.items():
                    aligned_benchmark = align_benchmark_to_strategy_periods(
                        bench_returns,
                        pd.DatetimeIndex(returns.index),
                        holding_days=h,
                        rebalance_freq=freq,
                    )
                    metrics = compute_periodic_metrics(
                        returns,
                        benchmark_returns=aligned_benchmark,
                        periods_per_year=int(returns.attrs.get("periods_per_year", 252)),
                        turnover=returns.attrs.get("turnover"),
                    )
                    if not metrics:
                        continue
                    row = {
                        "grid_id": f"GRID-{uuid.uuid4().hex[:10].upper()}",
                        "source_experiment_id": experiment_id,
                        "model_name": MODEL_NAME,
                        "mode": meta[1],
                        "top_n": top_n,
                        "holding_days": h,
                        "rebalance_freq": freq,
                        "buffer_n": buffer_n,
                        "benchmark_name": bench_name,
                        "constraint_profile": "theoretical_equal_weight",
                        "account_capital": None,
                        "max_position_pct": None,
                        "cash_reserve_pct": None,
                        "lot_size": None,
                        "avg_selected_count": None,
                        "min_selected_count": None,
                        "avg_cash_drag": None,
                        "max_actual_position_pct": None,
                        "avg_weight_deviation": None,
                        "skipped_price_cap_avg": None,
                        "skipped_valuation_avg": None,
                        "skipped_budget_avg": None,
                        "max_pe_ttm": None,
                        "max_pb": None,
                        **metrics,
                        "metrics_json": _json(metrics),
                    }
                    rows.append(row)
                for profile in account_profiles or []:
                    constrained_returns = simulate_topn_open_constrained(
                        pred,
                        prices,
                        top_n=top_n,
                        holding_days=h,
                        rebalance_freq=freq,
                        buffer_n=buffer_n,
                        account_capital=float(profile.get("account_capital", 300_000)),
                        max_position_pct=float(profile.get("max_position_pct", 0.06)),
                        cash_reserve_pct=float(profile.get("cash_reserve_pct", 0.05)),
                        lot_size=int(profile.get("lot_size", 100)),
                        max_pe_ttm=profile.get("max_pe_ttm"),
                        max_pb=profile.get("max_pb"),
                    )
                    if constrained_returns.empty:
                        continue
                    attrs = constrained_returns.attrs
                    for bench_name, bench_returns in benchmarks.items():
                        aligned_benchmark = align_benchmark_to_strategy_periods(
                            bench_returns,
                            pd.DatetimeIndex(constrained_returns.index),
                            holding_days=h,
                            rebalance_freq=freq,
                        )
                        metrics = compute_periodic_metrics(
                            constrained_returns,
                            benchmark_returns=aligned_benchmark,
                            periods_per_year=int(attrs.get("periods_per_year", 252)),
                            turnover=attrs.get("turnover"),
                        )
                        if not metrics:
                            continue
                        executable_metrics = {
                            "constraint_profile": attrs.get("constraint_profile"),
                            "account_capital": attrs.get("account_capital"),
                            "max_position_pct": attrs.get("max_position_pct"),
                            "cash_reserve_pct": attrs.get("cash_reserve_pct"),
                            "lot_size": attrs.get("lot_size"),
                            "avg_selected_count": attrs.get("avg_selected_count"),
                            "min_selected_count": attrs.get("min_selected_count"),
                            "avg_cash_drag": attrs.get("avg_cash_drag"),
                            "max_actual_position_pct": attrs.get("max_actual_position_pct"),
                            "avg_weight_deviation": attrs.get("avg_weight_deviation"),
                            "skipped_price_cap_avg": attrs.get("skipped_price_cap_avg"),
                            "skipped_valuation_avg": attrs.get("skipped_valuation_avg"),
                            "skipped_budget_avg": attrs.get("skipped_budget_avg"),
                            "max_pe_ttm": attrs.get("max_pe_ttm"),
                            "max_pb": attrs.get("max_pb"),
                        }
                        row = {
                            "grid_id": f"GRID-{uuid.uuid4().hex[:10].upper()}",
                            "source_experiment_id": experiment_id,
                            "model_name": MODEL_NAME,
                            "mode": meta[1],
                            "top_n": top_n,
                            "holding_days": h,
                            "rebalance_freq": freq,
                            "buffer_n": buffer_n,
                            "benchmark_name": bench_name,
                            **executable_metrics,
                            **metrics,
                            "metrics_json": _json({**metrics, **executable_metrics}),
                        }
                        rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty:
        return result

    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("DELETE FROM qlib_grid_results WHERE source_experiment_id = ?", [experiment_id])
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_qlib_grid_results AS SELECT * FROM result")
        cols = [
            "grid_id", "source_experiment_id", "model_name", "mode", "top_n",
            "holding_days", "rebalance_freq", "buffer_n", "benchmark_name",
            "constraint_profile", "account_capital", "max_position_pct",
            "cash_reserve_pct", "lot_size", "avg_selected_count",
            "min_selected_count", "avg_cash_drag", "max_actual_position_pct",
            "avg_weight_deviation", "skipped_price_cap_avg",
            "skipped_valuation_avg", "skipped_budget_avg", "max_pe_ttm", "max_pb",
            "start_date", "end_date", "annual_return", "cumulative_return",
            "annual_volatility", "sharpe_ratio", "max_drawdown", "turnover",
            "benchmark_return", "excess_return", "metrics_json",
        ]
        conn.execute(f"""
            INSERT OR REPLACE INTO qlib_grid_results ({", ".join(cols)})
            SELECT {", ".join(cols)} FROM _tmp_qlib_grid_results
        """)
    finally:
        conn.close()
    return result


def default_candidate_specs(include_unavailable: bool = True) -> list[dict[str, Any]]:
    """Return the nightly Qlib candidate catalog.

    The first batch keeps execution realistic for a local nightly run: Alpha158
    with LightGBM parameter variants are runnable today. Other model families
    are listed as skipped candidates so the experiment record still captures
    what should be enabled next when dependencies/adapters are ready.
    """
    lgb_variants = [
        ("lgb_baseline", "baseline", {}),
        ("lgb_conservative", "conservative", {
            "learning_rate": 0.03,
            "num_leaves": 64,
            "feature_fraction": 0.70,
            "bagging_fraction": 0.70,
            "num_boost_round": 800,
            "early_stopping_rounds": 80,
            "seed": 42,
        }),
        ("lgb_balanced", "balanced", {
            "learning_rate": 0.05,
            "num_leaves": 128,
            "feature_fraction": 0.80,
            "bagging_fraction": 0.80,
            "num_boost_round": 600,
            "early_stopping_rounds": 60,
            "seed": 42,
        }),
        ("lgb_deep", "deep", {
            "learning_rate": 0.03,
            "num_leaves": 256,
            "feature_fraction": 0.90,
            "bagging_fraction": 0.80,
            "num_boost_round": 1000,
            "early_stopping_rounds": 100,
            "seed": 42,
        }),
        ("lgb_fast_shallow", "fast_shallow", {
            "learning_rate": 0.08,
            "num_leaves": 32,
            "feature_fraction": 0.70,
            "bagging_fraction": 0.70,
            "num_boost_round": 400,
            "early_stopping_rounds": 40,
            "seed": 42,
        }),
    ]
    candidates = [
        {
            "candidate_id": candidate_id,
            "model_family": "lgbm",
            "model_variant": variant,
            "status": "READY",
            "params": _load_lgb_params(overrides),
        }
        for candidate_id, variant, overrides in lgb_variants
    ]
    skipped = [
        {
            "candidate_id": "xgb_alpha158",
            "model_family": "xgboost",
            "model_variant": "xgboost_alpha158",
            "status": "SKIPPED",
            "skip_reason": "候选已列入；当前批跑入口暂未接入 XGBoost 训练适配器",
            "params": {"objective": "reg:squarederror", "eta": 0.05, "max_depth": 6, "subsample": 0.8},
        },
        {
            "candidate_id": "catboost_alpha158",
            "model_family": "catboost",
            "model_variant": "catboost_alpha158",
            "status": "SKIPPED",
            "skip_reason": "候选已列入；当前批跑入口暂未接入 CatBoost 训练适配器",
            "params": {"loss_function": "RMSE", "learning_rate": 0.05, "depth": 6, "iterations": 600},
        },
        {
            "candidate_id": "mlp_alpha158",
            "model_family": "pytorch",
            "model_variant": "mlp_alpha158",
            "status": "SKIPPED",
            "skip_reason": "候选已列入；当前批跑入口暂未接入 PyTorch MLP 训练适配器",
            "params": {"hidden_units": [256, 128], "dropout": 0.2, "epochs": 30},
        },
    ]
    return candidates + skipped if include_unavailable else candidates


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def score_candidate_grid_row(row: Any, turnover_profile: str = "low") -> float:
    """Score a grid row for a medium/long-term production style.

    Excess return matters, but very high turnover and deep drawdowns are
    explicitly penalized so a daily high-churn result does not dominate the
    nightly selection just because its headline annual return is high.
    """
    get = row.get if hasattr(row, "get") else lambda key, default=None: default
    excess = _safe_float(get("excess_return"))
    sharpe = _safe_float(get("sharpe_ratio"))
    max_drawdown = min(_safe_float(get("max_drawdown")), 0.0)
    turnover = max(_safe_float(get("turnover")), 0.0)
    monthly_bonus = 0.02 if get("rebalance_freq") == "monthly" else 0.0
    constraint_profile = str(get("constraint_profile") or "theoretical_equal_weight")
    executable_bonus = 0.0
    executable_penalty = 0.0
    if constraint_profile.startswith("small_account"):
        avg_selected = _safe_float(get("avg_selected_count"))
        cash_drag = max(_safe_float(get("avg_cash_drag")), 0.0)
        max_weight = max(_safe_float(get("max_actual_position_pct")), 0.0)
        executable_bonus = 0.02
        if avg_selected < 15:
            executable_penalty += (15 - avg_selected) * 0.003
        executable_penalty += cash_drag * 0.08
        if max_weight > 0.08:
            executable_penalty += (max_weight - 0.08) * 1.5
    base = excess + 0.05 * sharpe - 0.20 * abs(max_drawdown) + monthly_bonus + executable_bonus - executable_penalty
    if turnover_profile == "medium":
        target_low, target_high = 30.0, 50.0
        if turnover < target_low:
            turnover_penalty = (target_low - turnover) * 0.0015
            turnover_bonus = 0.0
        elif turnover > target_high:
            turnover_penalty = (turnover - target_high) * 0.0030
            turnover_bonus = 0.0
        else:
            turnover_penalty = 0.0
            turnover_bonus = 0.025
        return base + turnover_bonus - turnover_penalty
    return excess + 0.05 * sharpe - 0.20 * abs(max_drawdown) - 0.002 * turnover + monthly_bonus


def select_best_candidate_grid(
    grid: pd.DataFrame,
    benchmark_name: str = "MIXED_EQUAL",
    turnover_profile: str = "low",
) -> dict[str, Any]:
    if grid.empty:
        return {}
    pool = grid[grid["benchmark_name"] == benchmark_name].copy()
    if pool.empty:
        pool = grid.copy()
    if "constraint_profile" in pool.columns:
        constrained = pool[
            pool["constraint_profile"].fillna("theoretical_equal_weight").astype(str).str.startswith("small_account")
        ].copy()
        if not constrained.empty:
            pool = constrained
    pool["score"] = pool.apply(lambda row: score_candidate_grid_row(row, turnover_profile=turnover_profile), axis=1)
    pool = pool.sort_values(
        ["score", "excess_return", "sharpe_ratio"],
        ascending=[False, False, False],
        na_position="last",
    )
    return pool.iloc[0].to_dict()


def save_candidate_result(conn: Any, row: dict[str, Any]) -> None:
    columns = [
        "candidate_id", "batch_id", "experiment_id", "model_name", "model_family",
        "model_variant", "status", "mode", "params_json", "grid_json",
        "best_benchmark", "best_top_n", "best_holding_days", "best_rebalance_freq",
        "best_buffer_n", "best_constraint_profile", "best_account_capital",
        "avg_selected_count", "avg_cash_drag", "max_actual_position_pct",
        "annual_return", "sharpe_ratio", "max_drawdown",
        "turnover", "benchmark_return", "excess_return", "ic_mean", "icir",
        "rank_ic_mean", "rank_ic_positive_rate", "score", "error_message",
        "started_at", "ended_at",
    ]
    payload = {col: row.get(col) for col in columns}
    placeholders = ", ".join(["?"] * len(columns))
    conn.execute(
        f"INSERT OR REPLACE INTO qlib_candidate_results ({', '.join(columns)}) VALUES ({placeholders})",
        [payload[col] for col in columns],
    )


def _save_candidate_result_to_db(row: dict[str, Any]) -> None:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        save_candidate_result(conn, row)
    finally:
        conn.close()


def evaluate_predictions(
    experiment_id: str,
    pred: pd.DataFrame,
    mode: str,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Persist predictions and daily metrics, return summary metrics."""
    if pred.empty:
        return {}
    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    pred["instrument"] = pred["instrument"].astype(str)
    symbols = pred["instrument"].dropna().unique().tolist()
    start = pred["datetime"].min().strftime("%Y-%m-%d")
    end = (pred["datetime"].max() + pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    prices = _load_price_frame(symbols, start, end)
    if prices.empty:
        return {}

    ic_df = compute_daily_ic(pred, prices)
    returns = simulate_topn_t1_open(pred, prices, top_n=top_n)
    benchmark_suite = load_benchmark_suite()
    primary_benchmark_name = "MIXED_EQUAL" if "MIXED_EQUAL" in benchmark_suite else "000300"
    primary_benchmark = benchmark_suite.get(primary_benchmark_name)
    metrics = compute_metrics(
        returns,
        benchmark_returns=primary_benchmark,
        turnover=returns.attrs.get("turnover"),
    )
    benchmark_metrics = {}
    for bench_name, bench_returns in benchmark_suite.items():
        bench_metrics = compute_metrics(
            returns,
            benchmark_returns=bench_returns,
            turnover=returns.attrs.get("turnover"),
        )
        benchmark_metrics[bench_name] = {
            "benchmark_return": bench_metrics.get("benchmark_return"),
            "excess_return": bench_metrics.get("excess_return"),
            "info_ratio": bench_metrics.get("info_ratio"),
        }
    metrics["benchmark_suite"] = benchmark_metrics
    metrics["primary_benchmark"] = primary_benchmark_name
    if ic_df.empty:
        metrics.update({"ic_mean": None, "icir": None, "rank_ic_mean": None, "rank_ic_positive_rate": None})
    else:
        rank = ic_df["rank_ic"].dropna()
        ic = ic_df["ic"].dropna()
        metrics.update({
            "ic_mean": float(ic.mean()) if not ic.empty else None,
            "icir": float(ic.mean() / ic.std()) if len(ic) > 1 and ic.std() > 0 else 0.0,
            "rank_ic_mean": float(rank.mean()) if not rank.empty else None,
            "rank_ic_positive_rate": float((rank > 0).mean()) if not rank.empty else None,
        })

    latest_dates = pred["datetime"]
    pred["prediction_date"] = latest_dates.dt.date
    pred["rank"] = pred.groupby("prediction_date")["score"].rank(ascending=False, method="first").astype(int)
    pred["selected"] = pred["rank"] <= top_n
    pred["confidence"] = pred.groupby("prediction_date")["score"].transform(_minmax_confidence)
    pred_out = pred.rename(columns={"instrument": "symbol"})[
        ["prediction_date", "symbol", "score", "rank", "confidence", "selected"]
    ].copy()
    pred_out["experiment_id"] = experiment_id
    pred_out["model_name"] = MODEL_NAME
    pred_out["model_version"] = _model_version(experiment_id)
    pred_out["mode"] = mode

    if not ic_df.empty:
        ic_out = ic_df.copy()
        ic_out["experiment_id"] = experiment_id
        ic_out["mode"] = mode
        if not returns.empty:
            ret_map = {d.date(): float(v) for d, v in returns.items()}
            ic_out["portfolio_return"] = ic_out["metric_date"].map(ret_map)
        bench = primary_benchmark
        if not bench.empty:
            bench_map = {d.date(): float(v) for d, v in bench.items()}
            ic_out["benchmark_return"] = ic_out["metric_date"].map(bench_map)
        ic_out["turnover"] = returns.attrs.get("turnover")
    else:
        ic_out = pd.DataFrame()

    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_qlib_predictions AS SELECT * FROM pred_out")
        conn.execute("""
            INSERT OR REPLACE INTO qlib_predictions (
                experiment_id, model_name, model_version, mode, prediction_date,
                symbol, score, rank, confidence, selected
            )
            SELECT experiment_id, model_name, model_version, mode, prediction_date,
                   symbol, score, rank, confidence, selected
            FROM _tmp_qlib_predictions
        """)
        if not ic_out.empty:
            conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_qlib_daily_metrics AS SELECT * FROM ic_out")
            conn.execute("""
                INSERT OR REPLACE INTO qlib_daily_metrics (
                    experiment_id, metric_date, mode, prediction_count, ic, rank_ic,
                    top_return, bottom_return, spread_return, portfolio_return,
                    benchmark_return, turnover
                )
                SELECT experiment_id, metric_date, mode, prediction_count, ic, rank_ic,
                       top_return, bottom_return, spread_return, portfolio_return,
                       benchmark_return, turnover
                FROM _tmp_qlib_daily_metrics
            """)
    finally:
        conn.close()
    return metrics


def _latest_covered_cn_data_date(conn: Any, min_coverage: float = 0.80) -> date | None:
    """Return the latest CN date with enough cross-section coverage for inference."""
    rows = conn.execute("""
        SELECT dp.trade_date, COUNT(DISTINCT dp.symbol) AS symbols
        FROM daily_price dp
        JOIN stock_info si ON si.symbol = dp.symbol
        WHERE si.country = 'CN'
        GROUP BY dp.trade_date
        ORDER BY dp.trade_date DESC
    """).fetchall()
    if not rows:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
        return row[0] if row and row[0] is not None else None
    total = max(int(symbols or 0) for _, symbols in rows)
    if total <= 0:
        return None
    threshold = max(float(min_coverage or 0), 0.0)
    for trade_date, symbols in rows:
        if float(symbols or 0) / total >= threshold:
            return trade_date
    return None


def _persist_prediction_frame(
    conn: Any,
    experiment_id: str,
    model_version: str,
    mode: str,
    pred: pd.DataFrame,
    top_n: int = DEFAULT_TOP_N,
) -> int:
    """Persist a prediction frame without requiring forward-return evaluation data."""
    if pred.empty:
        return 0
    pred_out = pred.copy()
    pred_out["datetime"] = pd.to_datetime(pred_out["datetime"])
    pred_out["instrument"] = pred_out["instrument"].astype(str)
    pred_out["prediction_date"] = pred_out["datetime"].dt.date
    pred_out["rank"] = (
        pred_out.groupby("prediction_date")["score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    pred_out["selected"] = pred_out["rank"] <= max(int(top_n or 1), 1)
    pred_out["confidence"] = pred_out.groupby("prediction_date")["score"].transform(_minmax_confidence)
    pred_out = pred_out.rename(columns={"instrument": "symbol"})[
        ["prediction_date", "symbol", "score", "rank", "confidence", "selected"]
    ].copy()
    pred_out["experiment_id"] = experiment_id
    pred_out["model_name"] = MODEL_NAME
    pred_out["model_version"] = model_version
    pred_out["mode"] = mode

    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_qlib_predictions AS SELECT * FROM pred_out")
    conn.execute("""
        INSERT OR REPLACE INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        SELECT experiment_id, model_name, model_version, mode, prediction_date,
               symbol, score, rank, confidence, selected
        FROM _tmp_qlib_predictions
    """)
    return len(pred_out)


def _minmax_confidence(values: pd.Series) -> pd.Series:
    min_v = values.min()
    max_v = values.max()
    if pd.isna(min_v) or pd.isna(max_v) or max_v <= min_v:
        return pd.Series(0.5, index=values.index)
    return (values - min_v) / (max_v - min_v)


def _register_candidate(experiment_id: str, model_version: str, metrics: dict[str, Any], model_path: str | None) -> None:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        conn.execute("""
            INSERT OR REPLACE INTO qlib_model_registry (
                model_version, experiment_id, model_name, status, market, model_path, metrics_json
            )
            VALUES (?, ?, ?, 'candidate', 'CN', ?, ?)
        """, [model_version, experiment_id, MODEL_NAME, model_path, _json(metrics)])
    finally:
        conn.close()


def _passes_publish_gate(metrics: dict[str, Any]) -> tuple[bool, str]:
    ic = metrics.get("ic_mean")
    icir = metrics.get("icir")
    max_dd = metrics.get("max_drawdown")
    excess = metrics.get("excess_return")
    failures = []
    if ic is None or ic <= 0:
        failures.append("IC Mean <= 0")
    if icir is None or icir <= 0:
        failures.append("ICIR <= 0")
    if max_dd is None or max_dd < -0.60:
        failures.append("最大回撤低于 -60%")
    if excess is not None and excess < -0.05:
        failures.append("相对基准年化劣化超过 5%")
    return not failures, "；".join(failures)


def _rebalance_period_expr(rebalance_freq: str) -> str:
    freq = str(rebalance_freq or "daily").lower()
    if freq == "monthly":
        return "strftime(signal_ts, '%Y-%m')"
    if freq == "weekly":
        return "strftime(signal_ts, '%G-W%V')"
    return ""


def _rebalance_period_value(prediction_date: date, rebalance_freq: str) -> str:
    ts = pd.Timestamp(prediction_date)
    freq = str(rebalance_freq or "daily").lower()
    if freq == "monthly":
        return ts.strftime("%Y-%m")
    if freq == "weekly":
        iso = ts.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return ts.strftime("%Y-%m-%d")


def _should_skip_rebalance_signal_write(
    conn: Any,
    model_version: str,
    prediction_date: date,
    rebalance_freq: str,
) -> tuple[bool, str]:
    """Return whether production prediction already wrote signals for this rebalance period."""
    freq = str(rebalance_freq or "daily").lower()
    period_expr = _rebalance_period_expr(freq)
    if not period_expr:
        return False, ""
    period = _rebalance_period_value(prediction_date, freq)
    existing = conn.execute(f"""
        SELECT COUNT(*), MAX(signal_ts)
        FROM signals
        WHERE model_name = ?
          AND model_version = ?
          AND {period_expr} = ?
    """, [MODEL_NAME, model_version, period]).fetchone()
    count = int(existing[0] or 0) if existing else 0
    if count <= 0:
        return False, ""
    return True, f"{freq} rebalance period {period} already has {count} production signals; latest={existing[1]}"


def _production_inference_segments(
    mode: str,
    base_segments: dict[str, Any],
    latest_data: date,
) -> dict[str, str]:
    latest = pd.Timestamp(latest_data)
    if mode == "walk_forward":
        year = latest.year
        return {
            "train_start": str(base_segments.get("train_start") or "2019-01-01"),
            "train_end": f"{year - 2}-12-31",
            "valid_start": f"{year - 1}-01-01",
            "valid_end": f"{year - 1}-12-31",
            "test_start": f"{year}-01-01",
            "test_end": latest.strftime("%Y-%m-%d"),
        }
    return {
        "train_start": str(base_segments.get("train_start") or "2019-01-01"),
        "train_end": str(base_segments.get("train_end") or "2022-12-31"),
        "valid_start": str(base_segments.get("valid_start") or "2023-01-01"),
        "valid_end": str(base_segments.get("valid_end") or "2023-12-31"),
        "test_start": str(base_segments.get("test_start") or "2024-01-01"),
        "test_end": latest.strftime("%Y-%m-%d"),
    }


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _production_artifact_paths(model_version: str) -> dict[str, Path]:
    root = PROJECT_ROOT / "models" / "qlib" / "production"
    return {
        "model": root / f"{model_version}-latest.pkl",
        "manifest": root / f"{model_version}-manifest.json",
    }


def _write_production_manifest(
    model_version: str,
    payload: dict[str, Any],
) -> str:
    paths = _production_artifact_paths(model_version)
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    data = {
        "model_version": model_version,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    paths["manifest"].write_text(
        json.dumps(data, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )
    return str(paths["manifest"])


def _qlib_calendar_latest_date(market: str = "cn") -> date | None:
    calendar = PROJECT_ROOT / "qlib_data" / f"{market}_data" / "calendars" / "day.txt"
    if not calendar.exists():
        return None
    lines = [line.strip() for line in calendar.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return pd.Timestamp(lines[-1]).date()
    except Exception:
        return None


def _ensure_qlib_data_covers(latest_data: date) -> None:
    qlib_latest = _qlib_calendar_latest_date("cn")
    if qlib_latest is not None and qlib_latest >= latest_data:
        return
    rc = prepare_data("cn")
    if rc != 0:
        raise RuntimeError(f"Qlib 数据准备失败，exit={rc}")


def refresh_production_predictions(force: bool = False, min_coverage: float = 0.80) -> dict[str, Any]:
    """Refresh production predictions to the latest coverage-qualified CN date."""
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        row = conn.execute("""
            SELECT r.model_version, r.experiment_id, r.model_path,
                   e.mode, e.train_start, e.train_end, e.valid_start, e.valid_end,
                   e.test_start, e.test_end, e.config_snapshot,
                   c.params_json, c.best_top_n
            FROM qlib_model_registry r
            LEFT JOIN qlib_experiments e ON e.experiment_id = r.experiment_id
            LEFT JOIN qlib_candidate_results c
              ON c.experiment_id = r.experiment_id
             AND c.status = 'SUCCEEDED'
            WHERE r.model_name = ? AND r.status = 'production'
            ORDER BY r.published_at DESC NULLS LAST, r.created_at DESC, c.ended_at DESC NULLS LAST
            LIMIT 1
        """, [MODEL_NAME]).fetchone()
        if not row:
            return {"status": "SKIPPED", "reason": "没有 production Qlib 模型"}
        latest_data = _latest_covered_cn_data_date(conn, min_coverage=min_coverage)
        latest_pred = conn.execute("""
            SELECT MAX(prediction_date)
            FROM qlib_predictions
            WHERE experiment_id = ?
              AND prediction_date <= ?
        """, [row[1], latest_data]).fetchone()[0] if latest_data is not None else None
    finally:
        conn.close()

    if latest_data is None:
        return {"status": "SKIPPED", "reason": "没有覆盖率达标的 A股行情截面"}
    if latest_pred is not None and latest_pred >= latest_data and not force:
        return {
            "status": "SKIPPED",
            "reason": "production 预测已是最新",
            "prediction_date": latest_pred,
            "latest_data": latest_data,
        }

    model_version = row[0]
    experiment_id = row[1]
    mode = row[3] or "walk_forward"
    base_segments = {
        "train_start": row[4],
        "train_end": row[5],
        "valid_start": row[6],
        "valid_end": row[7],
        "test_start": row[8],
        "test_end": row[9],
    }
    config_snapshot = _safe_json_dict(row[10])
    lgb_params = _safe_json_dict(row[11]) or _safe_json_dict(config_snapshot.get("lgb"))
    top_n = int(row[12] or DEFAULT_TOP_N)
    segments = _production_inference_segments(mode, base_segments, latest_data)
    paths = _production_artifact_paths(model_version)
    paths["model"].parent.mkdir(parents=True, exist_ok=True)

    try:
        _ensure_qlib_data_covers(latest_data)
        pred, saved_model = train_alpha158(
            **segments,
            model_output_path=paths["model"],
            lgb_params=lgb_params,
        )
    except Exception as exc:
        logger.exception("Production Qlib inference failed")
        return {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "latest_data": latest_data,
            "experiment_id": experiment_id,
            "model_version": model_version,
        }
    if pred.empty:
        return {"status": "SKIPPED", "reason": "production 推理未产生预测结果"}
    pred = pred[pd.to_datetime(pred["datetime"]).dt.date <= latest_data].copy()
    if pred.empty:
        return {"status": "SKIPPED", "reason": f"production 推理未覆盖 {latest_data}"}

    conn = get_connection()
    try:
        init_db(conn)
        written = _persist_prediction_frame(
            conn,
            experiment_id=experiment_id,
            model_version=model_version,
            mode="production_inference",
            pred=pred,
            top_n=top_n,
        )
        conn.execute("""
            UPDATE qlib_model_registry
            SET model_path = ?
            WHERE model_name = ? AND model_version = ?
        """, [saved_model, MODEL_NAME, model_version])
    finally:
        conn.close()

    manifest_path = _write_production_manifest(
        model_version,
        {
            "experiment_id": experiment_id,
            "mode": mode,
            "segments": segments,
            "params": lgb_params,
            "model_path": saved_model,
            "latest_data": latest_data,
            "prediction_min": pred["datetime"].min(),
            "prediction_max": pred["datetime"].max(),
            "prediction_rows": written,
        },
    )
    return {
        "status": "SUCCEEDED",
        "model_version": model_version,
        "experiment_id": experiment_id,
        "latest_data": latest_data,
        "prediction_rows": written,
        "model_path": saved_model,
        "manifest_path": manifest_path,
    }


def run_experiment(
    mode: str = "fixed",
    top_n: int = DEFAULT_TOP_N,
    lgb_params: dict[str, Any] | None = None,
    candidate_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"fixed", "walk_forward"}:
        raise ValueError("mode must be fixed or walk_forward")
    experiment_id = _experiment_id(mode)
    segments = _segments_from_config()
    _insert_experiment(
        experiment_id,
        mode,
        "RUNNING",
        segments,
        lgb_params=lgb_params,
        candidate_spec=candidate_spec,
    )
    model_version = _model_version(experiment_id)
    model_path = PROJECT_ROOT / "models" / "qlib" / f"{model_version}.pkl"

    try:
        if mode == "fixed":
            pred, saved_model = train_alpha158(**segments, model_output_path=model_path, lgb_params=lgb_params)
        else:
            pred_parts = []
            years = range(pd.to_datetime(segments["test_start"]).year, pd.to_datetime(segments["test_end"]).year + 1)
            for year in years:
                fold = {
                    "train_start": segments["train_start"],
                    "train_end": f"{year - 2}-12-31",
                    "valid_start": f"{year - 1}-01-01",
                    "valid_end": f"{year - 1}-12-31",
                    "test_start": f"{year}-01-01",
                    "test_end": min(pd.Timestamp(f"{year}-12-31"), pd.Timestamp(segments["test_end"])).strftime("%Y-%m-%d"),
                }
                if pd.Timestamp(fold["train_end"]) <= pd.Timestamp(fold["train_start"]):
                    continue
                part, _ = train_alpha158(**fold, lgb_params=lgb_params)
                if not part.empty:
                    pred_parts.append(part)
            pred = pd.concat(pred_parts, ignore_index=True) if pred_parts else pd.DataFrame()
            saved_model = None

        if pred.empty:
            raise RuntimeError("Qlib 未产生预测结果")

        metrics = evaluate_predictions(experiment_id, pred, mode=mode, top_n=top_n)
        if not metrics:
            raise RuntimeError("预测结果无法完成评估")
        run_id = save_backtest_result(
            MODEL_NAME,
            "CN",
            metrics,
            {"strategy": MODEL_NAME, "mode": mode, "top_n": top_n, "source": "qlib_experiment"},
            engine="qlib",
            decision_scope="decision",
        )
        _register_candidate(experiment_id, model_version, metrics, saved_model)
        _finish_experiment(experiment_id, "SUCCEEDED", metrics, run_id, model_version)
        return {"experiment_id": experiment_id, "model_version": model_version, "run_id": run_id, **metrics}
    except Exception as exc:
        logger.exception(f"Qlib experiment failed: {experiment_id}")
        _finish_experiment(experiment_id, "FAILED", error_message=f"{type(exc).__name__}: {exc}")
        return {"experiment_id": experiment_id, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}


def _batch_id() -> str:
    return f"QLIB-BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def _filter_candidates(candidate_ids: list[str] | None) -> list[dict[str, Any]]:
    candidates = default_candidate_specs(include_unavailable=True)
    if not candidate_ids:
        return candidates
    wanted = {item.strip() for item in candidate_ids if item.strip()}
    return [item for item in candidates if item["candidate_id"] in wanted]


def _skip_reason_for_candidate(spec: dict[str, Any]) -> str | None:
    if spec.get("status") == "SKIPPED":
        module_by_family = {"xgboost": "xgboost", "catboost": "catboost", "pytorch": "torch"}
        module_name = module_by_family.get(str(spec.get("model_family")))
        missing = module_name and importlib.util.find_spec(module_name) is None
        reason = spec.get("skip_reason") or "当前候选暂不可运行"
        if missing:
            reason = f"{reason}；当前 Python 环境缺少 {module_name}"
        return reason
    if spec.get("model_family") != "lgbm":
        return f"当前批跑入口暂只支持 LightGBM，{spec.get('model_family')} 已列入后续接入"
    return None


def _candidate_result_row(
    spec: dict[str, Any],
    batch_id: str,
    mode: str,
    status: str,
    started_at: datetime,
    experiment_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    best: dict[str, Any] | None = None,
    grid_config: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    metrics = metrics or {}
    best = best or {}
    return {
        "candidate_id": spec["candidate_id"],
        "batch_id": batch_id,
        "experiment_id": experiment_id,
        "model_name": MODEL_NAME,
        "model_family": spec["model_family"],
        "model_variant": spec["model_variant"],
        "status": status,
        "mode": mode,
        "params_json": _json(spec.get("params", {})),
        "grid_json": _json(grid_config or {}),
        "best_benchmark": best.get("benchmark_name"),
        "best_top_n": best.get("top_n"),
        "best_holding_days": best.get("holding_days"),
        "best_rebalance_freq": best.get("rebalance_freq"),
        "best_buffer_n": best.get("buffer_n"),
        "best_constraint_profile": best.get("constraint_profile"),
        "best_account_capital": best.get("account_capital"),
        "avg_selected_count": best.get("avg_selected_count"),
        "avg_cash_drag": best.get("avg_cash_drag"),
        "max_actual_position_pct": best.get("max_actual_position_pct"),
        "annual_return": best.get("annual_return"),
        "sharpe_ratio": best.get("sharpe_ratio"),
        "max_drawdown": best.get("max_drawdown"),
        "turnover": best.get("turnover"),
        "benchmark_return": best.get("benchmark_return"),
        "excess_return": best.get("excess_return"),
        "ic_mean": metrics.get("ic_mean"),
        "icir": metrics.get("icir"),
        "rank_ic_mean": metrics.get("rank_ic_mean"),
        "rank_ic_positive_rate": metrics.get("rank_ic_positive_rate"),
        "score": best.get("score"),
        "error_message": error_message,
        "started_at": started_at,
        "ended_at": datetime.now(),
    }


def run_candidate_batch(
    mode: str = "walk_forward",
    batch_id: str | None = None,
    candidate_ids: list[str] | None = None,
    top_ns: list[int] | None = None,
    holding_days: list[int] | None = None,
    rebalance_freqs: list[str] | None = None,
    buffer_mult: float = 1.5,
    turnover_profile: str = "low",
    account_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if mode not in {"fixed", "walk_forward"}:
        raise ValueError("mode must be fixed or walk_forward")
    if turnover_profile not in {"low", "medium"}:
        raise ValueError("turnover_profile must be low or medium")
    batch_id = batch_id or _batch_id()
    top_ns = top_ns or [20, 50, 100]
    holding_days = holding_days or list(range(1, 11))
    rebalance_freqs = rebalance_freqs or ["daily", "monthly"]
    grid_config = {
        "top_ns": top_ns,
        "holding_days": holding_days,
        "rebalance_freqs": rebalance_freqs,
        "buffer_mult": buffer_mult,
        "account_profiles": account_profiles or [],
        "selection_benchmark": "MIXED_EQUAL",
        "turnover_profile": turnover_profile,
        "target_turnover": "30-50" if turnover_profile == "medium" else "lower_is_better",
        "score": (
            "medium: excess + 0.05*sharpe - 0.20*abs(max_drawdown) + in_band_bonus - distance_penalty"
            if turnover_profile == "medium"
            else "low: excess + 0.05*sharpe - 0.20*abs(max_drawdown) - 0.002*turnover + monthly_bonus"
        ),
    }
    candidates = _filter_candidates(candidate_ids)
    output: dict[str, Any] = {
        "batch_id": batch_id,
        "mode": mode,
        "grid": grid_config,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "results": [],
    }

    for spec in candidates:
        started_at = datetime.now()
        skip_reason = _skip_reason_for_candidate(spec)
        if skip_reason:
            row = _candidate_result_row(
                spec,
                batch_id,
                mode,
                "SKIPPED",
                started_at,
                grid_config=grid_config,
                error_message=skip_reason,
            )
            _save_candidate_result_to_db(row)
            output["results"].append(row)
            logger.warning(f"Skipping candidate {spec['candidate_id']}: {skip_reason}")
            continue

        running = _candidate_result_row(spec, batch_id, mode, "RUNNING", started_at, grid_config=grid_config)
        _save_candidate_result_to_db(running)
        logger.info(f"Running Qlib candidate {spec['candidate_id']} ({spec['model_variant']})")

        result = run_experiment(
            mode=mode,
            top_n=DEFAULT_TOP_N,
            lgb_params=spec.get("params"),
            candidate_spec={
                "batch_id": batch_id,
                "candidate_id": spec["candidate_id"],
                "model_family": spec["model_family"],
                "model_variant": spec["model_variant"],
            },
        )
        experiment_id = result.get("experiment_id")
        if result.get("status") == "FAILED":
            row = _candidate_result_row(
                spec,
                batch_id,
                mode,
                "FAILED",
                started_at,
                experiment_id=experiment_id,
                metrics=result,
                grid_config=grid_config,
                error_message=result.get("error"),
            )
            _save_candidate_result_to_db(row)
            output["results"].append(row)
            continue

        try:
            grid = evaluate_parameter_grid(
                str(experiment_id),
                top_ns=top_ns,
                holding_days=holding_days,
                rebalance_freqs=rebalance_freqs,
                buffer_mult=buffer_mult,
                account_profiles=account_profiles,
            )
            best = select_best_candidate_grid(
                grid,
                benchmark_name="MIXED_EQUAL",
                turnover_profile=turnover_profile,
            )
            grid_config_with_rows = {**grid_config, "rows_written": len(grid)}
            row = _candidate_result_row(
                spec,
                batch_id,
                mode,
                "SUCCEEDED",
                started_at,
                experiment_id=experiment_id,
                metrics=result,
                best=best,
                grid_config=grid_config_with_rows,
            )
            _save_candidate_result_to_db(row)
            output["results"].append(row)
        except Exception as exc:
            logger.exception(f"Qlib candidate grid failed: {spec['candidate_id']}")
            row = _candidate_result_row(
                spec,
                batch_id,
                mode,
                "FAILED",
                started_at,
                experiment_id=experiment_id,
                metrics=result,
                grid_config=grid_config,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            _save_candidate_result_to_db(row)
            output["results"].append(row)

    succeeded = [row for row in output["results"] if row.get("status") == "SUCCEEDED" and row.get("score") is not None]
    succeeded.sort(key=lambda row: _safe_float(row.get("score")), reverse=True)
    output["best_candidate"] = succeeded[0] if succeeded else None
    output["ended_at"] = datetime.now().isoformat(timespec="seconds")
    out_dir = PROJECT_ROOT / "data" / "jobs" / "qlib_candidate_batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{batch_id}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    output["summary_path"] = str(out_path)
    return output


def publish_model(experiment_id: str, force: bool = False) -> dict[str, Any]:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        row = conn.execute("""
            SELECT e.experiment_id, e.model_version, e.status, e.metrics_json, r.model_path,
                   e.mode, e.train_start, e.train_end, e.valid_start, e.valid_end,
                   e.test_start, e.test_end, e.config_snapshot, c.params_json,
                   c.best_top_n, c.best_holding_days, c.best_rebalance_freq
            FROM qlib_experiments e
            LEFT JOIN qlib_model_registry r ON e.experiment_id = r.experiment_id
            LEFT JOIN qlib_candidate_results c
              ON c.experiment_id = e.experiment_id
             AND c.status = 'SUCCEEDED'
            WHERE e.experiment_id = ?
            ORDER BY r.created_at DESC NULLS LAST, c.ended_at DESC NULLS LAST
            LIMIT 1
        """, [experiment_id]).fetchone()
        if not row:
            raise ValueError(f"找不到实验: {experiment_id}")
        if row[2] != "SUCCEEDED":
            raise ValueError(f"实验未成功，不能发布: {row[2]}")
        model_version = row[1] or _model_version(experiment_id)
        metrics = json.loads(row[3] or "{}")
        ok, reason = _passes_publish_gate(metrics)
        if not ok and not force:
            raise ValueError(f"未通过发布门槛: {reason}")

        manifest_path = _write_production_manifest(
            model_version,
            {
                "experiment_id": experiment_id,
                "mode": row[5],
                "segments": {
                    "train_start": row[6],
                    "train_end": row[7],
                    "valid_start": row[8],
                    "valid_end": row[9],
                    "test_start": row[10],
                    "test_end": row[11],
                },
                "params": _safe_json_dict(row[13]) or _safe_json_dict(_safe_json_dict(row[12]).get("lgb")),
                "model_path": row[4],
                "best_top_n": row[14],
                "best_holding_days": row[15],
                "best_rebalance_freq": row[16],
                "metrics": metrics,
            },
        )
        production_path = row[4] or manifest_path

        conn.execute("""
            UPDATE qlib_model_registry
            SET status = 'archived', archived_at = CURRENT_TIMESTAMP
            WHERE model_name = ? AND status = 'production'
        """, [MODEL_NAME])
        conn.execute("""
            INSERT OR REPLACE INTO qlib_model_registry (
                model_version, experiment_id, model_name, status, market, model_path, metrics_json, published_at
            )
            VALUES (?, ?, ?, 'production', 'CN', ?, ?, CURRENT_TIMESTAMP)
        """, [model_version, experiment_id, MODEL_NAME, production_path, _json(metrics)])
        return {
            "model_version": model_version,
            "experiment_id": experiment_id,
            "status": "production",
            "forced": force,
            "artifact_path": production_path,
            "manifest_path": manifest_path,
        }
    finally:
        conn.close()


def predict_latest(model: str = "production", top_n: int | None = None) -> dict[str, Any]:
    from src.data_pipeline.loader import get_connection, init_db
    from src.research.strategies.alpha158_baseline import generate_signals
    from src.signals.generator import save_to_csv, save_to_db

    if model != "production":
        raise ValueError("目前只支持 model=production")

    refresh_result: dict[str, Any] | None = None
    prod = None
    latest_pred = None
    resolved_top_n = DEFAULT_TOP_N
    expected_holding_days = 5
    rebalance_freq = "daily"

    for attempt in range(2):
        conn = get_connection()
        try:
            init_db(conn)
            prod = conn.execute("""
                SELECT r.model_version, r.experiment_id, c.best_top_n, c.best_holding_days,
                       c.best_rebalance_freq
                FROM qlib_model_registry r
                LEFT JOIN qlib_candidate_results c
                  ON c.experiment_id = r.experiment_id
                 AND c.status = 'SUCCEEDED'
                WHERE r.model_name = ? AND r.status = 'production'
                ORDER BY r.published_at DESC NULLS LAST, r.created_at DESC, c.ended_at DESC NULLS LAST
                LIMIT 1
            """, [MODEL_NAME]).fetchone()
            if not prod:
                return {"status": "SKIPPED", "reason": "没有 production Qlib 模型"}
            resolved_top_n = int(top_n or prod[2] or DEFAULT_TOP_N)
            expected_holding_days = int(prod[3] or 5)
            latest_data = _latest_covered_cn_data_date(conn)
            latest_pred = conn.execute("""
                SELECT MAX(prediction_date)
                FROM qlib_predictions
                WHERE experiment_id = ?
                  AND prediction_date <= ?
            """, [prod[1], latest_data]).fetchone()[0] if latest_data is not None else None
        finally:
            conn.close()

        if latest_data is None:
            return {"status": "SKIPPED", "reason": "没有覆盖率达标的 A股行情截面"}
        if latest_pred is None or latest_pred < latest_data:
            if attempt > 0:
                return {
                    "status": "SKIPPED",
                    "reason": f"production 预测日期 {latest_pred} 仍早于覆盖率达标行情 {latest_data}",
                    "refresh_result": refresh_result,
                }
            refresh_result = refresh_production_predictions()
            if refresh_result.get("status") != "SUCCEEDED":
                return refresh_result
            continue
        break

    if prod is None or latest_pred is None:
        return {"status": "SKIPPED", "reason": "production 实验没有预测截面"}

    conn = get_connection()
    try:
        init_db(conn)
        rebalance_freq = str(prod[4] or "daily").lower()
        skip_rebalance, skip_reason = _should_skip_rebalance_signal_write(
            conn,
            model_version=prod[0],
            prediction_date=latest_pred,
            rebalance_freq=rebalance_freq,
        )
        if skip_rebalance:
            return {
                "status": "SKIPPED",
                "reason": skip_reason,
                "prediction_date": latest_pred,
                "model_version": prod[0],
                "top_n": resolved_top_n,
                "expected_holding_days": expected_holding_days,
                "rebalance_freq": rebalance_freq,
                "refresh_result": refresh_result,
            }
        pred = conn.execute("""
            SELECT prediction_date AS datetime, symbol AS instrument, score
            FROM qlib_predictions
            WHERE experiment_id = ? AND prediction_date = ?
            ORDER BY score DESC
        """, [prod[1], latest_pred]).fetchdf()
        holdings = conn.execute("""
            WITH latest_pos AS (
                SELECT symbol, quantity,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY trade_date DESC
                       ) AS rn
                FROM paper_positions
                WHERE strategy_name = ?
            )
            SELECT symbol, quantity
            FROM latest_pos
            WHERE rn = 1 AND COALESCE(quantity, 0) > 0
        """, [MODEL_NAME]).fetchdf()
    finally:
        conn.close()

    signals = generate_signals(
        pred,
        top_n=resolved_top_n,
        current_holdings=holdings,
        exit_rank_multiplier=2.0,
        expected_holding_days=expected_holding_days,
    )
    if signals.empty:
        return {"status": "SKIPPED", "reason": "未生成 Alpha158 信号"}
    signals["model_version"] = prod[0]
    written = save_to_db(signals)
    save_to_csv(signals)
    return {
        "status": "SUCCEEDED",
        "signals_written": written,
        "prediction_date": latest_pred,
        "model_version": prod[0],
        "top_n": resolved_top_n,
        "expected_holding_days": expected_holding_days,
        "rebalance_freq": rebalance_freq,
        "refresh_result": refresh_result,
        "buy_signals": int((signals["side"] == "BUY").sum()),
        "sell_signals": int((signals["side"] == "SELL").sum()),
    }


def prepare_data(market: str = "cn") -> int:
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "convert_to_qlib.py"), "--market", market]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode


def run_qlib_backtest(strategy: str = MODEL_NAME) -> dict[str, Any]:
    """Backward-compatible entrypoint: run the fixed Alpha158 research experiment."""
    if strategy != MODEL_NAME:
        logger.warning(f"目前仅支持 {MODEL_NAME}，跳过 {strategy}")
        return {}
    return run_experiment("fixed")


def run_all_strategies() -> dict[str, dict[str, Any]]:
    return {MODEL_NAME: run_qlib_backtest(MODEL_NAME)}


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qlib Alpha158 research and production workflow")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="检查 Qlib 环境、数据和 production 模型")
    p_prepare = sub.add_parser("prepare-data", help="将 DuckDB 行情导出为 Qlib 二进制数据")
    p_prepare.add_argument("--market", choices=["cn", "hk", "all"], default="cn")
    p_run = sub.add_parser("run-experiment", help="运行 Qlib 研究实验")
    p_run.add_argument("--mode", choices=["fixed", "walk_forward"], default="fixed")
    p_run.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p_publish = sub.add_parser("publish", help="发布成功实验为 production 模型")
    p_publish.add_argument("--experiment-id", required=True)
    p_publish.add_argument("--force", action="store_true")
    p_predict = sub.add_parser("predict-latest", help="使用 production 模型写入最新 Alpha158 信号")
    p_predict.add_argument("--model", default="production")
    p_predict.add_argument("--top-n", type=int, default=None, help="默认使用 production 候选批跑胜出 Top-N")
    p_refresh = sub.add_parser("refresh-production", help="刷新 production 模型最新预测截面并保存模型 artifact")
    p_refresh.add_argument("--force", action="store_true", help="即使预测已是最新，也重训最新 production 折模型")
    p_refresh.add_argument("--min-coverage", type=float, default=0.80, help="A股截面最低覆盖率，默认 0.80")
    p_grid = sub.add_parser("evaluate-grid", help="复用已有预测截面评估 Top-N/持仓周期/调仓频率网格")
    p_grid.add_argument("--experiment-id", default="latest", help="实验ID，或 latest")
    p_grid.add_argument("--top-n", default="20,50,100")
    p_grid.add_argument("--holding-days", default="1-10")
    p_grid.add_argument("--rebalance", default="daily,monthly")
    p_grid.add_argument("--buffer-mult", type=float, default=1.5)
    p_grid.add_argument("--small-account", action=argparse.BooleanOptionalAction, default=True)
    p_grid.add_argument("--small-account-capitals", default="300000,500000")
    p_grid.add_argument("--small-account-max-pe", type=float, default=120.0)
    p_grid.add_argument("--small-account-max-pb", type=float, default=None)
    p_candidates = sub.add_parser("run-candidates", help="批量运行 Qlib 模型/参数候选并记录最优组合")
    p_candidates.add_argument("--mode", choices=["fixed", "walk_forward"], default="walk_forward")
    p_candidates.add_argument("--batch-id", default=None)
    p_candidates.add_argument("--candidates", default=None, help="逗号分隔候选ID；默认运行候选清单全部项目")
    p_candidates.add_argument("--top-n", default="20,50,100")
    p_candidates.add_argument("--holding-days", default="1-10")
    p_candidates.add_argument("--rebalance", default="daily,monthly")
    p_candidates.add_argument("--buffer-mult", type=float, default=1.5)
    p_candidates.add_argument("--small-account", action=argparse.BooleanOptionalAction, default=True)
    p_candidates.add_argument("--small-account-capitals", default="300000,500000")
    p_candidates.add_argument("--small-account-max-pe", type=float, default=120.0)
    p_candidates.add_argument("--small-account-max-pb", type=float, default=None)
    p_candidates.add_argument(
        "--turnover-profile",
        choices=["low", "medium"],
        default="low",
        help="low 偏低换手；medium 目标年化换手 30-50，用于今晚这类兼顾超额与风险的批跑",
    )
    p_candidates.add_argument(
        "--preset",
        choices=["nightly", "quick"],
        default="nightly",
        help="quick 只跑少量组合用于冒烟；nightly 使用完整默认网格",
    )

    args = parser.parse_args(argv)
    if args.command in (None, "status"):
        _print_json(qlib_status("cn"))
        return 0
    if args.command == "prepare-data":
        return prepare_data(args.market)
    if args.command == "run-experiment":
        result = run_experiment(args.mode, top_n=args.top_n)
        _print_json(result)
        return 0 if result.get("status") != "FAILED" else 1
    if args.command == "publish":
        _print_json(publish_model(args.experiment_id, force=args.force))
        return 0
    if args.command == "predict-latest":
        _print_json(predict_latest(args.model, top_n=args.top_n))
        return 0
    if args.command == "refresh-production":
        result = refresh_production_predictions(force=args.force, min_coverage=args.min_coverage)
        _print_json(result)
        return 0 if result.get("status") != "FAILED" else 1
    if args.command == "evaluate-grid":
        experiment_id = args.experiment_id
        if experiment_id == "latest":
            from src.data_pipeline.loader import get_connection

            conn = get_connection(read_only=True)
            try:
                row = conn.execute("""
                    SELECT experiment_id
                    FROM qlib_experiments
                    WHERE model_name = ? AND status = 'SUCCEEDED'
                    ORDER BY CASE mode WHEN 'walk_forward' THEN 0 ELSE 1 END, ended_at DESC NULLS LAST
                    LIMIT 1
                """, [MODEL_NAME]).fetchone()
            finally:
                conn.close()
            if not row:
                raise SystemExit("没有成功的 Qlib 实验可用于网格评估")
            experiment_id = row[0]
        result = evaluate_parameter_grid(
            experiment_id,
            top_ns=_parse_int_grid(args.top_n),
            holding_days=_parse_int_grid(args.holding_days),
            rebalance_freqs=[x.strip() for x in args.rebalance.split(",") if x.strip()],
            buffer_mult=args.buffer_mult,
            account_profiles=small_account_profiles(
                _parse_float_grid(args.small_account_capitals),
                max_pe_ttm=args.small_account_max_pe,
                max_pb=args.small_account_max_pb,
            ) if args.small_account else None,
        )
        best = result[result["benchmark_name"] == "MIXED_EQUAL"].copy()
        if "constraint_profile" in best.columns:
            constrained = best[
                best["constraint_profile"].fillna("").astype(str).str.startswith("small_account")
            ].copy()
            if not constrained.empty:
                best = constrained
        best["selection_score"] = best.apply(score_candidate_grid_row, axis=1)
        best = best.sort_values(
            ["selection_score", "excess_return", "sharpe_ratio"], ascending=[False, False, False]
        ).head(10)
        _print_json({
            "source_experiment_id": experiment_id,
            "rows_written": len(result),
            "best_mixed_equal": best[[
                "top_n", "holding_days", "rebalance_freq", "buffer_n",
                "constraint_profile", "account_capital", "avg_selected_count",
                "avg_cash_drag", "max_actual_position_pct",
                "selection_score", "annual_return", "sharpe_ratio", "max_drawdown",
                "turnover", "benchmark_return", "excess_return",
            ]].to_dict("records"),
        })
        return 0
    if args.command == "run-candidates":
        top_ns = _parse_int_grid(args.top_n)
        holding_days = _parse_int_grid(args.holding_days)
        rebalance_freqs = [x.strip() for x in args.rebalance.split(",") if x.strip()]
        candidate_ids = [x.strip() for x in args.candidates.split(",") if x.strip()] if args.candidates else None
        if args.preset == "quick":
            top_ns = top_ns[:1] or [50]
            holding_days = holding_days[:1] or [5]
            rebalance_freqs = rebalance_freqs[:1] or ["monthly"]
            if candidate_ids is None:
                candidate_ids = ["lgb_baseline", "lgb_balanced"]
        result = run_candidate_batch(
            mode=args.mode,
            batch_id=args.batch_id,
            candidate_ids=candidate_ids,
            top_ns=top_ns,
            holding_days=holding_days,
            rebalance_freqs=rebalance_freqs,
            buffer_mult=args.buffer_mult,
            turnover_profile=args.turnover_profile,
            account_profiles=small_account_profiles(
                _parse_float_grid(args.small_account_capitals),
                max_pe_ttm=args.small_account_max_pe,
                max_pb=args.small_account_max_pb,
            ) if args.small_account else None,
        )
        _print_json({
            "batch_id": result["batch_id"],
            "mode": result["mode"],
            "summary_path": result["summary_path"],
            "best_candidate": result.get("best_candidate"),
            "statuses": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "status": row.get("status"),
                    "experiment_id": row.get("experiment_id"),
                    "score": row.get("score"),
                    "error_message": row.get("error_message"),
                }
                for row in result.get("results", [])
            ],
        })
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
