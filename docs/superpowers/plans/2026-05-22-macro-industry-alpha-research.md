# Macro And Industry Alpha Research Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add the first research-only macro and industry alpha building blocks for a low-frequency retail workflow without changing production trading behavior.

**Architecture:** Keep new models isolated under `src/research/`. They produce regime states and research candidate signals, but they are not imported by `src/signals/generator.py`, do not write to `signals`, and do not consume `satellite_budget` until separate validation passes the shared alpha gate.

**Tech Stack:** Python 3.12, pandas, DuckDB source data, pytest, Ruff.

---

## Execution Boundary

This plan intentionally stops at research/shadow components:

- No changes to `src/signals/generator.py`.
- No changes to `src/portfolio/allocator.py`.
- No changes to `scripts/daily_close.sh`.
- No production trading or paper execution behavior changes.

Promotion path for later work:

1. Add validation/backtest metrics for each candidate.
2. Run the shared gate in `src.research.alpha_gate`.
3. Only after passing gate thresholds, add a config-gated production integration.

## Task 1: Market Regime Model

**Files:**
- Create: `src/research/market_regime.py`
- Test: `tests/test_market_regime.py`

- [x] Implemented `compute_market_regime(...)` for index-level risk states.
- [x] Implemented `latest_market_regime(...)` unknown fallback.
- [x] Added tests for risk-on, risk-off, empty input, benchmark filtering, and non-benchmark-only input.
- [x] Reviewed and fixed benchmark fallback so non-benchmark data cannot silently classify the wrong index.

## Task 2: Industry Relative Momentum Model

**Files:**
- Create: `src/research/strategies/industry_relative_momentum.py`
- Test: `tests/test_industry_relative_momentum_strategy.py`

- [x] Implemented `compute_industry_momentum_scores(...)`.
- [x] Implemented `generate_industry_momentum_signals(...)` as research-only candidate output.
- [x] Added tests for industry ranking, within-industry ranking, latest-only output, all-date output, and minimum industry-member filtering.
- [x] Reviewed and fixed `latest_only` ordering so stale older qualifying rows are never emitted when the latest scored date has no qualifying rows.

## Task 3: Integration Guardrails

**Files:**
- Read-only check: `src/signals/generator.py`
- Read-only check: `src/portfolio/allocator.py`
- Read-only check: `config/settings.yaml`

- [x] Confirmed new modules are not wired into production signal generation.
- [x] Confirmed new modules do not affect core/satellite allocation or `satellite_budget`.
- [x] Confirmed future integration should be research summary / alpha tournament first, not daily trading.

## Task 4: Shared Research Alpha Validation

**Files:**
- Create: `src/research/alpha_validation.py`
- Create: `scripts/validate_research_alpha.py`
- Test: `tests/test_research_alpha_validation.py`

- [x] Added shared score-panel validation for research-only cross-sectional candidates.
- [x] Reused the production backtest metric helpers and shared alpha gate thresholds.
- [x] Added DB price-panel loading with stock name, industry, market cap, PE/PB, and tradeability fields.
- [x] Added CLI entry point for `low_vol`, `cross_reversal`, and `industry_relative_momentum`, including `lookback` and `buffer_n` experiment knobs.
- [x] Kept candidates research-only: validation reports gate status but does not write production signals.
- [x] Ran first real-data validation pass and recorded results in `docs/research_alpha_validation_2026-05-22.md`.
- [x] Ran `cross_reversal_60d`专项优化 with replacement caps, smoothing, quarterly diagnostics, and size-neutral residual scoring. No variant passed gate; keep research-only.
- [x] Ran beta-neutral residualization专项. It improved IR for one variant but did not lower Alpha158/benchmark correlation enough; keep research-only and move next work toward ensemble overlay gating.

## Verification

Focused verification:

```bash
python3.12 -m pytest tests/test_market_regime.py tests/test_industry_relative_momentum_strategy.py tests/test_low_vol_strategy.py tests/test_cross_reversal_strategy.py tests/test_alpha_gate.py -q
python3.12 -m pytest tests/test_research_alpha_validation.py -q
ruff check src/research/market_regime.py tests/test_market_regime.py src/research/strategies/industry_relative_momentum.py tests/test_industry_relative_momentum_strategy.py
ruff check src/research/alpha_validation.py scripts/validate_research_alpha.py tests/test_research_alpha_validation.py
rg -n "market_regime|industry_relative_momentum" src/signals src/portfolio scripts config/settings.yaml src/dashboard_v2 || true
```

Expected:

- Focused tests pass.
- Ruff passes.
- `rg` returns no production integration references.
