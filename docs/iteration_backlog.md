# make-money Iteration Backlog

Review date: 2026-05-16
Last reviewed: 2026-05-16
Review cadence: weekly after Friday close, plus ad-hoc review after any P0 production change

This file is the durable project backlog. Keep it small enough to review every week, and link each active item to a design, implementation plan, test evidence, and final outcome.

## Operating Rules

- Status values: `Proposed`, `Ready`, `In Progress`, `Blocked`, `Done`, `Deferred`.
- Only `Ready` items may enter implementation. `Ready` means scope, files, acceptance checks, and rollback notes are written down.
- Each active item must have one owner, one priority, and one next action.
- When an item is completed, record the verification command and the observed result.
- Do not mix research-only metrics with production decision metrics unless the comparison scope is explicitly labeled.
- P0 work protects trustworthiness: survivorship control, tradability realism, engine comparability, and daily workflow consistency.
- Data-source policy for the first six-month validation period, through 2026-11-16: use free or public data sources by default. Paid sources such as Tushare, Wind, Choice, JoinQuant, or RiceQuant require explicit approval and a written reason that no free/public route can satisfy the validation need.

## Current Baseline

- Latest pushed commit before P1-07: `5faaee1` (`feat: add manual core fund execution plan`).
- Review v2 baseline correction: the external v2 review used `origin/main` (`23b8178`) and did not include local commits `93e9995` and `9e000f4`.
- Test baseline: `pytest -q` passed with 181 tests on 2026-05-16.
- Lint baseline: `ruff check .` passed on 2026-05-16.
- Durable rule: before starting new alpha work, finish the survivorship-bias impact report so future backtests have a trust anchor.
- Validation-period data stance: do not assume Tushare or other paid data sources are available; prefer Baostock snapshots plus official public adjustment announcements for index membership history.

## Current Priority Backlog

This section is the executable queue after the 2026-05-16 v2 review reconciliation. Items are intentionally ordered by decision impact, not by implementation size.

### P0 - Trustworthiness Gates

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P0-06 | Guard Qlib production readiness | Done | Codex | Monitor next `predict-latest` / `refresh-production` output for publish-gate skips | Qlib training refuses static current-only universes; publish and production inference reject models below IC/excess/drawdown gates |
| P0-07 | Import reliable historical index membership archives | Done | Codex | Re-run after each future membership refresh or source change | `build_membership_coverage_report` shows earliest coverage <= 2020-01-01 and active counts of 300/500; sample validation against public adjustment announcements is recorded in `docs/index_membership_sample_validation.md` |
| P0-08 | Produce survivorship impact report v2 | Done | Codex | Use PIT universe as the default trust anchor for future model PK and promotion discussions | `docs/survivorship_impact_v2.md` reports annual return, excess return, Sharpe, max drawdown, and turnover delta between static and point-in-time universes |
| P0-09 | One-week daily close monitoring | In Progress | Codex | Continue the 2026-05-18 to 2026-05-22 close-run checks in `docs/daily_close_monitoring.md` | 5 consecutive close runs complete or produce actionable failure notes; track `skipped_untradeable`, `skipped_turnover`, `skipped_budget`, signal outcome updates |

### P1 - Execution And Risk Loop

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P1-05 | Automate current-holding fundamentals coverage | Done | Codex | Monitor the next daily close run for the new non-blocking fundamentals step | Current holdings have 0 missing values for the four fields after daily close; failures are logged per symbol without blocking the close |
| P1-06 | Core fund execution planning v2 | Done | Codex | Monitor the next allocation plan output in Dashboard before any manual core fund operation | Dashboard shows actionable core fund BUY/ADD/REDUCE plan with budget consumption and no mutation of paper orders |
| P1-07 | Exposure monitor quality thresholds | Done | Codex | Monitor exposure warnings during the next paper-trade cycle | Dashboard flags exposure risks with deterministic thresholds and tests for each warning state |
| P1-08 | Daily close failure diagnostics | Proposed | Codex | Persist per-step close status and stderr excerpts to a durable job run table/file | Dashboard can show latest failed step, command, return code, and last log excerpt without reading terminal history |

