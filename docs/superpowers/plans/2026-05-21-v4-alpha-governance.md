# V4 Alpha Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Turn the v4 architecture review into an executable alpha-governance roadmap: configurable signal consensus, coverage-backed factor research, production model demotion, and user-facing education.

**Architecture:** Keep production trading behavior conservative while building the next alpha track in research-only mode. Production changes are limited to warning-only data coverage, configurable arbitration gates, and model governance; new factors must pass the same objective alpha gate before they can reach `signals.generate_all()` or consume `satellite_budget`.

**Tech Stack:** Python 3.12, DuckDB, pandas, Qlib/LightGBM, pytest, Ruff, Bash runtime scripts, React/Vite Dashboard V2.

---

## Current-State Analysis

The v4 review is directionally correct, but the current workspace has several important facts that the execution plan must respect:

1. `scripts/daily_close.sh` already runs `src.portfolio.fundamentals_coverage update || true` after market data update, but it does not run `src.data_pipeline.field_coverage_backfill`.
2. `src.data_pipeline.field_coverage_backfill` already has a CLI with `--scopes`, `--skip-industry-fetch`, and `--record-health`. It can write `data_source_health` rows, so it is ready to be inserted as a warning-only close-loop step.
3. `src.signals.arbiter` already filters Alpha158 consensus to the current `qlib_model_registry.status='production'` model, but it still hard-codes `alpha158` as the only consensus baseline.
4. `config/settings.yaml` already has `portfolio.signal_arbiter`; it is the correct place to add `consensus_baselines`.
5. `src.monitoring.model_monitor` evaluates and persists alerts, but it has no demotion function and no CLI command for governance actions.
6. `docs/value_quality_validation_2022_2025.md` proves the current `value_quality` prototype should remain research-only: annual excess return `-8.75pp`, IR `-0.52`, max drawdown `-40.67%`, Alpha158 correlation `0.69`, benchmark correlation `0.90`.
7. The workspace currently has uncommitted open-trade reliability and Dashboard V2 capital-display changes. Plan execution must preserve those edits and avoid broad rewrites.

## Priority Model

| Priority | Theme | Trading Behavior Impact | Reason |
|---|---|---:|---|
| P0 | Coverage + arbiter infrastructure + alpha gate | Low | Builds the rails for future factors without changing the live strategy universe. |
| P1 | Research-only alpha tracks and model demotion | Medium | Generates candidates and reduces stale-production risk. |
| P2 | Dashboard single-track migration | Medium | Reduces operational ambiguity after V2 reaches parity. |
| P3 | User guide investor education | None | Reduces misuse and panic abandonment during drawdowns. |

## File Map

| File | Responsibility |
|---|---|
| `scripts/daily_close.sh` | Add warning-only field-coverage backfill after current holdings coverage. |
| `src/dashboard/job_manager.py` | Keep Dashboard-triggered close workflow in the same step order as `daily_close.sh`. |
| `tests/test_dashboard_runtime_scripts.py` | Assert runtime scripts include field coverage in the correct order. |
| `tests/test_dashboard_job_manager.py` | Assert Dashboard job manager includes the same field coverage step. |
| `config/settings.yaml` | Add `portfolio.signal_arbiter.consensus_baselines`. |
| `src/signals/arbiter.py` | Replace Alpha158-only consensus with configurable baseline consensus. |
| `tests/test_signal_arbiter.py` | Lock current Alpha158 behavior and cover multi-baseline OR semantics. |
| `src/research/alpha_gate.py` | Centralize promotion gates for all research alpha candidates. |
| `tests/test_alpha_gate.py` | Verify pass/fail decisions and metric explanations. |
| `scripts/run_alpha_tournament.py` | Produce comparable research-only gate reports for candidate factors. |
| `src/research/strategies/low_vol.py` | Research-only low-volatility factor. |
| `src/research/strategies/cross_reversal.py` | Research-only cross-sectional reversal factor. |
| `src/research/strategies/value_quality.py` | Keep research-only, add v0.2 neutralization and turnover helpers. |
| `src/research/strategies/value_quality_validation.py` | Add v0.2 validation options and alpha-gate output. |
| `src/monitoring/model_monitor.py` | Add `auto_demote` governance action and CLI command. |
| `tests/test_model_monitor.py` | Verify demotion requires consecutive CRITICAL alerts. |
| `docs/dashboard_v2_user_guide.md` | Add investor education, onboarding, quantitative review thresholds, and emergency playbooks. |
| `frontend/dashboard-v2/src/pages/UserGuidePage.tsx` | Only adjust if markdown rendering misses new headings or tables. |

---

## Task 1: Add Warning-Only Field Coverage To Daily Close

**Files:**
- Modify: `scripts/daily_close.sh`
- Modify: `src/dashboard/job_manager.py`
- Test: `tests/test_dashboard_runtime_scripts.py`
- Test: `tests/test_dashboard_job_manager.py`

- [x] **Step 1: Add runtime-script test for the new step**

Add this assertion to the existing daily-close runtime test in `tests/test_dashboard_runtime_scripts.py`:

```python
def test_daily_close_runs_field_coverage_after_fundamentals_coverage():
    script = Path("scripts/daily_close.sh").read_text()
    fundamentals = 'src.portfolio.fundamentals_coverage update || true'
    field_coverage = (
        'src.data_pipeline.field_coverage_backfill '
        '--scopes current_holdings,signal_candidates,target_universe '
        '--skip-industry-fetch --record-health || true'
    )
    assert fundamentals in script
    assert field_coverage in script
    assert script.index(fundamentals) < script.index(field_coverage)
    assert script.index(field_coverage) < script.index("src.index_funds.pipeline update")
```

