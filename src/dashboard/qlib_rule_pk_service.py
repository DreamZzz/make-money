"""Rule-signal versus Qlib prediction comparison helpers."""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from datetime import date
from typing import Any

import pandas as pd

RULE_MODEL_NAMES = ("trend_following", "mean_reversion", "industry_rotation")
DEFAULT_HORIZONS = (1, 5, 10)


CLASSIFICATION_ORDER = [
    "共振买入",
    "冲突：规则卖出/Qlib高分",
    "规则内部冲突",
    "Qlib独立候选",
    "规则买入/Qlib弱",
    "规则卖出",
]


def load_rule_qlib_pk(
    conn: Any,
    signal_date: date | str | None = None,
    prediction_date: date | str | None = None,
    experiment_id: str | None = None,
    top_n: int = 50,
    secondary_top_n: int | None = None,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """Load and compare rule-based active signals with the latest Qlib cross-section.

    The report is read-only. It never writes Qlib predictions into trading signals.
    """
    top_n = max(int(top_n), 1)
    secondary_top_n = int(secondary_top_n or top_n * 2)
    horizons = tuple(sorted({max(int(h), 1) for h in horizons}))

    selected_prediction_date = _resolve_prediction_date(conn, prediction_date)
    if selected_prediction_date is None:
        return _empty_report("NO_QLIB_PREDICTIONS", top_n, secondary_top_n, horizons)

    selected_experiment = _resolve_experiment_id(conn, selected_prediction_date, experiment_id)
    if selected_experiment is None:
        return _empty_report("NO_QLIB_PREDICTIONS", top_n, secondary_top_n, horizons)

    selected_signal_date = _resolve_signal_date(conn, signal_date, selected_prediction_date)
    qlib = _load_qlib_predictions(conn, selected_prediction_date, selected_experiment)
    rules = _load_rule_signals(conn, selected_signal_date) if selected_signal_date is not None else pd.DataFrame()

    details = build_pk_details(
        rule_signals=rules,
        qlib_predictions=qlib,
        top_n=top_n,
        secondary_top_n=secondary_top_n,
    )
    if details.empty:
        summary = _summary(details, top_n, secondary_top_n)
        status = "NO_RULE_SIGNALS" if rules.empty else "OK"
        return {
            "status": status,
            "signal_date": selected_signal_date,
            "prediction_date": selected_prediction_date,
            "experiment_id": selected_experiment,
            "top_n": top_n,
            "secondary_top_n": secondary_top_n,
            "summary": summary,
            "details": details,
            "history": pd.DataFrame(),
            "glossary": metric_glossary(),
        }

    prices = _load_prices(conn, details["symbol"].dropna().astype(str).unique().tolist())
    details = add_forward_returns(details, prices, horizons)
    history = summarize_forward_history(details, top_n, horizons)
    return {
        "status": "OK",
        "signal_date": selected_signal_date,
        "prediction_date": selected_prediction_date,
        "experiment_id": selected_experiment,
        "top_n": top_n,
        "secondary_top_n": secondary_top_n,
        "summary": _summary(details, top_n, secondary_top_n),
        "details": details,
        "history": history,
        "glossary": metric_glossary(),
    }


def resolve_champion_experiment(
    conn: Any,
    prediction_date: date | str | None = None,
    default_top_n: int = 50,
) -> dict[str, Any] | None:
    """Resolve the experiment used for production/champion comparison.

    Priority:
    1. Published production model with an available prediction cross-section.
    2. Highest-scoring candidate batch result with predictions.
    3. Latest prediction experiment.
    """
    resolved_date = pd.to_datetime(prediction_date).date() if prediction_date is not None else None
    date_filter = "AND p.prediction_date = ?" if resolved_date is not None else ""
    params = [resolved_date] if resolved_date is not None else []

    prod = conn.execute(f"""
        SELECT r.experiment_id, r.model_version, MAX(p.prediction_date) AS prediction_date,
               c.best_top_n, c.best_holding_days, c.best_rebalance_freq
        FROM qlib_model_registry r
        JOIN qlib_predictions p ON r.experiment_id = p.experiment_id
        LEFT JOIN qlib_candidate_results c
          ON r.experiment_id = c.experiment_id AND c.status = 'SUCCEEDED'
        WHERE r.model_name = 'alpha158'
          AND r.status = 'production'
          {date_filter}
        GROUP BY r.experiment_id, r.model_version, c.best_top_n, c.best_holding_days, c.best_rebalance_freq, r.published_at
        ORDER BY r.published_at DESC NULLS LAST
        LIMIT 1
    """, params).fetchone()
    if prod:
        return {
            "experiment_id": prod[0],
            "model_version": prod[1],
            "prediction_date": prod[2],
            "top_n": int(prod[3] or default_top_n),
            "holding_days": prod[4],
            "rebalance_freq": prod[5],
            "source": "production",
        }

    candidate = conn.execute(f"""
        SELECT c.experiment_id, COALESCE(e.model_version, p.model_version) AS model_version,
               MAX(p.prediction_date) AS prediction_date, c.best_top_n,
               c.best_holding_days, c.best_rebalance_freq, c.score
        FROM qlib_candidate_results c
        JOIN qlib_predictions p ON c.experiment_id = p.experiment_id
        LEFT JOIN qlib_experiments e ON c.experiment_id = e.experiment_id
        WHERE c.status = 'SUCCEEDED'
          {date_filter}
        GROUP BY c.experiment_id, COALESCE(e.model_version, p.model_version),
                 c.best_top_n, c.best_holding_days, c.best_rebalance_freq, c.score, c.excess_return
        ORDER BY c.score DESC NULLS LAST, c.excess_return DESC NULLS LAST
        LIMIT 1
    """, params).fetchone()
    if candidate:
        return {
            "experiment_id": candidate[0],
            "model_version": candidate[1],
            "prediction_date": candidate[2],
            "top_n": int(candidate[3] or default_top_n),
            "holding_days": candidate[4],
            "rebalance_freq": candidate[5],
            "source": "candidate_champion",
        }

    latest = conn.execute(f"""
        SELECT p.experiment_id, p.model_version, MAX(p.prediction_date) AS prediction_date
        FROM qlib_predictions p
        WHERE p.model_name = 'alpha158'
          {date_filter}
        GROUP BY p.experiment_id, p.model_version
        ORDER BY prediction_date DESC NULLS LAST, p.experiment_id DESC
        LIMIT 1
    """, params).fetchone()
    if latest:
        return {
            "experiment_id": latest[0],
            "model_version": latest[1],
            "prediction_date": latest[2],
            "top_n": default_top_n,
            "holding_days": None,
            "rebalance_freq": None,
            "source": "latest_prediction",
        }
    return None


def record_ab_snapshot(
    conn: Any,
    signal_date: date | str | None = None,
    prediction_date: date | str | None = None,
    experiment_id: str | None = None,
    top_n: int | None = None,
    secondary_top_n: int | None = None,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    champion = None
    if experiment_id is None:
        champion = resolve_champion_experiment(conn, prediction_date=prediction_date)
        if champion is None:
            return {"status": "SKIPPED", "reason": "没有可用 Qlib production/champion 预测"}
        experiment_id = champion["experiment_id"]
        prediction_date = champion["prediction_date"]
        top_n = int(top_n or champion.get("top_n") or 50)
    else:
        top_n = int(top_n or 50)
    secondary_top_n = int(secondary_top_n or top_n * 2)

    report = load_rule_qlib_pk(
        conn,
        signal_date=signal_date,
        prediction_date=prediction_date,
        experiment_id=experiment_id,
        top_n=top_n,
        secondary_top_n=secondary_top_n,
        horizons=horizons,
    )
    if report["status"] != "OK":
        return {"status": "SKIPPED", "reason": report["status"]}

    run_id = _ab_run_id(report["prediction_date"], report["experiment_id"], top_n)
    details = report["details"]
    members = _build_ab_members(details, run_id, top_n)
    if members.empty:
        return {"status": "SKIPPED", "reason": "没有可记录的 A/B 成员"}

    summary_json = json.dumps(report["summary"], ensure_ascii=False, default=str)
    model_version = champion.get("model_version") if champion else _model_version_for_experiment(conn, experiment_id)
    champion_source = champion.get("source") if champion else "explicit"
    conn.execute("""
        INSERT OR REPLACE INTO rule_qlib_ab_snapshots (
            run_id, snapshot_date, signal_date, prediction_date, experiment_id,
            model_version, top_n, secondary_top_n, champion_source, status, summary_json
        )
        VALUES (?, CURRENT_DATE, ?, ?, ?, ?, ?, ?, ?, 'RECORDED', ?)
    """, [
        run_id, report["signal_date"], report["prediction_date"], report["experiment_id"],
        model_version, int(top_n), int(secondary_top_n), champion_source, summary_json,
    ])
    conn.execute("DELETE FROM rule_qlib_ab_members WHERE run_id = ?", [run_id])
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_rule_qlib_ab_members AS SELECT * FROM members")
    conn.execute("""
        INSERT OR REPLACE INTO rule_qlib_ab_members (
            run_id, arm, symbol, name, classification, rule_side, rule_models,
            rule_confidence, rule_score, qlib_rank, qlib_score, weight
        )
        SELECT run_id, arm, symbol, name, classification, rule_side, rule_models,
               rule_confidence, rule_score, qlib_rank, qlib_score, weight
        FROM _tmp_rule_qlib_ab_members
    """)
    return {
        "status": "RECORDED",
        "run_id": run_id,
        "experiment_id": report["experiment_id"],
        "prediction_date": report["prediction_date"],
        "signal_date": report["signal_date"],
        "top_n": int(top_n),
        "members": int(len(members)),
        "champion_source": champion_source,
    }


def evaluate_ab_tracking(
    conn: Any,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> dict[str, pd.DataFrame]:
    snapshots = conn.execute("""
        SELECT run_id, snapshot_date, signal_date, prediction_date, experiment_id,
               model_version, top_n, champion_source, status
        FROM rule_qlib_ab_snapshots
        ORDER BY prediction_date, run_id
    """).fetchdf()
    if snapshots.empty:
        return {"snapshots": snapshots, "member_returns": pd.DataFrame(), "arm_summary": pd.DataFrame()}
    members = conn.execute("""
        SELECT m.run_id, s.signal_date, s.prediction_date, s.experiment_id, s.model_version,
               s.top_n, s.champion_source, m.arm, m.symbol, m.name, m.classification,
               m.rule_side, m.rule_models, m.rule_confidence, m.rule_score,
               m.qlib_rank, m.qlib_score, m.weight
        FROM rule_qlib_ab_members m
        JOIN rule_qlib_ab_snapshots s ON m.run_id = s.run_id
        ORDER BY s.prediction_date, m.arm, m.symbol
    """).fetchdf()
    if members.empty:
        return {"snapshots": snapshots, "member_returns": members, "arm_summary": pd.DataFrame()}
    prices = _load_prices(conn, members["symbol"].dropna().astype(str).unique().tolist())
    member_returns = add_forward_returns(members, prices, horizons)
    arm_summary = _summarize_ab_arms(member_returns, horizons)
    return {"snapshots": snapshots, "member_returns": member_returns, "arm_summary": arm_summary}


def build_pk_details(
    rule_signals: pd.DataFrame,
    qlib_predictions: pd.DataFrame,
    top_n: int,
    secondary_top_n: int,
) -> pd.DataFrame:
    qlib = _normalize_qlib(qlib_predictions)
    rules = _aggregate_rule_signals(rule_signals)
    if qlib.empty and rules.empty:
        return pd.DataFrame()

    top = qlib[qlib["qlib_rank"] <= top_n].copy() if not qlib.empty else pd.DataFrame()
    union_symbols = set(rules["symbol"].astype(str)) if not rules.empty else set()
    union_symbols.update(top["symbol"].astype(str).tolist() if not top.empty else [])
    if not union_symbols:
        return pd.DataFrame()

    base = pd.DataFrame({"symbol": sorted(union_symbols)})
    if not rules.empty:
        base = base.merge(rules, on="symbol", how="left")
    if not qlib.empty:
        base = base.merge(qlib, on="symbol", how="left")
    base = _coalesce_duplicate_metadata(base)

    for col in ["rule_buy", "rule_sell"]:
        if col not in base:
            base[col] = False
        base[col] = base[col].where(base[col].notna(), False).astype(bool)
    for col in ["rule_models", "rule_side"]:
        if col not in base:
            base[col] = ""
        base[col] = base[col].fillna("")

    base["classification"] = base.apply(
        lambda row: _classify(row, top_n=top_n, secondary_top_n=secondary_top_n),
        axis=1,
    )
    base["classification_order"] = base["classification"].map(
        {name: idx for idx, name in enumerate(CLASSIFICATION_ORDER)}
    ).fillna(len(CLASSIFICATION_ORDER))
    return base.sort_values(
        ["classification_order", "qlib_rank", "rule_confidence", "symbol"],
        ascending=[True, True, False, True],
        na_position="last",
    ).reset_index(drop=True)


def _coalesce_duplicate_metadata(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for col in ["name", "industry"]:
        variants = [candidate for candidate in [col, f"{col}_x", f"{col}_y"] if candidate in result.columns]
        if not variants:
            continue
        combined = result[variants[0]]
        for candidate in variants[1:]:
            combined = combined.combine_first(result[candidate])
        result[col] = combined
        drop_cols = [candidate for candidate in variants if candidate != col]
        result = result.drop(columns=drop_cols, errors="ignore")
    return result


def add_forward_returns(details: pd.DataFrame, prices: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    if details.empty or prices.empty:
        return details.copy()
    result = details.copy()
    prices = prices.copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.sort_values(["symbol", "trade_date"])
    grouped = {str(symbol): group.reset_index(drop=True) for symbol, group in prices.groupby("symbol")}

    event_date_col = "signal_date"
    if event_date_col not in result:
        result[event_date_col] = result.get("prediction_date")

    for horizon in horizons:
        values = []
        for _, row in result.iterrows():
            values.append(_forward_return_for_row(grouped, row, horizon))
        result[f"forward_return_{horizon}d"] = values
    return result


def summarize_forward_history(details: pd.DataFrame, top_n: int, horizons: Iterable[int]) -> pd.DataFrame:
    if details.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    group_specs = [
        ("规则买入", details["rule_buy"]),
        ("规则卖出", details["rule_sell"]),
        (f"Qlib Top{top_n}", details["qlib_rank"].le(top_n)),
        ("共振买入", details["classification"].eq("共振买入")),
        ("分歧信号", details["classification"].str.startswith("冲突", na=False)),
        ("Qlib独立候选", details["classification"].eq("Qlib独立候选")),
    ]
    for group_name, mask in group_specs:
        sub = details[mask.fillna(False)].copy()
        if sub.empty:
            continue
        for horizon in horizons:
            col = f"forward_return_{horizon}d"
            if col not in sub:
                continue
            returns = pd.to_numeric(sub[col], errors="coerce").dropna()
            if returns.empty:
                continue
            side = sub.loc[returns.index, "rule_side"].fillna("")
            directional = returns.copy()
            sell_mask = side.str.contains("SELL|SHORT", regex=True)
            directional.loc[sell_mask] = -directional.loc[sell_mask]
            rows.append({
                "group": group_name,
                "horizon": horizon,
                "observations": int(len(returns)),
                "avg_forward_return": float(returns.mean()),
                "win_rate": float((returns > 0).mean()),
                "avg_directional_return": float(directional.mean()),
                "directional_win_rate": float((directional > 0).mean()),
            })
    return pd.DataFrame(rows)


def metric_glossary() -> list[dict[str, str]]:
    return [
        {
            "metric": "共振买入",
            "meaning": "规则策略给出 BUY，且 Qlib 排名进入 Top-N。",
            "plain_explanation": "两个体系都看好，适合作为优先观察或后续加权候选。",
        },
        {
            "metric": "分歧信号",
            "meaning": "规则策略给出 SELL/SHORT，但 Qlib 排名仍在 Top-N。",
            "plain_explanation": "规则认为短期该撤，Qlib 仍认为横截面有吸引力，应重点人工确认。",
        },
        {
            "metric": "方向性收益",
            "meaning": "BUY 使用未来收益；SELL/SHORT 使用未来收益的相反数。",
            "plain_explanation": "买入后涨算对，卖出后跌也算对。",
        },
    ]


def _resolve_prediction_date(conn: Any, prediction_date: date | str | None) -> date | None:
    if prediction_date is not None:
        return pd.to_datetime(prediction_date).date()
    row = conn.execute("SELECT MAX(prediction_date) FROM qlib_predictions").fetchone()
    return row[0] if row and row[0] else None


def _resolve_experiment_id(conn: Any, prediction_date: date, experiment_id: str | None) -> str | None:
    if experiment_id:
        return experiment_id
    row = conn.execute("""
        SELECT p.experiment_id
        FROM qlib_predictions p
        LEFT JOIN qlib_experiments e ON p.experiment_id = e.experiment_id
        WHERE p.prediction_date = ? AND p.model_name = 'alpha158'
        GROUP BY p.experiment_id, e.started_at
        ORDER BY e.started_at DESC NULLS LAST, p.experiment_id DESC
        LIMIT 1
    """, [prediction_date]).fetchone()
    return str(row[0]) if row and row[0] else None


def _resolve_signal_date(conn: Any, signal_date: date | str | None, fallback_date: date) -> date | None:
    if signal_date is not None:
        return pd.to_datetime(signal_date).date()
    row = conn.execute("""
        SELECT MAX(CAST(signal_ts AS DATE))
        FROM signals
        WHERE model_name IN ('trend_following', 'mean_reversion', 'industry_rotation')
          AND CAST(signal_ts AS DATE) <= ?
    """, [fallback_date]).fetchone()
    return row[0] if row and row[0] else None


def _load_qlib_predictions(conn: Any, prediction_date: date, experiment_id: str) -> pd.DataFrame:
    return conn.execute("""
        SELECT p.prediction_date, p.experiment_id, p.symbol, si.name, si.industry,
               p.score AS qlib_score, p.rank AS qlib_rank, p.confidence AS qlib_confidence
        FROM qlib_predictions p
        LEFT JOIN stock_info si ON p.symbol = si.symbol
        WHERE p.prediction_date = ? AND p.experiment_id = ?
        ORDER BY p.rank
    """, [prediction_date, experiment_id]).fetchdf()


def _load_rule_signals(conn: Any, signal_date: date) -> pd.DataFrame:
    return conn.execute("""
        SELECT CAST(s.signal_ts AS DATE) AS signal_date,
               s.signal_id, s.model_name, s.symbol, si.name, si.industry,
               s.side, s.score AS rule_score, s.confidence AS rule_confidence,
               s.max_position_pct, s.thesis
        FROM signals s
        LEFT JOIN stock_info si ON s.symbol = si.symbol
        WHERE s.model_name IN ('trend_following', 'mean_reversion', 'industry_rotation')
          AND CAST(s.signal_ts AS DATE) = ?
          AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
        ORDER BY s.confidence DESC NULLS LAST, s.score DESC NULLS LAST
    """, [signal_date]).fetchdf()


def _load_prices(conn: Any, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    placeholders = ", ".join(["?"] * len(symbols))
    return conn.execute(f"""
        SELECT symbol, trade_date, close
        FROM daily_price
        WHERE symbol IN ({placeholders})
          AND close IS NOT NULL
          AND close > 0
        ORDER BY symbol, trade_date
    """, symbols).fetchdf()


def _normalize_qlib(qlib: pd.DataFrame) -> pd.DataFrame:
    if qlib.empty:
        return qlib.copy()
    df = qlib.copy()
    df["symbol"] = df["symbol"].astype(str)
    if "qlib_rank" not in df or df["qlib_rank"].isna().any():
        df["qlib_rank"] = df["qlib_score"].rank(ascending=False, method="first")
    df["qlib_rank"] = pd.to_numeric(df["qlib_rank"], errors="coerce")
    df["prediction_date"] = pd.to_datetime(df["prediction_date"]).dt.date
    return df


def _aggregate_rule_signals(rules: pd.DataFrame) -> pd.DataFrame:
    if rules.empty:
        return pd.DataFrame(columns=[
            "symbol", "signal_date", "name", "industry", "rule_buy", "rule_sell",
            "rule_side", "rule_models", "rule_confidence", "rule_score",
            "max_position_pct", "thesis",
        ])
    df = rules.copy()
    df["symbol"] = df["symbol"].astype(str)
    df["side"] = df["side"].astype(str).str.upper()
    grouped = []
    for symbol, group in df.groupby("symbol", sort=True):
        sides = sorted(set(group["side"].dropna()))
        models = sorted(set(group["model_name"].dropna()))
        top = group.sort_values(["rule_confidence", "rule_score"], ascending=False, na_position="last").iloc[0]
        grouped.append({
            "symbol": symbol,
            "signal_date": pd.to_datetime(top.get("signal_date")).date(),
            "name": top.get("name"),
            "industry": top.get("industry"),
            "rule_buy": "BUY" in sides,
            "rule_sell": bool(set(sides) & {"SELL", "SHORT"}),
            "rule_side": "/".join(sides),
            "rule_models": ",".join(models),
            "rule_confidence": float(top.get("rule_confidence")) if pd.notna(top.get("rule_confidence")) else None,
            "rule_score": float(top.get("rule_score")) if pd.notna(top.get("rule_score")) else None,
            "max_position_pct": top.get("max_position_pct"),
            "thesis": top.get("thesis"),
        })
    return pd.DataFrame(grouped)


def _classify(row: pd.Series, top_n: int, secondary_top_n: int) -> str:
    rank = row.get("qlib_rank")
    in_top = pd.notna(rank) and float(rank) <= top_n
    rule_buy = bool(row.get("rule_buy"))
    rule_sell = bool(row.get("rule_sell"))
    if rule_buy and rule_sell and in_top:
        return "规则内部冲突"
    if rule_buy and rule_sell:
        return "规则内部冲突"
    if rule_buy and in_top:
        return "共振买入"
    if rule_sell and in_top:
        return "冲突：规则卖出/Qlib高分"
    if in_top and not rule_buy and not rule_sell:
        return "Qlib独立候选"
    if rule_buy:
        return "规则买入/Qlib弱"
    if rule_sell:
        return "规则卖出"
    return "其他"


def _forward_return_for_row(grouped: dict[str, pd.DataFrame], row: pd.Series, horizon: int) -> float | None:
    symbol = str(row.get("symbol"))
    group = grouped.get(symbol)
    if group is None or group.empty:
        return None
    event_date = row.get("signal_date") or row.get("prediction_date")
    if pd.isna(event_date):
        return None
    event_date = pd.to_datetime(event_date)
    dates = pd.to_datetime(group["trade_date"])
    candidates = group[dates >= event_date]
    if candidates.empty:
        return None
    start_idx = int(candidates.index[0])
    end_idx = start_idx + horizon
    if end_idx >= len(group):
        return None
    start_close = float(group.loc[start_idx, "close"])
    end_close = float(group.loc[end_idx, "close"])
    if start_close <= 0:
        return None
    return end_close / start_close - 1


def _summary(details: pd.DataFrame, top_n: int, secondary_top_n: int) -> dict[str, Any]:
    if details.empty:
        return {
            "rule_symbols": 0,
            "rule_buy_symbols": 0,
            "rule_sell_symbols": 0,
            "qlib_top_n": 0,
            "overlap_top_n": 0,
            "overlap_secondary_top_n": 0,
            "consensus_buy": 0,
            "conflict_sell_high_rank": 0,
            "rule_internal_conflict": 0,
            "qlib_only_top_n": 0,
            "avg_qlib_rank_rule_buy": None,
            "avg_qlib_rank_rule_sell": None,
        }
    rule_any = details["rule_buy"] | details["rule_sell"]
    qlib_top = details["qlib_rank"].le(top_n)
    qlib_secondary = details["qlib_rank"].le(secondary_top_n)
    rule_buy = details["rule_buy"]
    rule_sell = details["rule_sell"]
    return {
        "rule_symbols": int(rule_any.sum()),
        "rule_buy_symbols": int(rule_buy.sum()),
        "rule_sell_symbols": int(rule_sell.sum()),
        "qlib_top_n": int(qlib_top.sum()),
        "overlap_top_n": int((rule_any & qlib_top).sum()),
        "overlap_secondary_top_n": int((rule_any & qlib_secondary).sum()),
        "consensus_buy": int(details["classification"].eq("共振买入").sum()),
        "conflict_sell_high_rank": int(details["classification"].eq("冲突：规则卖出/Qlib高分").sum()),
        "rule_internal_conflict": int(details["classification"].eq("规则内部冲突").sum()),
        "qlib_only_top_n": int(details["classification"].eq("Qlib独立候选").sum()),
        "avg_qlib_rank_rule_buy": _mean_or_none(details.loc[rule_buy, "qlib_rank"]),
        "avg_qlib_rank_rule_sell": _mean_or_none(details.loc[rule_sell, "qlib_rank"]),
    }


def _mean_or_none(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.mean())


def _empty_report(status: str, top_n: int, secondary_top_n: int, horizons: tuple[int, ...]) -> dict[str, Any]:
    return {
        "status": status,
        "signal_date": None,
        "prediction_date": None,
        "experiment_id": None,
        "top_n": top_n,
        "secondary_top_n": secondary_top_n,
        "summary": _summary(pd.DataFrame(), top_n, secondary_top_n),
        "details": pd.DataFrame(),
        "history": pd.DataFrame(),
        "glossary": metric_glossary(),
        "horizons": horizons,
    }


def _ab_run_id(prediction_date: date, experiment_id: str, top_n: int) -> str:
    suffix = str(experiment_id).split("-")[-1]
    return f"RQAB-{pd.to_datetime(prediction_date).strftime('%Y%m%d')}-{suffix}-T{int(top_n)}"


def _build_ab_members(details: pd.DataFrame, run_id: str, top_n: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    arm_masks = {
        "A_RULE_BUY": details["rule_buy"],
        "B_QLIB_TOPN": details["qlib_rank"].le(top_n),
        "C_CONSENSUS": details["classification"].eq("共振买入"),
    }
    for arm, mask in arm_masks.items():
        sub = details[mask.fillna(False)].copy()
        if sub.empty:
            continue
        weight = 1.0 / len(sub)
        for _, row in sub.iterrows():
            rows.append({
                "run_id": run_id,
                "arm": arm,
                "symbol": str(row["symbol"]),
                "name": row.get("name"),
                "classification": row.get("classification"),
                "rule_side": row.get("rule_side"),
                "rule_models": row.get("rule_models"),
                "rule_confidence": row.get("rule_confidence"),
                "rule_score": row.get("rule_score"),
                "qlib_rank": int(row["qlib_rank"]) if pd.notna(row.get("qlib_rank")) else None,
                "qlib_score": row.get("qlib_score"),
                "weight": weight,
            })
    return pd.DataFrame(rows)


def _summarize_ab_arms(member_returns: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for arm, group in member_returns.groupby("arm"):
        base = {
            "arm": arm,
            "snapshots": int(group["run_id"].nunique()),
            "members": int(len(group)),
        }
        for horizon in horizons:
            col = f"forward_return_{int(horizon)}d"
            if col not in group:
                continue
            returns = pd.to_numeric(group[col], errors="coerce")
            weighted = group["weight"].astype(float) * returns
            valid = returns.notna()
            base[f"observations_{int(horizon)}d"] = int(valid.sum())
            base[f"avg_forward_return_{int(horizon)}d"] = float(returns[valid].mean()) if valid.any() else None
            by_run = weighted[valid].groupby(group.loc[valid, "run_id"]).sum()
            base[f"portfolio_return_{int(horizon)}d"] = float(by_run.mean()) if not by_run.empty else None
            base[f"win_rate_{int(horizon)}d"] = float((returns[valid] > 0).mean()) if valid.any() else None
        rows.append(base)
    return pd.DataFrame(rows).sort_values("arm").reset_index(drop=True) if rows else pd.DataFrame()


def _model_version_for_experiment(conn: Any, experiment_id: str) -> str | None:
    row = conn.execute("""
        SELECT COALESCE(e.model_version, p.model_version)
        FROM qlib_predictions p
        LEFT JOIN qlib_experiments e ON p.experiment_id = e.experiment_id
        WHERE p.experiment_id = ?
        LIMIT 1
    """, [experiment_id]).fetchone()
    return row[0] if row and row[0] else None


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rule-vs-Qlib shadow A/B tracking")
    sub = parser.add_subparsers(dest="command", required=True)
    p_record = sub.add_parser("record-ab", help="记录今日规则/Qlib 影子 A/B 样本")
    p_record.add_argument("--top-n", type=int, default=None)
    p_record.add_argument("--experiment-id", default=None)
    sub.add_parser("evaluate-ab", help="汇总 A/B 影子跟踪表现")
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        if args.command == "record-ab":
            result = record_ab_snapshot(conn, experiment_id=args.experiment_id, top_n=args.top_n)
            print(json.dumps(result, ensure_ascii=False, default=_json_default, indent=2))
            return 0 if result.get("status") in {"RECORDED", "SKIPPED"} else 1
        if args.command == "evaluate-ab":
            report = evaluate_ab_tracking(conn)
            result = {
                "snapshots": report["snapshots"].to_dict("records"),
                "arm_summary": report["arm_summary"].to_dict("records"),
            }
            print(json.dumps(result, ensure_ascii=False, default=_json_default, indent=2))
            return 0
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