### P2 - Feedback And Alpha Expansion

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P2-04 | Signal outcomes Dashboard | Ready | Codex | Add model_name x month x horizon tables/charts for hit rate, average return, and sample count | Dashboard exposes T+1/T+5/T+20 realized signal performance with empty-state handling |
| P2-05 | Extend signal outcomes to benchmark-relative returns | Proposed | Codex | Add benchmark return lookup and `alpha_vs_benchmark`; consider T+60 only after enough history exists | `signal_outcomes` can report raw return and benchmark-relative alpha per signal/horizon |
| P2-06 | Value-quality fundamental factor prototype | Proposed | Codex | Design financial data ingestion and a small `value_quality.py` strategy using existing `financials`, `pe_ttm`, and `pb` fields | 2022-2025 standalone backtest exists and correlation with Alpha158/technical sleeve is measured |
| P2-07 | Qlib PortAna artifact | Proposed | Codex | Verify installed Qlib report APIs and add optional HTML artifact output | Successful Qlib runs can link to a saved attribution/position report artifact |
| P2-08 | Environment-specific config loading | Proposed | Codex | Design `MM_ENV` config merge order | `config/settings.dev.yaml` and `config/settings.prod.yaml` override safely without breaking default local runs |

### P3 - Retail Usability And Hygiene

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P3-04 | Small-account risk profiles | Proposed | Codex | Design profile interaction with allocator, lot-size skips, and max holdings | 50k/100k/300k modes produce realistic position counts and explicit one-lot funding hints |
| P3-05 | Weekly operation summary card | Proposed | Codex | Add weekly suggested actions count, required cash, and estimated manual time to the weekly report | Retail user can see "operate N times / need cash Y / expected time T" before reading individual signals |
| P3-06 | Retail user manual | Proposed | Codex | Draft after P0-08 and P1-06 stabilize | `docs/user_guide.md` explains weekly workflow, follow/ignore rules, failure modes, and manual execution discipline in <= 20 pages |
| P3-07 | Strategy math unit tests expansion | Proposed | Codex | Add direct tests for ATR, RSI, Bollinger bands, and IC edge cases | Focused tests cover warmup, gaps, flat series, NaN handling, and known toy examples |

## Active P0 Track

| ID | Item | Status | Owner | Design | Acceptance |
|---|---|---|---|---|---|
| P0-01 | Add historical index membership and use it for Qlib instruments | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Snapshot-backed `index_member_history`, dynamic Qlib instruments, CSIndex snapshot reconciliation, and historical archive CSV import are implemented |
| P0-02 | Apply open-tradability guards for backtest and paper trading | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | ST, suspended, and A-share limit-up/limit-down opens are not treated as fillable orders |
| P0-03 | Make vectorbt results non-comparable by default, with explicit engine labels | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Strategy comparison excludes research-only vectorbt rows unless explicitly requested |
| P0-04 | Unify daily production entry points around the full close workflow | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Daily update entry point cannot skip signal generation, Qlib prediction, paper trading, or NAV rebuild by accident |
| P0-05 | Add rolling IC decay view on existing `qlib_daily_metrics` | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Dashboard shows 30/60/180 trading-day rolling RankIC and IC means for successful experiments |

## Implementation Notes

### 2026-05-15 P0 Trustworthiness Pass

- Landed: `index_member_history` schema, membership helper module, snapshot persistence in `init_all`, dynamic Qlib `all/csi300/csi500/csi800` instrument files, and Alpha158 default universe switched to `csi800`.
- Landed next step: CSIndex dated snapshot ingestion and reconciliation. Repeated updates now accumulate real add/remove ranges by closing removed members at T-1 and opening new members at T.
- Landed P0 closeout: historical membership interval archives can be imported from CSV/XLS/XLSX via `scripts/backfill_index_membership.py`, with `docs/index_membership_coverage.md` coverage output.
- Landed: shared open execution guard for invalid open price, ST, suspension, and A-share opening limit moves; consumed by Qlib simulations and paper trading.
- Landed: `backtest_results.engine` and `backtest_results.decision_scope`; vectorbt saves as `research_only`, comparator excludes it by default.
- Landed: `scripts/daily_update.py` delegates to `scripts/daily_close.sh`, preserving proxy cleanup and full close workflow.
- Landed: Dashboard IC tab now shows 30/60/180 trading-day rolling RankIC and IC decay columns from `qlib_daily_metrics`.
- Remaining P0 data task: populate the archive importer with official historical adjustment files when available; the system-side ingestion path is now in place.
- P1 design started: core-satellite allocator design is drafted at `docs/superpowers/specs/2026-05-15-p1-core-satellite-allocator-design.md`.
- Verification: `pytest -q` passed with 133 tests on 2026-05-15; `ruff check .` passed after enabling the local Ruff baseline.