- [x] **Step 2: Add Dashboard job-manager step-order test**

Extend `tests/test_dashboard_job_manager.py` with:

```python
def test_daily_close_job_includes_field_coverage_step_after_fundamentals():
    from src.dashboard import job_manager as jm

    step_keys = [step.key for step in jm.JOB_DEFINITIONS["daily_close"].steps]
    assert "fundamentals_coverage" in step_keys
    assert "field_coverage" in step_keys
    assert step_keys.index("fundamentals_coverage") < step_keys.index("field_coverage")
    assert step_keys.index("field_coverage") < step_keys.index("index_funds_update")
    assert jm.SINGLE_STEPS["field_coverage"].cmd == [
        jm.PYTHON,
        "-m",
        "src.data_pipeline.field_coverage_backfill",
        "--scopes",
        "current_holdings,signal_candidates,target_universe",
        "--skip-industry-fetch",
        "--record-health",
    ]
```

- [x] **Step 3: Run the focused failing tests**

Run:

```bash
python3.12 -m pytest tests/test_dashboard_runtime_scripts.py::test_daily_close_runs_field_coverage_after_fundamentals_coverage tests/test_dashboard_job_manager.py::test_daily_close_job_includes_field_coverage_step_after_fundamentals -q
```

Expected result before implementation: both tests fail because the field coverage step is absent.

- [x] **Step 4: Insert warning-only close-loop command**

In `scripts/daily_close.sh`, change the step count from `1/12` to `1/13`, then insert this block after current-holdings coverage and before index-fund update:

```bash
# 4. 补目标池字段覆盖（失败不阻塞收盘闭环）
echo "4/13 补目标池字段覆盖"
"$PYTHON" -m src.data_pipeline.field_coverage_backfill \
  --scopes current_holdings,signal_candidates,target_universe \
  --skip-industry-fetch \
  --record-health || true
```

Renumber the later echo labels so the last step remains `13/13`.

- [x] **Step 5: Add matching Dashboard job-manager step**

In `src/dashboard/job_manager.py`, add a `SINGLE_STEPS["field_coverage"]` entry:

```python
"field_coverage": _step(
    "field_coverage",
    "补目标池字段覆盖",
    [
        PYTHON,
        "-m",
        "src.data_pipeline.field_coverage_backfill",
        "--scopes",
        "current_holdings,signal_candidates,target_universe",
        "--skip-industry-fetch",
        "--record-health",
    ],
    optional=True,
),
```

Place `SINGLE_STEPS["field_coverage"]` in `JOB_DEFINITIONS["daily_close"].steps` immediately after `SINGLE_STEPS["fundamentals_coverage"]`.

- [x] **Step 6: Verify**

Run:

```bash
python3.12 -m pytest tests/test_dashboard_runtime_scripts.py tests/test_dashboard_job_manager.py tests/test_field_coverage_backfill.py -q
python3.12 -m ruff check scripts/daily_close.sh src/dashboard/job_manager.py tests/test_dashboard_runtime_scripts.py tests/test_dashboard_job_manager.py
bash -n scripts/daily_close.sh
```

Expected result: tests pass, Ruff reports no Python issues, and Bash syntax check passes.

---

## Task 2: Make Signal Arbiter Consensus Baselines Configurable

**Files:**
- Modify: `config/settings.yaml`
- Modify: `src/signals/arbiter.py`
- Test: `tests/test_signal_arbiter.py`

- [x] **Step 1: Add config default**

Add this under `portfolio.signal_arbiter` in `config/settings.yaml`:

```yaml
    consensus_baselines:
      - alpha158
```

- [x] **Step 2: Add preserving-current-behavior test**

Add to `tests/test_signal_arbiter.py`:

```python
def test_alpha158_consensus_baseline_preserves_rule_buy_behavior(conn):
    _seed_production_prediction(conn, symbol="000001", rank=100, confidence=0.60)
    _insert_signal(
        conn,
        signal_id="trend-buy",
        model_name="trend_following",
        symbol="000001",
        side="BUY",
        confidence=0.90,
        score=0.90,
    )

    config = {
        "portfolio": {
            "min_rebalance_buy_confidence": 0.75,
            "min_rebalance_buy_rank_score": 0.50,
            "signal_arbiter": {
                "enabled": True,
                "consensus_baselines": ["alpha158"],
                "max_prediction_stale_days": 3,
                "max_rule_buy_rank": 500,
                "min_rule_buy_confidence": 0.45,
                "block_when_missing": True,
            },
        }
    }
    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=config)
    assert result.accepted == 1
    row = conn.execute("SELECT decision, consensus_status FROM signal_decisions WHERE signal_id = 'trend-buy'").fetchone()
    assert row == ("ACCEPTED", "CONSENSUS")
```

- [x] **Step 3: Add multi-baseline OR semantics test**

Add to `tests/test_signal_arbiter.py`:

```python
def test_rule_buy_accepts_when_any_configured_baseline_agrees(conn):
    _seed_model_registry(conn, model_name="alpha158", model_version="alpha158-prod")
    _seed_model_registry(conn, model_name="low_vol", model_version="low-vol-prod")
    _seed_prediction(conn, model_name="alpha158", model_version="alpha158-prod", symbol="000001", rank=650, confidence=0.30)
    _seed_prediction(conn, model_name="low_vol", model_version="low-vol-prod", symbol="000001", rank=120, confidence=0.62)
    _insert_signal(
        conn,
        signal_id="trend-buy-or",
        model_name="trend_following",
        symbol="000001",
        side="BUY",
        confidence=0.90,
        score=0.90,
    )

    config = {
        "portfolio": {
            "min_rebalance_buy_confidence": 0.75,
            "min_rebalance_buy_rank_score": 0.50,
            "signal_arbiter": {
                "enabled": True,
                "consensus_baselines": ["alpha158", "low_vol"],
                "max_prediction_stale_days": 3,
                "max_rule_buy_rank": 500,
                "min_rule_buy_confidence": 0.45,
                "block_when_missing": True,
            },
        }
    }
    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=config)
    assert result.accepted == 1
    reason = conn.execute("SELECT decision_reason FROM signal_decisions WHERE signal_id = 'trend-buy-or'").fetchone()[0]
    assert "low_vol" in reason
```

- [x] **Step 4: Add empty-baseline fallback test**

Add:

```python
def test_empty_consensus_baselines_disables_rule_buy_consensus_gate(conn):
    _insert_signal(
        conn,
        signal_id="trend-buy-no-baseline",
        model_name="trend_following",
        symbol="000001",
        side="BUY",
        confidence=0.90,
        score=0.90,
    )

    config = {
        "portfolio": {
            "min_rebalance_buy_confidence": 0.75,
            "min_rebalance_buy_rank_score": 0.50,
            "signal_arbiter": {
                "enabled": True,
                "consensus_baselines": [],
                "max_prediction_stale_days": 3,
                "max_rule_buy_rank": 500,
                "min_rule_buy_confidence": 0.45,
                "block_when_missing": True,
            },
        }
    }
    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=config)
    assert result.accepted == 1
    row = conn.execute("SELECT consensus_status FROM signal_decisions WHERE signal_id = 'trend-buy-no-baseline'").fetchone()
    assert row == ("NO_BASELINE_REQUIRED",)
```

- [x] **Step 5: Implement baseline loader**

Replace `_load_latest_qlib_predictions(conn)` with a general loader:

```python
def _consensus_baselines(config: dict) -> list[str]:
    arbiter_cfg = config.get("portfolio", {}).get("signal_arbiter", {})
    raw = arbiter_cfg.get("consensus_baselines", ["alpha158"])
    if raw is None:
        return ["alpha158"]
    return [str(item).strip() for item in raw if str(item).strip()]


def _load_latest_baseline_predictions(conn: duckdb.DuckDBPyConnection, model_names: list[str]) -> pd.DataFrame:
    if not model_names:
        return pd.DataFrame(columns=[
            "model_name",
            "symbol",
            "prediction_date",
            "rank",
            "confidence",
            "score",
            "model_version",
        ])
    placeholders = ",".join(["?"] * len(model_names))
    try:
        return conn.execute(
            f"""
            WITH production AS (
                SELECT model_name, model_version
                FROM qlib_model_registry
                WHERE status = 'production'
                  AND model_name IN ({placeholders})
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY model_name ORDER BY published_at DESC NULLS LAST, created_at DESC NULLS LAST
                ) = 1
            ),
            candidate_predictions AS (
                SELECT qp.model_name, qp.symbol, qp.prediction_date, qp.rank, qp.confidence,
                       qp.score, qp.model_version, qp.selected
                FROM qlib_predictions qp
                JOIN production p
                  ON p.model_name = qp.model_name
                 AND p.model_version = qp.model_version
                WHERE qp.mode = 'production_inference'
            )
            SELECT model_name, symbol, prediction_date, rank, confidence, score, model_version
            FROM candidate_predictions
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY model_name, symbol ORDER BY prediction_date DESC, selected DESC, rank ASC
            ) = 1
            """,
            model_names,
        ).fetchdf()
    except Exception:
        return pd.DataFrame(columns=[
            "model_name",
            "symbol",
            "prediction_date",
            "rank",
            "confidence",
            "score",
            "model_version",
        ])
```

- [x] **Step 6: Implement OR consensus decision**

Change `arbitrate_pending_signals` and `_build_decisions` so they pass `baseline_predictions` grouped by symbol. For rule BUY signals:

```python
baselines = _consensus_baselines(config)
if model_name in baselines:
    return {
        **common,
        "decision": ACCEPTED,
        "decision_reason": f"{model_name} production BUY 通过统一仲裁",
        "consensus_status": "BASELINE_SELF",
    }
if not baselines:
    return {
        **common,
        "decision": ACCEPTED,
        "decision_reason": "规则BUY通过统一仲裁；未配置共识基准",
        "consensus_status": "NO_BASELINE_REQUIRED",
    }
```

Then accept a rule BUY when any configured baseline is fresh and above `max_rule_buy_rank` plus `min_rule_buy_confidence`. Use the accepted baseline row for the existing `qlib_*` decision columns so no schema migration is needed in this task.

- [x] **Step 7: Update priority bonus**

Change:

```python
model_bonus = 0.2 if model_name == "alpha158" else 0.0
```

to:

```python
model_bonus = 0.2 if model_name in set(_consensus_baselines(config)) else 0.0
```

Pass `config` into `_priority_score` or compute the baseline set once and pass it down.

- [x] **Step 8: Verify**

Run:

```bash
python3.12 -m pytest tests/test_signal_arbiter.py -q
python3.12 -m ruff check src/signals/arbiter.py tests/test_signal_arbiter.py config/settings.yaml
```

Expected result: existing Alpha158 arbitration behavior remains unchanged when `consensus_baselines: ["alpha158"]`.

---

## Task 3: Add A Shared Alpha Gate For Research Candidates

