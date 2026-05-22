# Regime Policy Layer Rollout

> **Status:** Implemented as opt-in production policy layer. Default config keeps existing trading behavior unchanged until `portfolio.regime_policy.enabled=true`.

## Goal

Keep macro/regime control independent from Alpha158 and rule factors. The layer does not generate stock alpha; it controls portfolio stance, cash target, satellite budget, and BUY permission when market-level risk changes.

## Design Boundary

- Signal generation remains pure alpha output. Macro policy does not suppress signal creation.
- SELL/SHORT remains risk-release first and is not blocked by macro risk-off states.
- BUY is controlled in two places:
  - `src/signals/arbiter.py`: close-time admission gate.
  - `src/portfolio/paper_engine.py`: open-time fallback gate for already accepted/stale decisions.
- Portfolio structure is controlled in `src/portfolio/allocator.py` by applying policy targets to Core/Satellite/Cash.
- Dashboard exposes `regime_policy` as its own object; it is not mixed into cash balances or single-stock signal rows.

## Implemented

- [x] Added `src/portfolio/regime_policy.py`.
- [x] Added DB-backed `load_latest_regime_policy(...)` using `index_daily`.
- [x] Added crisis classification to `src/research/market_regime.py` for extreme one-day drawdowns / high-vol drawdown states.
- [x] Added `cash_target_pct` to allocation config, plan objects, schema, migration, and persistence.
- [x] Wired allocator CLI to apply policy only when `portfolio.regime_policy.enabled=true`.
- [x] Wired signal arbiter to reject BUY in `risk_off/crisis/unknown` or below policy confidence floor.
- [x] Wired paper engine fallback to reject BUY if an old accepted decision would violate latest policy.
- [x] Added Dashboard V2 `regime_policy` API object and lightweight UI panel on Today/Rebalance/Portfolio/Health.

## Default Profiles

| State | Core | Satellite | Cash | BUY Mode |
|---|---:|---:|---:|---|
| risk_on | 50% | 45% | 5% | Normal |
| neutral | 60% | 30% | 10% | High confidence only |
| defensive | 70% | 15% | 15% | Restricted |
| risk_off | 70% | 10% | 20% | Sell only |
| crisis | 50% | 0% | 50% | Sell only |
| unknown | 60% | 30% | 10% | Data blocked |

## Rollout Plan

1. Keep disabled by default for one or more dry-run days.
2. Turn on `portfolio.regime_policy.enabled=true` after checking Dashboard `regime_policy.application_state`.
3. Review next daily close allocation plan:
   - `cash_target_pct` should persist in `allocation_plans`.
   - `regime_policy.application_state` should become `applied_to_plan`.
   - BUY candidates should be reduced or paused according to state.
4. Review next open paper trade:
   - BUY blocked by macro policy should become `NO_ACTION`.
   - SELL should remain executable.

## Verification

Focused checks:

```bash
python3.12 -m pytest tests/test_regime_policy.py tests/test_market_regime.py tests/test_signal_arbiter.py tests/test_allocator.py::test_resolve_regime_policy_for_plan_is_opt_in_and_reads_index_daily tests/test_open_trade_workflow.py::test_paper_engine_regime_policy_blocks_previously_accepted_buy tests/test_dashboard_v2_service.py::test_dashboard_v2_exposes_regime_policy_without_mixing_money_vocab -q
ruff check src/portfolio/regime_policy.py src/research/market_regime.py src/signals/arbiter.py src/portfolio/allocator.py src/portfolio/paper_engine.py src/dashboard_v2/service.py
```