### 2026-05-16 Review v2 Reconciliation And P0' Closeout

- Baseline correction: the v2 review inspected `origin/main` and missed local commit `93e9995`, so P1 exposure monitoring, BUY turnover cap, and basic signal outcomes were already done locally.
- Landed in `9e000f4`: Qlib production readiness guard now rejects static current-only universes and dynamic instruments without closed historical membership ranges.
- Landed in `9e000f4`: model publish and production inference now use configurable gates for IC mean, ICIR, excess return, and max drawdown.
- Decision: keep dynamic universe filtering in Qlib instruments generated by `scripts/convert_to_qlib.py`; `qlib_runner.py` acts as a guard instead of reimplementing a second per-date universe join inside every simulator.
- Remaining P0 trust gap: the code path is guarded, but the historical archive itself still needs reliable 000300/000905 interval data before the survivorship impact report can be produced.
- Verification: `ruff check .` passed; `pytest -q` passed with 160 tests; commit hook passed with Ruff plus 65-test smoke suite.

### 2026-05-16 P0-07 Free Historical Membership Fetcher

- Landed: `scripts/fetch_index_membership_baostock.py` converts free Baostock monthly snapshots for `000300` and `000905` into the existing `index_code,symbol,start_date,end_date,source` archive format.
- Landed: pure tests cover month-end snapshot generation, Baostock result conversion, exchange-prefix symbol normalization, and add/remove/re-entry interval construction.
- Landed: `scripts/backfill_index_membership.py --replace-indexes` can replace synthetic current snapshot rows when importing an authoritative historical archive, preventing duplicated active member counts.
- Dependency stance: `baostock>=0.9.1` is recorded as a free data dependency, but local Homebrew Python blocks global pip installs; use a project environment or temporary target install before running the real fetch.
- Real fetch/import: Baostock monthly snapshots for 2020-01-01 through 2026-05-16 generated 1,621 interval rows. Imported rows produce active counts `000300=300` and `000905=500`; Qlib `csi300/csi500/csi800` dynamic instrument checks pass after `prepare-data`.
- Sample validation: public adjustment examples from 2020-12, 2023-12, and 2024-06 match local Baostock intervals in direction and month-end timing; see `docs/index_membership_sample_validation.md`.
- Verification evidence: `pytest tests/test_baostock_membership_fetcher.py tests/test_index_membership_backfill.py -q` passed; `ruff check scripts/fetch_index_membership_baostock.py tests/test_baostock_membership_fetcher.py pyproject.toml docs/iteration_backlog.md` passed; full `pytest -q` passed with 164 tests before the import-replace fix.
- Landed P0-08: `docs/survivorship_impact_v2.md` compares current static constituents with the Baostock point-in-time archive on the production Alpha158 predictions. Static current constituents overstate annual return by 6.29 pp in the 2024-01-01 to 2026-05-14 sample, so future model PK/promotion discussion should treat PIT universe results as the trust anchor.
- Remaining caveat: Baostock membership is monthly snapshot history, so official adjustment effective dates are approximated to month-end; this is acceptable for the six-month free-data validation period but should be revisited before paid-data-grade audit.
- Started P0-09: `docs/daily_close_monitoring.md` records the 2026-05-16 weekend baseline run. The latest `daily_close_workflow` succeeded 11/11 steps; paper trading executed 0/261 signals because there was no trading day after 2026-05-15 in local data, so it is a useful baseline but not counted toward the 5 trading-day target.

### 2026-05-16 P1-05 Current Holding Fundamentals Coverage