**Files:**
- Create: `src/research/alpha_gate.py`
- Create: `tests/test_alpha_gate.py`
- Create: `scripts/run_alpha_tournament.py`

- [x] **Step 1: Add gate tests**

Create `tests/test_alpha_gate.py`:

```python
from src.research.alpha_gate import AlphaGateThresholds, evaluate_alpha_gate


def test_alpha_gate_passes_when_all_metrics_clear_thresholds():
    result = evaluate_alpha_gate(
        {
            "information_ratio": 0.36,
            "correlation_alpha158": 0.42,
            "correlation_benchmark": 0.61,
            "max_drawdown": -0.18,
            "annual_turnover": 0.82,
            "factor_coverage": 0.84,
        },
        thresholds=AlphaGateThresholds(),
    )
    assert result.passed is True
    assert result.failed_reasons == []


def test_alpha_gate_fails_with_named_reasons():
    result = evaluate_alpha_gate(
        {
            "information_ratio": 0.12,
            "correlation_alpha158": 0.69,
            "correlation_benchmark": 0.90,
            "max_drawdown": -0.41,
            "annual_turnover": 1.77,
            "factor_coverage": 0.50,
        },
        thresholds=AlphaGateThresholds(),
    )
    assert result.passed is False
    assert result.failed_reasons == [
        "information_ratio 0.12 < 0.30",
        "correlation_alpha158 0.69 > 0.50",
        "correlation_benchmark 0.90 > 0.70",
        "max_drawdown -0.41 < -0.25",
        "annual_turnover 1.77 > 1.00",
        "factor_coverage 0.50 < 0.80",
    ]
```

- [x] **Step 2: Implement gate module**

Create `src/research/alpha_gate.py`:

```python
"""Promotion gate for research-only alpha candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlphaGateThresholds:
    min_information_ratio: float = 0.30
    max_correlation_alpha158: float = 0.50
    max_correlation_benchmark: float = 0.70
    min_max_drawdown: float = -0.25
    max_annual_turnover: float = 1.00
    min_factor_coverage: float = 0.80


@dataclass(frozen=True)
class AlphaGateResult:
    passed: bool
    failed_reasons: list[str]
    metrics: dict[str, float | None]


def _metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_alpha_gate(
    metrics: dict[str, Any],
    thresholds: AlphaGateThresholds | None = None,
) -> AlphaGateResult:
    thresholds = thresholds or AlphaGateThresholds()
    values = {
        "information_ratio": _metric(metrics, "information_ratio"),
        "correlation_alpha158": _metric(metrics, "correlation_alpha158"),
        "correlation_benchmark": _metric(metrics, "correlation_benchmark"),
        "max_drawdown": _metric(metrics, "max_drawdown"),
        "annual_turnover": _metric(metrics, "annual_turnover"),
        "factor_coverage": _metric(metrics, "factor_coverage"),
    }
    failed: list[str] = []
    if values["information_ratio"] is None or values["information_ratio"] < thresholds.min_information_ratio:
        failed.append(f"information_ratio {values['information_ratio'] or 0:.2f} < {thresholds.min_information_ratio:.2f}")
    if values["correlation_alpha158"] is None or values["correlation_alpha158"] > thresholds.max_correlation_alpha158:
        failed.append(f"correlation_alpha158 {values['correlation_alpha158'] or 0:.2f} > {thresholds.max_correlation_alpha158:.2f}")
    if values["correlation_benchmark"] is None or values["correlation_benchmark"] > thresholds.max_correlation_benchmark:
        failed.append(f"correlation_benchmark {values['correlation_benchmark'] or 0:.2f} > {thresholds.max_correlation_benchmark:.2f}")
    if values["max_drawdown"] is None or values["max_drawdown"] < thresholds.min_max_drawdown:
        failed.append(f"max_drawdown {values['max_drawdown'] or 0:.2f} < {thresholds.min_max_drawdown:.2f}")
    if values["annual_turnover"] is None or values["annual_turnover"] > thresholds.max_annual_turnover:
        failed.append(f"annual_turnover {values['annual_turnover'] or 0:.2f} > {thresholds.max_annual_turnover:.2f}")
    if values["factor_coverage"] is None or values["factor_coverage"] < thresholds.min_factor_coverage:
        failed.append(f"factor_coverage {values['factor_coverage'] or 0:.2f} < {thresholds.min_factor_coverage:.2f}")
    return AlphaGateResult(passed=not failed, failed_reasons=failed, metrics=values)
```

- [x] **Step 3: Add tournament CLI shell**

Create `scripts/run_alpha_tournament.py`:

```python
#!/usr/bin/env python3
"""Run research-only alpha gate reports for candidate factors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.research.alpha_gate import evaluate_alpha_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate research alpha candidates against the shared gate.")
    parser.add_argument("--metrics-json", required=True, help="Path to a metrics JSON object produced by a validation script.")
    args = parser.parse_args(argv)

    metrics = json.loads(Path(args.metrics_json).read_text())
    result = evaluate_alpha_gate(metrics)
    payload = {
        "passed": result.passed,
        "failed_reasons": result.failed_reasons,
        "metrics": result.metrics,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Verify**

Run:

```bash
python3.12 -m pytest tests/test_alpha_gate.py -q
python3.12 -m ruff check src/research/alpha_gate.py tests/test_alpha_gate.py scripts/run_alpha_tournament.py
```

Expected result: gate pass/fail reasons are deterministic and reusable across factor tracks.

---

## Task 4: Build Research-Only Low-Vol And Cross-Reversal Tracks

**Files:**
- Create: `src/research/strategies/low_vol.py`
- Create: `src/research/strategies/cross_reversal.py`
- Create: `tests/test_low_vol_strategy.py`
- Create: `tests/test_cross_reversal_strategy.py`

- [x] **Step 1: Define low-vol scoring test**

Create `tests/test_low_vol_strategy.py`:

```python
import pandas as pd

from src.research.strategies.low_vol import compute_low_vol_scores


def test_low_vol_score_prefers_lower_realized_volatility():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"] * 2),
            "symbol": ["LOW"] * 4 + ["HIGH"] * 4,
            "close": [10.0, 10.1, 10.0, 10.1, 10.0, 12.0, 8.0, 13.0],
            "amount": [1000.0] * 8,
        }
    )
    scored = compute_low_vol_scores(prices, lookback=3)
    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-04")]
    low_score = float(latest.loc[latest["symbol"] == "LOW", "score"].iloc[0])
    high_score = float(latest.loc[latest["symbol"] == "HIGH", "score"].iloc[0])
    assert low_score > high_score
```

- [x] **Step 2: Define cross-reversal scoring test**

Create `tests/test_cross_reversal_strategy.py`:

```python
import pandas as pd

from src.research.strategies.cross_reversal import compute_cross_reversal_scores


def test_cross_reversal_prefers_recent_loser_within_industry():
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 2),
            "symbol": ["A"] * 3 + ["B"] * 3,
            "industry": ["tech"] * 6,
            "close": [10.0, 9.0, 8.0, 10.0, 11.0, 12.0],
            "amount": [1000.0] * 6,
        }
    )
    scored = compute_cross_reversal_scores(prices, lookback=2)
    latest = scored[scored["trade_date"] == pd.Timestamp("2026-01-03")]
    loser_score = float(latest.loc[latest["symbol"] == "A", "score"].iloc[0])
    winner_score = float(latest.loc[latest["symbol"] == "B", "score"].iloc[0])
    assert loser_score > winner_score
```

- [x] **Step 3: Implement low-vol scoring**

Create `src/research/strategies/low_vol.py` with:

```python
"""Research-only low-volatility factor."""
from __future__ import annotations

import pandas as pd

MODEL_NAME = "low_vol"


def compute_low_vol_scores(prices: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["trade_date", "symbol", "realized_vol", "liquidity", "score"])
    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["symbol", "trade_date"])
    df["return"] = df.groupby("symbol")["close"].pct_change()
    df["realized_vol"] = (
        df.groupby("symbol")["return"]
        .rolling(lookback, min_periods=max(2, min(lookback, 20)))
        .std()
        .reset_index(level=0, drop=True)
    )
    df["liquidity"] = (
        df.groupby("symbol")["amount"]
        .rolling(lookback, min_periods=max(2, min(lookback, 20)))
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["vol_rank"] = df.groupby("trade_date")["realized_vol"].rank(pct=True, ascending=True)
    df["liquidity_rank"] = df.groupby("trade_date")["liquidity"].rank(pct=True, ascending=True)
    df["score"] = 0.85 * (1.0 - df["vol_rank"]) + 0.15 * df["liquidity_rank"]
    return df[["trade_date", "symbol", "realized_vol", "liquidity", "score"]].dropna(subset=["score"])
```

- [x] **Step 4: Implement cross-reversal scoring**

Create `src/research/strategies/cross_reversal.py` with:

```python
"""Research-only industry-neutral cross-sectional reversal factor."""
from __future__ import annotations

import pandas as pd

MODEL_NAME = "cross_reversal"


def compute_cross_reversal_scores(prices: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["trade_date", "symbol", "industry", "lookback_return", "score"])
    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["industry"] = df["industry"].fillna("unknown").astype(str)
    df = df.sort_values(["symbol", "trade_date"])
    df["lookback_return"] = df.groupby("symbol")["close"].pct_change(periods=lookback)
    df["raw_reversal"] = -df["lookback_return"]
    df["score"] = df.groupby(["trade_date", "industry"])["raw_reversal"].rank(pct=True, ascending=True)
    return df[["trade_date", "symbol", "industry", "lookback_return", "score"]].dropna(subset=["score"])
```

- [x] **Step 5: Verify**

Run:

```bash
python3.12 -m pytest tests/test_low_vol_strategy.py tests/test_cross_reversal_strategy.py -q
python3.12 -m ruff check src/research/strategies/low_vol.py src/research/strategies/cross_reversal.py tests/test_low_vol_strategy.py tests/test_cross_reversal_strategy.py
```

Expected result: both factors are research-only modules and are not imported by `src/signals/generator.py`.

---

## Task 5: Upgrade Value-Quality To V0.2 Research-Only

**Files:**
- Modify: `src/research/strategies/value_quality.py`
- Modify: `src/research/strategies/value_quality_validation.py`
- Test: `tests/test_value_quality_strategy.py`
- Test: `tests/test_value_quality_validation.py`

- [x] **Step 1: Add industry-neutral score test**

Add to `tests/test_value_quality_strategy.py`:

```python
def test_value_quality_industry_neutral_score_compares_within_industry():
    fundamentals = pd.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "trade_date": pd.to_datetime(["2026-03-31"] * 4),
            "industry": ["bank", "bank", "tech", "tech"],
            "pe_ttm": [5.0, 8.0, 30.0, 50.0],
            "pb": [0.5, 0.8, 4.0, 8.0],
            "roe": [0.12, 0.08, 0.20, 0.10],
            "net_margin": [0.30, 0.20, 0.15, 0.05],
            "debt_ratio": [0.85, 0.88, 0.20, 0.35],
            "market_cap": [1e11, 8e10, 5e10, 3e10],
            "amount": [1e8, 1e8, 1e8, 1e8],
        }
    )
    scored = compute_value_quality_scores(fundamentals, industry_neutral=True)
    assert scored.loc[scored["symbol"] == "A", "score"].iloc[0] > scored.loc[scored["symbol"] == "B", "score"].iloc[0]
    assert scored.loc[scored["symbol"] == "C", "score"].iloc[0] > scored.loc[scored["symbol"] == "D", "score"].iloc[0]