- Landed: `src.portfolio.fundamentals_coverage` checks latest positive `paper_positions` and fills missing `stock_info.industry`, `stock_info.market_cap`, `daily_price.pe_ttm`, and `daily_price.pb` for current holdings only.
- Data-source behavior: the updater uses AkShare A-share spot data for market cap/PE/PB and per-symbol individual info for industry fallback; existing non-empty fields are preserved unless `--force` is used.
- Operational behavior: `scripts/daily_close.sh` runs the updater 在行情更新后、信号生成前 with `|| true`; Dashboard `daily_close_workflow` has a matching `fundamentals_coverage` step before `generate_signals`.
- Real local check: `python -m src.portfolio.fundamentals_coverage update` returned `status=OK` for 13 current holdings with 0 missing `industry/market_cap/pe_ttm/pb` fields and no external fetch needed.
- Verification evidence: `pytest tests/test_fundamentals_coverage.py tests/test_dashboard_job_manager.py tests/test_dashboard_runtime_scripts.py -q` passed; `bash -n scripts/daily_close.sh` passed; targeted `ruff check` passed.

### 2026-05-16 P1-06 Core Fund Manual Execution Plan

- Landed: `allocation_plan_items` now records `execution_mode`, `expected_cash`, `cash_effect`, and `budget_consumption` for each plan item.
- Landed: core index-fund BUY/ADD/REDUCE rows are explicit `MANUAL` execution plans; BUY/ADD consume core budget and show cash outflow, REDUCE shows expected cash inflow without consuming budget.
- Landed: Portfolio Dashboard `Core 执行计划` shows execution mode, expected manual amount, cash effect, and core budget consumption.
- Guardrail: allocator still does not write index-fund rows into `paper_orders`; core fund execution remains a manual plan until a dedicated executor is designed.
- Verification evidence: `pytest tests/test_allocator.py -q` passed with 8 tests; full `pytest -q` passed with 179 tests; `ruff check .` passed.

### 2026-05-16 P1-07 Exposure Monitor Quality Thresholds

- Landed: `src.portfolio.exposure_monitor` now returns a deterministic `warnings` table for top1 position weight, max industry weight, Top5 concentration, unknown-industry weight, PE coverage, and PB coverage.
- Landed: exposure thresholds are configurable under `portfolio.exposure` with defaults for max position, max industry, max Top5, unknown industry, and minimum PE/PB coverage.
- Landed: Portfolio Dashboard shows active exposure-quality warnings above the industry/size/position tables; all-clear state is explicit.
- Real local check: current 13 stock holdings are OK under defaults: top1 8.6%, Top5 41.8%, max industry 23.1%, unknown industry 0.0%, PE coverage 91.8%, PB coverage 100.0%.
- Verification evidence: `pytest tests/test_exposure_monitor.py -q` passed with 5 tests; targeted `ruff check` passed for exposure monitor, portfolio dashboard, config, and exposure tests.

## P1 Candidates

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P1-01 | Core-satellite allocator for index funds and stock strategies | Done | Codex | Monitor daily close output for one week | One account-level budget governs stock strategy BUY orders; index-fund core execution remains advisory |
| P1-02 | Portfolio exposure monitor | Done | Codex | Monitor exposure values during the next paper-trade cycle | Dashboard shows industry, size, valuation, and benchmark-relative concentration |
| P1-03 | Enforce daily turnover cap in executable rebalance plan | Done | Codex | Monitor skipped_turnover after the next close run | BUY orders above the daily cap are deferred by confidence/rank priority; SELL remains executable |
| P1-04 | Persist signal outcomes | Done | Codex | Review outcome coverage after next filled paper orders | T+1/T+5/T+20 outcomes are stored by signal and model version |

## P2 Candidates

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P2-01 | Qlib PortAna report artifacts | Proposed | Codex | Verify local Qlib report APIs in active environment | Each successful Qlib run can produce a linked position/attribution report |
| P2-02 | Environment-specific config loading | Proposed | Codex | Design `MM_ENV` config merge order | `config/settings.dev.yaml` and `config/settings.prod.yaml` override safely |
| P2-03 | Small-account risk profiles | Proposed | Codex | Design profile interaction with allocator | 50k/100k/300k modes show realistic lot and concentration constraints |

## P3 Hygiene

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P3-01 | Add direct ATR tests | Proposed | Codex | Export or test through trend helper boundary | ATR handles gaps and warmup consistently |
| P3-02 | Add local pre-commit for Ruff and pytest smoke | Done | Codex | Local hook enabled via `core.hooksPath=.githooks` | Local hook runs `ruff check .` and a focused pytest smoke before commits |
| P3-03 | Retail user manual | Proposed | Codex | Draft after P0/P1 behavior stabilizes | Manual explains weekly actions, ignore rules, and failure modes in under 20 pages |