```

- [x] **Step 2: Add stick-with-prior turnover test**

Add:

```python
def test_value_quality_keeps_prior_holding_inside_retention_band():
    scores = pd.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "score": [0.90, 0.88, 0.70],
        }
    )
    selected = select_value_quality_symbols(
        scores,
        top_n=2,
        prior_symbols={"C"},
        retention_quantile=0.30,
    )
    assert selected == ["A", "C"]
```

- [x] **Step 3: Implement v0.2 helpers**

Add these public helpers to `src/research/strategies/value_quality.py`:

```python
def industry_neutral_rank(df: pd.DataFrame, column: str, ascending: bool) -> pd.Series:
    return df.groupby("industry", dropna=False)[column].rank(pct=True, ascending=ascending)


def select_value_quality_symbols(
    scores: pd.DataFrame,
    top_n: int = 20,
    prior_symbols: set[str] | None = None,
    retention_quantile: float = 0.30,
) -> list[str]:
    prior_symbols = prior_symbols or set()
    ranked = scores.sort_values("score", ascending=False).copy()
    ranked["rank_pct"] = ranked["score"].rank(pct=True, ascending=False)
    retained = ranked[
        ranked["symbol"].astype(str).isin(prior_symbols)
        & (ranked["rank_pct"] <= retention_quantile)
    ]["symbol"].astype(str).tolist()
    fresh = ranked[~ranked["symbol"].astype(str).isin(retained)]["symbol"].astype(str).tolist()
    return (retained + fresh)[:top_n]
```

Update `compute_value_quality_scores` so `industry_neutral=True` ranks valuation and quality columns within `industry`, while preserving the current default behavior for existing tests.

- [x] **Step 4: Keep production wiring blocked**

Add this test to `tests/test_value_quality_strategy.py`:

```python
def test_value_quality_remains_research_only():
    import inspect
    import src.signals.generator as generator

    source = inspect.getsource(generator.generate_all)
    assert "value_quality" not in source
```

- [x] **Step 5: Add validation gate output**

In `src/research/strategies/value_quality_validation.py`, import `evaluate_alpha_gate` and add `alpha_gate_passed` plus `alpha_gate_failed_reasons` to the returned result dict.

- [x] **Step 6: Verify**

Run:

```bash
python3.12 -m pytest tests/test_value_quality_strategy.py tests/test_value_quality_validation.py tests/test_alpha_gate.py -q
python3.12 -m ruff check src/research/strategies/value_quality.py src/research/strategies/value_quality_validation.py tests/test_value_quality_strategy.py tests/test_value_quality_validation.py
```

Expected result: value-quality remains research-only until it passes the shared alpha gate.

---

## Task 6: Add Production Model Auto-Demotion

**Files:**
- Modify: `src/monitoring/model_monitor.py`
- Test: `tests/test_model_monitor.py`

- [x] **Step 1: Add demotion tests**

Add to `tests/test_model_monitor.py`:

```python
def test_auto_demote_demotes_after_consecutive_critical_alerts(conn):
    _seed_production_model(conn, model_version="alpha158-prod", experiment_id="EXP-PROD")
    for day in range(1, 9):
        conn.execute(
            """
            INSERT INTO model_monitor_alerts (
                alert_id, model_name, model_version, experiment_id, alert_date,
                severity, metric_name, status, message, updated_at
            )
            VALUES (?, 'alpha158', 'alpha158-prod', 'EXP-PROD', ?, 'CRITICAL',
                    'production_model_unhealthy', 'ACTIVE', 'critical', CURRENT_TIMESTAMP)
            """,
            [f"A-{day}", date(2026, 5, day)],
        )

    result = auto_demote_production_model(conn, model_name="alpha158", min_consecutive_days=8, as_of=date(2026, 5, 8))
    assert result["demoted"] is True
    status = conn.execute("SELECT status FROM qlib_model_registry WHERE model_version = 'alpha158-prod'").fetchone()[0]
    assert status == "staging"


def test_auto_demote_does_not_demote_when_critical_streak_is_broken(conn):
    _seed_production_model(conn, model_version="alpha158-prod", experiment_id="EXP-PROD")
    for day in [1, 2, 3, 5, 6, 7, 8]:
        conn.execute(
            """
            INSERT INTO model_monitor_alerts (
                alert_id, model_name, model_version, experiment_id, alert_date,
                severity, metric_name, status, message, updated_at
            )
            VALUES (?, 'alpha158', 'alpha158-prod', 'EXP-PROD', ?, 'CRITICAL',
                    'production_model_unhealthy', 'ACTIVE', 'critical', CURRENT_TIMESTAMP)
            """,
            [f"A-{day}", date(2026, 5, day)],
        )

    result = auto_demote_production_model(conn, model_name="alpha158", min_consecutive_days=8, as_of=date(2026, 5, 8))
    assert result["demoted"] is False
    status = conn.execute("SELECT status FROM qlib_model_registry WHERE model_version = 'alpha158-prod'").fetchone()[0]
    assert status == "production"