## Weekly Review Template

Copy this block below the previous weekly entry.

```markdown
### YYYY-MM-DD Weekly Review

- Completed:
- Blocked:
- Newly discovered risk:
- Metrics to watch:
- Next P0/P1 action:
- Verification evidence:
```

### 2026-05-15 Weekly Review

- Completed: system-level review double-check; baseline tests passed; P0 trustworthiness pass implemented for snapshots/instruments, tradability guards, comparator scope, daily workflow, and rolling IC view.
- Blocked: historical archive completeness now depends on acquiring reliable official adjustment files or a curated CSV export.
- Newly discovered risk: `scripts/daily_update.py` is a partial workflow and can drift from `scripts/daily_close.sh`.
- Metrics to watch: Qlib excess return, max drawdown, annual turnover, rolling RankIC, skipped tradability count, skipped lot count.
- Next P0/P1 action: use the archive importer when source files are available; start P1-01 core-satellite allocator design.
- Verification evidence: focused P0 regression suite passed with 15 tests; latest `pytest -q` passed with 133 tests; `ruff check .` passed after Ruff install and baseline enablement.

### 2026-05-15 P1 Allocator Phase 1

- Landed: `src.portfolio.allocator` computes and persists a core-satellite plan with current sleeve values, drift, and deployable budgets.
- Landed: `allocation_plans` and `allocation_plan_items` schema tables.
- Landed: CLI `python3 -m src.portfolio.allocator plan` and daily close script/Dashboard workflow step before paper trading.
- Landed: `paper_engine` reads latest active allocation plan and caps stock BUY cash requirement by `satellite_budget`; SELL orders remain unaffected.
- Landed: allocator writes advisory core fund execution items from latest `index_fund_signals`, allocating `core_budget` to BUY/ADD signals while leaving PAUSE/HOLD/REDUCE visible.
- Landed: Portfolio Dashboard shows the latest unified wallet split, sleeve budgets, sleeve actions, and core fund execution plan.
- Current live plan: `ALLOC-DEFAULT-20260515` recommends core budget around 95k and satellite budget around 28k from current cash.
- Remaining: decide whether core fund execution should remain advisory or get a separate snapshot/order executor in a later P1/P2 iteration.

### 2026-05-15 P1 Exposure Monitor

- Landed: `src.portfolio.exposure_monitor` computes symbol, industry, size bucket, valuation, concentration, and benchmark-relative exposure snapshots.
- Landed: Portfolio Dashboard `持仓暴露` section with summary metrics, industry tilt, market-cap buckets, and position drill-down.
- Benchmark: defaults to `000300`, using market-cap weights when available and equal weight fallback.
- Verification evidence: `pytest tests/test_exposure_monitor.py tests/test_dashboard_runtime_scripts.py -q` passed with 6 tests.

### 2026-05-15 P1 Buy Turnover Cap

- Landed: `paper_engine` enforces `portfolio.max_daily_turnover_pct` as a one-way daily BUY turnover cap.
- Landed: BUY execution priority now keeps higher-confidence/higher-score signals first after SELL/SHORT orders.
- Landed: SELL/SHORT orders remain exempt from the BUY turnover cap so exits are not blocked.
- Landed: result stats include `skipped_turnover`.
- Verification evidence: focused turnover tests passed; latest `scripts/quality_check.sh` passed with 157 tests.

### 2026-05-15 P1 Signal Outcomes

- Landed: `signal_outcomes` table keyed by `(signal_id, horizon_days)`.
- Landed: `src.signals.outcome_tracker` computes BUY and SELL/SHORT forward returns for T+1/T+5/T+20 horizons.
- Landed: daily close script and Dashboard job workflow update signal outcomes after paper trading, NAV, and performance review.
- Current local run: `python3 -m src.signals.outcome_tracker update` returned `updated=0`, meaning no eligible filled signals currently require outcome rows.
- Verification evidence: focused signal outcome and Dashboard workflow tests passed; latest `scripts/quality_check.sh` passed with 157 tests.