```

- [x] **Step 2: Implement demotion function**

Add to `src/monitoring/model_monitor.py`:

```python
def auto_demote_production_model(
    conn: Any,
    model_name: str = "alpha158",
    min_consecutive_days: int = 8,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    production = _load_production_model(conn)
    if production is None or production.get("model_name") != model_name:
        return {"demoted": False, "reason": "production_model_missing"}
    rows = conn.execute(
        """
        SELECT DISTINCT alert_date
        FROM model_monitor_alerts
        WHERE model_name = ?
          AND model_version = ?
          AND severity = 'CRITICAL'
          AND status = 'ACTIVE'
          AND alert_date <= ?
        ORDER BY alert_date DESC
        """,
        [model_name, production["model_version"], as_of],
    ).fetchall()
    critical_days = {pd.Timestamp(row[0]).date() for row in rows}
    streak = 0
    cursor = as_of
    while cursor in critical_days:
        streak += 1
        cursor = (pd.Timestamp(cursor) - pd.Timedelta(days=1)).date()
    if streak < min_consecutive_days:
        return {"demoted": False, "reason": "critical_streak_too_short", "critical_streak": streak}
    conn.execute(
        """
        UPDATE qlib_model_registry
        SET status = 'staging'
        WHERE model_name = ?
          AND model_version = ?
          AND status = 'production'
        """,
        [model_name, production["model_version"]],
    )
    return {
        "demoted": True,
        "model_name": model_name,
        "model_version": production["model_version"],
        "critical_streak": streak,
    }
```

- [x] **Step 3: Add CLI command**

Extend `main()` with:

```python
p_demote = sub.add_parser("auto-demote", help="demote unhealthy production model after consecutive CRITICAL alerts")
p_demote.add_argument("--model-name", default="alpha158")
p_demote.add_argument("--min-consecutive-days", type=int, default=8)
p_demote.add_argument("--as-of", default=None)
```

and:

```python
if args.command == "auto-demote":
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        result = auto_demote_production_model(
            conn,
            model_name=args.model_name,
            min_consecutive_days=args.min_consecutive_days,
            as_of=as_of,
        )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0
```

- [x] **Step 4: Verify**

Run:

```bash
python3.12 -m pytest tests/test_model_monitor.py -q
python3.12 -m ruff check src/monitoring/model_monitor.py tests/test_model_monitor.py
```

Expected result: auto-demotion is explicit, test-covered, and not triggered by WARN-only alerts.

---

## Task 7: Decide And Prepare Dashboard V2 Single-Track Migration

**Files:**
- Modify: `docs/iteration_backlog.md`
- Modify: `README.md`
- Optional later modify: `scripts/daily_close.sh`
- Optional later create: `scripts/install_dashboard_v2_launch_agent.sh`

- [x] **Step 1: Record the migration policy**

Add this decision to `docs/iteration_backlog.md`:

```markdown
### Dashboard Single-Track Policy

- Daily operation entrypoint: Dashboard V2 `/today`.
- Legacy Streamlit role: research fallback only.
- Production close/open tasks must not depend on Streamlit pages being live.
- Migration gate: Dashboard V2 must show today action, rebalance, portfolio, health, research summary, user guide, scheduler history, model monitor alerts, and signal outcome summaries before Streamlit restart logic is removed from production scripts.
```

- [x] **Step 2: Add README entrypoint clarity**

In `README.md`, ensure the top quickstart says:

```markdown
日常入口使用 Dashboard V2：

```bash
scripts/run_dashboard_v2.sh
```

打开 `http://localhost:5173/today`。旧 Streamlit 仅作为研究兜底入口，不作为每日操作入口。
```

- [x] **Step 3: Defer code switch until parity checklist passes**

Do not change `scripts/daily_close.sh` dashboard restart behavior in this task. The code switch should happen in a later commit after the migration policy checklist is true in production.

- [x] **Step 4: Verify docs**

Run:

```bash
rg -n "Dashboard Single-Track Policy|http://localhost:5173/today|旧 Streamlit" README.md docs/iteration_backlog.md
```

Expected result: both README and backlog clearly state V2 is the daily entrypoint and Streamlit is research fallback.

---

## Task 8: Expand The Retail User Guide With Investor Education

**Files:**
- Modify: `docs/dashboard_v2_user_guide.md`
- Test: `frontend/dashboard-v2/src/pages/UserGuidePage.tsx` only if rendering fails

- [x] **Step 1: Add section 0**

Insert a new section before the current introduction:

```markdown
## 0. 在你开始之前

### 0.1 这套系统能做什么，不能做什么

| 能做 | 不能做 |
|---|---|
| 自动化收盘检查、信号生成、风险过滤、资金分配和纸盘复盘 | 保证每年正收益 |
| 帮你用纪律化流程追求年化跑赢核心指数 3–8% | 让你一夜暴富 |
| 把每次买卖背后的数据口径和风险理由留痕 | 替你承担最终投资责任 |

### 0.2 你应该预期什么

| 维度 | 合理预期 |
|---|---:|
| 年化超额收益 | 3–8%，对比沪深 300、中证 500、恒生科技等核心指数 |
| 单年最大回撤 | 20–25% |
| 看到效果所需时间 | 12 个月以上 |
| 任意 3 个月跑输概率 | 约 35% |

### 0.3 什么情况下应该停止跟单

| 停止条件 | 动作 |
|---|---|
| 连续 6 个月跑输基准 5pp 以上 | 暂停新买入，只保留复盘和数据更新 |
| 连续 3 个 daily_close 失败且未修复 | 暂停调仓，先修复数据链路 |
| 半年内有明确大额用钱需求 | 降低仓位或停止跟单 |
```

- [x] **Step 2: Add first-use onboarding section**

Add:

```markdown
## 1.5 第一次使用：从零到首次调仓

| 问题 | 建议 |
|---|---|
| 最低多少资金值得用 | 5 万可以观察，10 万以上更适合实盘跟单，因为 A 股一手门槛会卡掉小账户 |
| 已有持仓怎么处理 | 先录入或同步到纸盘，再让系统给出减仓、持有、加仓建议 |
| 第一次是否一次性买齐 | 不建议。第一周先执行 Core 基金和最高置信度 Satellite，剩余仓位分 2–4 周补齐 |
| 资金档位 | small ≤10 万，medium 10–50 万，large ≥50 万 |
| 冷启动信号能不能信 | 数据、模型、信号收益跟踪都连续正常 5 个交易日后，再按正式流程执行 |
```

- [x] **Step 3: Add signal outcome interpretation**

Add under the portfolio-health section:

```markdown
### 如何读信号收益跟踪

| 指标 | 健康区间 | 解读 |
|---|---:|---|
| T+5 alpha_vs_benchmark | > 0 | 短期信号没有明显拖累 |
| T+20 alpha_vs_benchmark | > 0 | 月度调仓逻辑仍有效 |
| hit rate | 50–60% 正常，低于 45% 警惕 | 命中率不是越高越好，关键是平均超额为正 |
| model_name 对比 | Alpha158、trend、mean_reversion、industry_rotation 分开看 | 某个模型连续为负，说明该模型需要降权或重训 |
```

- [x] **Step 4: Add weekly review quantitative thresholds**

Add:

```markdown
### 本周复盘的量化门槛

| 信号 | 警惕门槛 | 建议动作 |
|---|---:|---|
| 60 日滚动 ICIR | 较基线下降 50% 且持续 2 周 | 暂停新增 Satellite BUY |
| 暂缓比例 | 大于 60% 且持续 2 周 | 检查预算、停牌、涨跌停和一手门槛 |
| 信号冲突数 | 每周 ≥3 且持续 2 周 | 降低规则策略权重，优先看 arbiter 决策 |
| 纸盘落后基准 | 30 日累计落后 ≥5pp | 暂停扩仓，复盘成交和模型告警 |
| 严重风险警告 | CRITICAL ≥2 个且持续 3 天 | 停止 BUY，优先处理风险 |
```

- [x] **Step 5: Add emergency scenarios and privacy note**

Add:

```markdown
## 7.5 异常场景应急

| 场景 | 处理方式 |
|---|---|
| 出差 3 天回来 | 不补做旧调仓。从当天 `/today` 重新开始，避免追旧信号 |
| daily_close 连续失败一周 | 暂停调仓，先看 `/health` 的失败步骤和日志摘要 |
| 券商账户资金冻结或停牌 | 纸盘照常记录，实盘不同步执行，后续用现金流和持仓快照校正 |
| 换电脑或重装系统 | 备份 DuckDB 数据文件和 `config/settings.yaml`，恢复后先跑 Dashboard V2 自检 |

## 安全与隐私

所有交易、持仓和模型数据默认存在本机 DuckDB。Dashboard V2 不会把你的持仓上传到外部服务。AkShare、yfinance、腾讯财经等免费源只用于下载行情和字段数据。
```

- [x] **Step 6: Verify guide rendering**

Run:

```bash
rg -n "在你开始之前|第一次使用|如何读信号收益跟踪|本周复盘的量化门槛|异常场景应急|安全与隐私" docs/dashboard_v2_user_guide.md
cd frontend/dashboard-v2 && npm test -- --run
```

Expected result: markdown contains all new sections and frontend tests still pass.

---

## Execution Order

1. Task 1: Field coverage in close loop.
2. Task 2: Configurable arbiter consensus.
3. Task 3: Shared alpha gate.
4. Task 8: User guide expansion, because it is cheap and reduces operational misuse.
5. Task 4: Low-vol and cross-reversal research-only factors.
6. Task 5: Value-quality v0.2 research-only upgrade.
7. Task 6: Auto-demote.
8. Task 7: Dashboard single-track decision and later code switch.

## Production Guardrails

- Do not import `value_quality`, `low_vol`, or `cross_reversal` from `src/signals/generator.py` until the shared alpha gate passes.
- Do not add new factors to `portfolio.signal_arbiter.consensus_baselines` until they have a production inference table compatible with `qlib_predictions` or a deliberate equivalent storage design.
- Do not let field coverage failure block `daily_close`; it should write `data_source_health` and keep the close chain moving.
- Do not change existing paper-order history while implementing governance.
- Preserve the current open-trade reliability changes already present in the workspace.

## Verification Gate For The Whole Plan

After all selected P0/P1 tasks are implemented, run:

```bash
python3.12 -m pytest -q
python3.12 -m ruff check .
cd frontend/dashboard-v2 && npm test -- --run && npm run build
bash -n scripts/daily_close.sh
```

Expected result: Python tests pass, Ruff passes, frontend tests/build pass, and `daily_close.sh` is syntactically valid.

## Recommended Commit Slices

1. `chore: add field coverage to close workflow`
2. `feat: configure signal arbiter consensus baselines`
3. `feat: add shared alpha gate`
4. `docs: expand dashboard user guide`
5. `feat: add research-only low vol and reversal factors`
6. `feat: improve value quality research validation`
7. `feat: add production model auto demotion`
8. `docs: define dashboard v2 single-track policy`
