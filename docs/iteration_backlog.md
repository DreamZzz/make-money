# make-money Iteration Backlog

Review date: 2026-05-16
Last reviewed: 2026-05-18
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

### Dashboard Single-Track Policy

- Daily operation entrypoint: Dashboard V2 `/today`.
- Legacy Streamlit role: research fallback only; 旧 Streamlit 不作为每日操作入口。
- Production close/open tasks must not depend on Streamlit pages being live.
- Migration gate: Dashboard V2 must show today action, rebalance, portfolio, health, research summary, user guide, scheduler history, model monitor alerts, and signal outcome summaries before Streamlit restart logic is removed from production scripts.

## Current Baseline

- Latest local commit before P2-06 validation: `864df9b` (`feat: backfill cn financials from akshare`).
- Review v2 baseline correction: the external v2 review used `origin/main` (`23b8178`) and did not include local commits `93e9995` and `9e000f4`.
- Test baseline: `pytest -q` passed with 195 tests on 2026-05-16.
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
| P0-09 | Daily production chain review | In Progress | Codex | Continue weekday end-to-end reviews in `docs/daily_close_monitoring.md` after the 20:00 close workflow window | For each trading day, record scheduler status, data pull, signal/model freshness, allocation budgets, paper execution, current-holding consistency, signal outcomes, and concrete issue-register rows; initial stabilization exits only after 5 consecutive trading days have both open and close chains successful or fully diagnosed |
| P0-10 | Replace calendar-triggered task scheduling with an interval watchdog | Done | Codex | Monitor the next 20:00 close run and next 09:40 open run from Dashboard V2 Health | Open and close workflows no longer depend on `StartCalendarInterval`; a persisted run-state lock prevents duplicate runs; missed/due/running/succeeded/failed states are visible in Dashboard V2; tests cover idempotency, missed-run recovery, and failure surfacing |

### P1 - Execution And Risk Loop

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P1-05 | Automate current-holding fundamentals coverage | Done | Codex | Monitor the next daily close run for the new non-blocking fundamentals step | Current holdings have 0 missing values for the four fields after daily close; failures are logged per symbol without blocking the close |
| P1-06 | Core fund execution planning v2 | Done | Codex | Monitor the next allocation plan output in Dashboard before any manual core fund operation | Dashboard shows actionable core fund BUY/ADD/REDUCE plan with budget consumption and no mutation of paper orders |
| P1-07 | Exposure monitor quality thresholds | Done | Codex | Monitor exposure warnings during the next paper-trade cycle | Dashboard flags exposure risks with deterministic thresholds and tests for each warning state |
| P1-08 | Daily close failure diagnostics | Done | Codex | Use the failure diagnostic card for the next failed close run before opening raw logs | Dashboard can show latest failed step, command, return code, and last log excerpt without reading terminal history |
| P1-09 | Free-source data probe layer | Done | Codex | Use probe output to choose the first production-safe fallback/backfill target | Tencent daily, Eastmoney reports, THS theme, and optional mootdx probes normalize source output, record `data_source_health`, and do not change trading decisions |
| P1-10 | Target-universe field coverage backfill via free sources | In Progress | Codex | Find a reliable free industry-mapping source; do not use Eastmoney per-symbol metadata as the main route while it is unstable locally | `industry`, `market_cap`, `pe_ttm`, and `pb` coverage reaches >=80% for the target universe; failures are cached and surfaced without blocking daily close |
| P1-11 | Global signal arbiter for rule/Qlib consensus | Done | Codex | Monitor the next close/open cycle for accepted/rejected decision counts and rejected BUY reasons | Rule strategies can only reach paper execution after global confidence gates and fresh Qlib cross-checks; same-symbol BUY conflicts are resolved once in `signal_decisions`; SELL risk-release signals remain executable |

### P2 - Feedback And Alpha Expansion

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P2-04 | Signal outcomes Dashboard | Done | Codex | Re-check after the next filled paper orders create READY outcome rows | Dashboard exposes T+1/T+5/T+20 realized signal performance with empty-state handling |
| P2-05 | Extend signal outcomes to benchmark-relative returns | Done | Codex | Re-check alpha columns after the next READY outcome rows are produced | `signal_outcomes` can report raw return and benchmark-relative alpha per signal/horizon |
| P2-06 | Value-quality fundamental factor prototype | Done | Codex | Keep research-only; improve historical valuation/size coverage before another promotion attempt | 2022-2025 standalone backtest and correlation evidence are recorded; current prototype is not approved for production |
| P2-07 | Qlib PortAna artifact | Done | Codex | Re-check after the next successful Qlib run that Dashboard exposes the artifact status/path | Successful Qlib runs can link to a saved attribution/position report artifact |
| P2-08 | Environment-specific config loading | Done | Codex | Use `MM_ENV=dev` or `MM_ENV=prod` only when an explicit environment overlay is needed | `config/settings.dev.yaml` and `config/settings.prod.yaml` override safely without breaking default local runs |
| P2-09 | Production model monitoring loop | Done | Codex | Run `predict-latest`, then monitor the next close-run transition from prediction alerts to realized outcome metrics | Published Qlib production models write local monitor alerts for prediction freshness, signal generation, paper execution, and benchmark-relative outcome drift |
| P2-10 | Fundamental alpha sprint and shadow-mode gate | Ready | Codex | Produce `docs/fundamental_alpha_diagnosis.md` for the current `value_quality` failure, then design `value_quality_v2` with sector-neutral ranking, coverage gates, and shadow-mode monitoring | Fundamental signals stay research-only/shadow-only until validation passes: 2022-2025 excess return > 0, IR >= 0.30, MaxDD no worse than MIXED_EQUAL by more than 3pp, annual turnover <= 12, correlation vs Alpha158 < 0.50, monthly selectable count >= 10, and factor coverage >= 80%; only after this gate may it be wired into `signals.generate_all()` or consume `satellite_budget` |
| P2-11 | Report/theme shadow features from free sources | Proposed | Codex | After P1-10 improves field coverage, design `research_reports` and `stock_theme_tags` staging tables | Eastmoney report and THS theme data enter Dashboard attribution and shadow monitoring first; they must not create BUY signals until a separate validation gate passes |

### P3 - Retail Usability And Hygiene

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P3-04 | Small-account risk profiles | Done | Codex | Monitor next paper-trade and weekly-report run for skipped_profile and one-lot funding hints | 50k/100k/300k modes produce realistic position counts and explicit one-lot funding hints |
| P3-05 | Weekly operation summary card | Done | Codex | Monitor whether the weekly summary matches the executable plan after the next signal batch | Retail user can see "operate N times / need cash Y / expected time T" before reading individual signals |
| P3-06 | Retail user manual | Done | Codex | Keep the Markdown manual and Dashboard V2 `/guide` page in sync when product flows change | `docs/dashboard_v2_user_guide.md` and Dashboard V2 `/guide` explain expectation management, onboarding, weekly review thresholds, signal outcome interpretation, emergency handling, privacy, and manual execution discipline |
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

### 2026-05-16 P1-08 Daily Close Failure Diagnostics

- Landed: Dashboard job runner now persists per-step `cmd_text`, `duration_seconds`, and `log_excerpt` in each durable run JSON file while still streaming full logs to disk.
- Landed: `latest_failure_diagnostic()` extracts the failed step, command, return code, duration, and output excerpt directly from the run record.
- Landed: Strategy Compare / task workbench shows a failure diagnostic card with failed step, exit code, command, duration, and recent output before the raw log console.
- Compatibility: old run JSON records without new diagnostic fields remain renderable in Dashboard.
- Verification evidence: `pytest tests/test_dashboard_job_manager.py tests/test_dashboard_runtime_scripts.py -q` passed with 16 tests; targeted `ruff check` passed for job manager, strategy compare view, and related tests.

### 2026-05-16 P2-04 Signal Outcomes Dashboard

- Landed: `src.dashboard.signal_outcome_service` aggregates `signal_outcomes` into strategy/horizon summary, month/model/horizon feedback, and recent detail rows.
- Landed: Portfolio Dashboard now has `信号收益跟踪` with READY/PENDING sample counts, weighted average return, weighted hit rate, strategy summary, monthly table/chart, and recent signal detail.
- Empty-state behavior: when `signal_outcomes` has no rows, Dashboard shows a clear message instead of an empty table.
- Real local check: current production DuckDB has 0 `signal_outcomes` rows, so the new panel currently renders the empty-state path until the next filled paper orders mature.
- Verification evidence: `pytest tests/test_signal_outcome_dashboard.py tests/test_dashboard_runtime_scripts.py -q` passed with 5 tests; targeted `ruff check` passed for the new service, Portfolio Dashboard, and tests.

### 2026-05-16 P2-05 Benchmark-Relative Signal Outcomes

- Landed: `signal_outcomes` now stores `benchmark_code`, `benchmark_return_pct`, and `alpha_vs_benchmark`.
- Landed: `src.signals.outcome_tracker` maps A-share signals to `000300` and HK signals to `HSTECH`, computes benchmark same-horizon return, and writes raw return minus benchmark return as alpha when both sides are READY.
- Landed: Portfolio Dashboard signal outcome summary, monthly feedback, and detail tables now show average alpha / benchmark return / alpha vs benchmark.
- Empty/partial-data behavior: if a signal return is READY but benchmark data is missing, raw return remains READY while benchmark return and alpha stay empty.
- Real local check: `python -m src.signals.outcome_tracker update` returned `updated=0`, and production DuckDB has the three new benchmark columns with no current outcome rows.
- Verification evidence: focused signal outcome and Dashboard tests passed; full verification is recorded with this commit.

### 2026-05-17 P2-09 Production Model Monitoring Loop

- Landed: `model_monitor_alerts` persists active/resolved production model alerts keyed by model/version/date/metric.
- Landed: `src.monitoring.model_monitor update` evaluates the active Qlib production model across production inference freshness, signal generation, paper execution status, and realized `signal_outcomes` alpha/hit-rate drift.
- Landed: daily close and Dashboard job workflows now run model monitoring after `signal_outcomes`, so alerts are generated after prediction, paper trading, NAV, and outcome updates.
- Landed: Dashboard V2 `today` and `health` include `health.model_monitor` with active alert counts and current alert details.
- Current local check: the newly published `alpha158-20260517160658-74b46e` has active WARN alerts for missing production inference and missing production signals until the next `predict-latest` / close loop writes them.
- Verification evidence: `pytest tests/test_model_monitor.py tests/test_dashboard_v2_service.py tests/test_dashboard_job_manager.py tests/test_dashboard_runtime_scripts.py -q` passed; targeted Ruff and `bash -n scripts/daily_close.sh` passed.

### 2026-05-18 P0-10 Scheduler Reliability Diagnosis

- Diagnosed missed 2026-05-18 09:40 open paper-trade run: `com.quant.open-paper-trade` was loaded, not disabled, and had no script/Python/DB-lock evidence around the scheduled time.
- Root-cause probe: `StartCalendarInterval` test LaunchAgents failed to fire automatically in the current GUI session, while manual `launchctl kickstart` and `StartInterval` probes succeeded.
- Judgment: the direct failure is macOS user-domain calendar-trigger delivery, not the make-money workflow script itself.
- Follow-up: replace open/close `StartCalendarInterval` LaunchAgents with an interval watchdog that checks due windows, records state, avoids duplicate same-day execution, and exposes missed-run diagnostics to Dashboard V2.

### 2026-05-18 P0-10 Scheduler Watchdog Implementation

- Landed: `scripts/scheduler_watchdog.py` runs under a `StartInterval` LaunchAgent, evaluates local due windows in Python, persists `output/scheduler_state.json`, and prevents duplicate same-day runs with a RUNNING/SUCCEEDED/FAILED/MISSED state lock.
- Landed: `scripts/install_scheduler_watchdog.sh` installs `com.quant.scheduler-watchdog`, disables the old `com.quant.daily-update` and `com.quant.open-paper-trade` calendar LaunchAgents, and keeps stdout/stderr in `output/scheduler_watchdog*.log`.
- Landed: Dashboard V2 Health reads the watchdog state and shows the open/close job status, next due time, last execution date, and result; the old CalendarInterval labels are no longer the health source of truth.
- Install evidence: `launchctl print gui/501/com.quant.scheduler-watchdog` shows `run interval = 300 seconds`, `runs = 1`, `last exit code = 0`; `launchctl list` only shows `com.quant.scheduler-watchdog` among the three scheduler labels.
- Current state after install: open paper trade is `MISSED` for 2026-05-18 because the window had already passed; close workflow is `WAITING` for 2026-05-18 20:00; next open due is 2026-05-19 09:40.
- Verification evidence: `pytest tests/test_scheduler_watchdog.py tests/test_dashboard_v2_service.py tests/test_dashboard_runtime_scripts.py -q` passed; `ruff check scripts/scheduler_watchdog.py src/dashboard_v2/service.py tests/test_scheduler_watchdog.py tests/test_dashboard_v2_service.py tests/test_dashboard_runtime_scripts.py` passed; Dashboard V2 `/api/v2/health` reports `WAITING`/`MISSED` watchdog states instead of 500 or stale calendar status.

### 2026-05-18 P3-06 Dashboard V2 User Guide Iteration

- Landed: `docs/dashboard_v2_user_guide.md` now includes investment philosophy and expectation management, first-use onboarding, quantitative weekly review thresholds, signal outcome interpretation, emergency playbooks, privacy notes, mobile/remote-access caveat, and retail-readable glossary thresholds.
- Landed: Dashboard V2 now has a `/guide` route and primary navigation entry that renders the product manual as semantic HTML inside the app.
- Landed: README points users to both the Markdown guide and in-app `/guide` manual.
- Verification evidence: `npm --prefix frontend/dashboard-v2 test -- --run` passed with 10 tests; `npm --prefix frontend/dashboard-v2 run build` passed; Playwright smoke checks passed on desktop 1440x1000 and mobile 390x844 with no console errors and no mobile page-level horizontal overflow.

### 2026-05-18 P1-09 Free-Source Data Probe Layer

- Landed: `src.data_pipeline.fetchers.free_sources` normalizes Tencent daily bars, Eastmoney stock research reports, THS concept/theme summaries, and optional mootdx daily bars into stable DataFrame shapes with `source_status` metadata.
- Landed: `src.data_pipeline.free_source_probe` and `scripts/probe_free_sources.py` can probe selected symbols/sources and convert results into existing `data_source_health` rows without mutating trading data or signal decisions.
- Dependency boundary: `mootdx>=0.11.7` is recorded as optional `data-extra`; environments without mootdx report `source_error: mootdx is not installed` instead of failing the whole probe.
- Real local probe: `000001` and `600519` passed Tencent daily and Eastmoney report probes, THS theme probe passed, and mootdx correctly reported missing dependency; 4 health rows were recorded under run `FREE-SOURCE-PROBE-bae462282699`.
- Next step: use P1-10 to backfill field coverage in three scopes: current holdings first, latest signal candidates second, then the 708-symbol target universe.
- Verification evidence: `pytest tests/test_free_source_fetchers.py tests/test_free_source_probe.py -q` passed; `ruff check src/data_pipeline/fetchers/free_sources.py src/data_pipeline/free_source_probe.py scripts/probe_free_sources.py tests/test_free_source_fetchers.py tests/test_free_source_probe.py` passed.

### 2026-05-18 P1-10 Field Coverage Backfill

- Landed: `src.data_pipeline.field_coverage_backfill` and `scripts/backfill_field_coverage_free_sources.py` backfill `stock_info.market_cap` plus latest `daily_price.pe_ttm/pb` by staged scope without touching signals, paper orders, or allocation decisions.
- Landed: Tencent quote snapshot fallback fills PE/PB/market-cap when AkShare/Eastmoney all-market spot fails; `--skip-industry-fetch` prevents unstable per-symbol Eastmoney metadata calls from blocking valuation coverage.
- Real local result: current holdings improved from PE/PB `0/13` to `13/13`; target universe improved to `market_cap=708/708`, `pb=708/708`, `pe_ttm=644/708`, while `industry` remains `13/708`.
- Health rows were recorded for `field_coverage_current_holdings`, `field_coverage_target_universe`, and `field_coverage_signal_candidates`; failures/partial results are visible through `data_source_health`.
- Remaining gap: industry coverage still needs a reliable free batch mapping source. THS industry list/summary is reachable, but local AkShare does not expose a THS industry constituents endpoint; Eastmoney industry constituents and per-symbol metadata are unstable locally.
- Verification evidence: `pytest tests/test_field_coverage_backfill.py tests/test_free_source_fetchers.py tests/test_free_source_probe.py -q` passed; targeted Ruff passed for the new modules/scripts/tests.

### 2026-05-20 P1-11 Global Signal Arbiter

- Landed: `signal_decisions` persists one global arbitration decision per active stock signal before paper execution.
- Landed: `src.signals.arbiter` applies portfolio-level BUY confidence/rank-score gates, requires fresh Alpha158 production cross-check for rule-strategy BUYs, blocks same-symbol BUY/SELL conflicts, and keeps only the highest-priority same-symbol BUY.
- Landed: SELL/SHORT signals remain risk-release actions and are not globally deduplicated, so separate strategy books can still exit their own positions.
- Landed: `paper_engine` now loads only `ACCEPTED` decisions, and `daily_close.sh` plus Dashboard job manager run the arbiter after Qlib prediction and before allocation planning.
- Landed: Dashboard V2 current holdings expose buy source and Qlib alignment status/reason, so low-rank legacy holdings are visible as cross-check conflicts rather than silently accepted positions.
- Verification evidence: `pytest -q` passed with 288 tests; `ruff check .` passed; V2 component test and production build passed.

### 2026-05-16 P2-06 Value-Quality Prototype

- Landed: research-only `src.research.strategies.value_quality` with local value/quality/liquidity scoring from `daily_price.pe_ttm`, `daily_price.pb`, `financials.roe`, `financials.net_margin`, `financials.debt_ratio`, and `stock_info.market_cap`.
- Landed: standard BUY signal conversion for the top scored names, tagged `value_quality/fundamental/research_only`; production `generate_all()` is not wired to this strategy yet.
- Landed: simple equal-weight Top-N return simulation and return-correlation helper so the future 2022-2025 validation can compare against Alpha158/technical sleeves.
- Real local check before backfill: current DuckDB had `financials=0`, so the CLI could score 708 CN rows but average factor coverage was only 0.9%; generated prototype names were valuation/liquidity-driven with quality factors neutral.
- Landed next: free-source AkShare financial backfill support via `src.data_pipeline.financials_backfill` and `scripts/backfill_cn_financials_akshare.py`; the script defaults to the local priced research universe and can opt into all `stock_info` CN symbols with `--all-stock-info`.
- Real local backfill: `scripts/backfill_cn_financials_akshare.py --sleep 0.2` selected 708 priced CN symbols, skipped 25 existing symbols, fetched 683 symbols, inserted 49,353 rows, and reported `empty=0 / failed=0`.
- Real local coverage after backfill: `financials=60,544` rows, `priced_with_financials=708/708`, report coverage spans `1989-12-31` to `2026-03-31`; value-quality CLI average coverage improved to 50.9%.
- Status note: P2-06 is no longer blocked on financial history; subsequent standalone validation kept it research-only because performance and diversification evidence were weak.
- Verification evidence: focused value-quality tests passed; full verification is recorded with this commit.

### 2026-05-16 P2-06 Standalone Validation

- Landed: `src.research.strategies.value_quality_validation` and `scripts/validate_value_quality.py` for repeatable standalone validation.
- Validation design: monthly Top-20, 20 trading-day hold, 60 calendar-day financial reporting lag, T+1 open execution, CN open-tradability guards, commission + stamp duty costs, benchmark `MIXED_EQUAL`.
- Real local result: `BT-20260516152109-4094BE`, 2022-01-28 to 2025-12-31, annual return `-4.38%`, benchmark annual return `4.37%`, excess return `-8.75 pp`, max drawdown `-40.67%`, IR `-0.52`.
- Correlation evidence: Alpha158 period-return correlation `0.69` over 23 common periods; benchmark correlation `0.90`.
- Judgment: keep `value_quality` research-only and do not wire into production signal generation; current version does not provide a reliable standalone or diversifying alpha.
- Details: `docs/value_quality_validation_2022_2025.md`.

### 2026-05-18 P2-10 Fundamental Alpha Sprint

- Decision: create a dedicated fundamental-alpha optimization track instead of directly wiring the current `value_quality` prototype into production.
- Scope: diagnose the current failure by monthly factor coverage, value/quality/liquidity subfactor IC, industry and size-bucket contribution, turnover/cost attribution, and correlation versus Alpha158.
- Next design direction: `value_quality_v2` should use sector-neutral valuation and quality ranks, explicit coverage gates, financial-sector handling, low-turnover monthly construction, and optional use as an Alpha158 priority/deferral overlay.
- Production boundary: until the validation gate is met, fundamental signals may be generated only as research/shadow candidates for Dashboard monitoring and `signal_outcomes`; they must not enter `signals.generate_all()`, paper trading, or the unified `satellite_budget`.
- Gate to main flow: 2022-2025 excess return > 0, IR >= 0.30, MaxDD no worse than `MIXED_EQUAL` by more than 3pp, annual turnover <= 12, correlation vs Alpha158 < 0.50, monthly selectable count >= 10, and factor coverage >= 80%.

### 2026-05-16 P2-07 Qlib PortAna Artifact

- Landed: optional `src.backtest.qlib_portana` artifact generator converts Qlib portfolio/benchmark daily metrics into the `report_graph` DataFrame contract and writes a saved `portana.html` when the local Qlib report API is available.
- Landed: future `evaluate_predictions()` runs attach `metrics_json.portana_artifact` with status, path, figure count, row count, and source; missing Qlib report APIs are recorded as `skipped` instead of failing the experiment.
- Landed: `scripts/generate_qlib_portana.py` can refresh an existing experiment from `qlib_daily_metrics`, with `--experiment-id latest` and `--no-update` modes.
- Landed: Qlib analysis Dashboard report tables expose `portana_status` and `portana_artifact_path` so the artifact can be located without querying DuckDB manually.
- Real local check: Homebrew Python lacks Qlib report APIs and correctly records `status=skipped`; `/usr/bin/python3` has `qlib.contrib.report.analysis_position.report_graph` and generated `/Users/zhaoqiang/Documents/Project/make-money/output/qlib_portana/QLIB-WALK_FORWARD-20260514221005-AD82EC/portana.html` with 563 report rows.
- Verification evidence: focused PortAna and report-service tests passed; full verification is recorded with this commit.

### 2026-05-16 P2-08 Environment-Specific Config Loading

- Landed: `load_config()` now merges configs in this order: built-in defaults, `config/settings.yaml`, then optional `config/settings.<MM_ENV>.yaml`.
- Landed: explicit `load_config(env=..., config_dir=...)` arguments make the merge order testable without changing process-wide environment state.
- Safety behavior: unset `MM_ENV` preserves the existing local behavior; unsafe environment names such as `../prod` are rejected before any path is built.
- Landed: `config/settings.dev.yaml` and `config/settings.prod.yaml` provide minimal opt-in overlays for development and production runs.
- Verification evidence: focused config tests passed; full verification is recorded with this commit.

### 2026-05-16 P3-04/P3-05 Retail Account Usability

- Landed: `portfolio.risk_profile` supports `auto/small/medium/large`; auto maps roughly to 50k/300k/800k account bands, and the selected profile overlays stock count, single-name cap, overweight cap, and operation-time assumptions.
- Landed: small-account profile limits stock holdings to 5 names while raising single-name capacity, so 50k/100k accounts avoid too many unusable dust positions.
- Landed: paper trading enforces `max_stock_positions`; new-stock BUY signals beyond the profile limit are marked `NO_ACTION` with `skipped_profile` and an explicit profile reason.
- Landed: executable rebalance plans expose `min_lot_value` and `funding_gap`; unaffordable candidates now show the approximate one-lot funding gap instead of only saying "不足一手".
- Landed: Weekly Report shows a top operation-summary card with operation count, required cash, estimated manual minutes, candidate count, released cash, active risk profile, and one-lot funding gap notice.
- Verification evidence: focused risk-profile, optimizer, paper-engine, and weekly-summary tests passed; full verification is recorded with this commit.

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
| P2-01 | Qlib PortAna report artifacts | Done | Codex | Superseded by current priority item P2-07 | Each successful Qlib run can produce a linked position/attribution report |
| P2-02 | Environment-specific config loading | Done | Codex | Superseded by current priority item P2-08 | `config/settings.dev.yaml` and `config/settings.prod.yaml` override safely |
| P2-03 | Small-account risk profiles | Done | Codex | Superseded by P3-04 | 50k/100k/300k modes show realistic lot and concentration constraints |

## P3 Hygiene

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P3-01 | Add direct ATR tests | Proposed | Codex | Export or test through trend helper boundary | ATR handles gaps and warmup consistently |
| P3-02 | Add local pre-commit for Ruff and pytest smoke | Done | Codex | Local hook enabled via `core.hooksPath=.githooks` | Local hook runs `ruff check .` and a focused pytest smoke before commits |
| P3-03 | Retail user manual | Done | Codex | Superseded by current priority item P3-06 | Manual explains weekly actions, ignore rules, expectation management, failure modes, and in-app reading path |

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

### 2026-05-24 Pre-Go-Live Iteration (P1-D / P0-B / P0-C / P1-E / P0-A)

Triggered by a fresh end-to-end readiness review against the goal of guiding retail trading. Root causes were verified against the live DuckDB before any change.

- P1-D close-chain reliability (Landed): `src.data_pipeline.main.evaluate_update_health` replaces the absolute `failure_count > max_update_failures(=0)` gate. Transient `*_source_error` (rate-limit / provider unreachable) no longer hard-fail the chain; only low coverage (`min_update_success_ratio`, default 0.5) or genuine `*_failed` exceptions over `max_update_failures` abort. This fixes the 2026-05-22 close FAILED caused by a rate-limit burst while data was still fresh. Qlib runtime confirmed healthy: `daily_close.sh` resolves `.venv-qlib/bin/python` first; `qlib_predictions` max date == `daily_price` max date (2026-05-22).
- P0-B satellite budget starvation (Landed): root cause was static 60/40 with 5% tolerance + core-cash-priority — satellite at 35.3% vs 40% sat within the tolerance band, so it received 0 budget while under-target core absorbed all cash. Per product decision (individual stock signals are the core value), `config/settings.yaml` allocation re-weighted to core 0.50 / satellite 0.45 / cash 0.05. Verified with `--no-persist`: satellite_budget moves from 0 to ~46,659 on the 2026-05-22 account state. Next close applies it automatically.
- P0-C arbiter BUY gate calibration (Landed): the global confidence floor (0.75, calibrated for rule strategies ~0.84) was rejecting 12/15 of alpha158's own production BUYs (confidence scale ~0.67). Added baseline-specific floors `min_baseline_buy_confidence` (0.55) / `min_baseline_buy_rank_score` (0.30) applied to baseline-self signals; rule-strategy floors unchanged. Decision: do NOT loosen rule-strategy floors — with a 26-36% realized hit rate, looser gates would only execute more losing trades. The "Qlib共识过期" rejection bucket is a symptom of P1-D freshness lag and self-resolves with fresh daily predictions (`max_prediction_stale_days=3` kept).
- P1-E execution regression coverage (Verified): the headline execution bugs already have green regression tests — `test_paper_engine_deduplicates_same_strategy_symbol_side_execution_day` (duplicate same-day BUY, 600808) and `test_current_position_helpers_ignore_stale_positions_after_strategy_is_flat` (stale holdings gate). 27 execution tests pass. Remaining: the close-vs-open fill timing (order_ts) residual is mitigated by trade_key dedup + the budget fix and is a watch-item for the 5-trading-day stabilization window, not a code emergency.
- P0-A go-live gate (Defined; expectation reframe): "high win rate" is the wrong target for a monthly-rebalance trend/ML system (low win rate, positive expectancy by design). Go-live gate before guiding real retail trading: (1) >= 100 READY `signal_outcomes` rows accumulated from the fixed chain; (2) T+20 `alpha_vs_benchmark` weighted average sustained positive over the sample; (3) open-execution NO_ACTION rate back under ~0.5 for 3+ consecutive trading days; (4) 5 consecutive clean open+close chains (P0-09). Current state (2026-05-24): 67 READY rows, T+1 hit 36% / T+5 hit 26%, NO_ACTION ~0.93 — gate NOT met. Realistic timeline is weeks, not next week.
- Verification evidence: `pytest -q` 379 passed, 1 pre-existing unrelated failure (`test_survivorship_impact` DuckDB timestamp representation, fails on stock HEAD too); `ruff check` clean on all changed files; new tests in `tests/test_daily_update.py` (4) and `tests/test_signal_arbiter.py` (1).

### 2026-05-24 多账户竞赛验证系统（虚拟账户并行对标）

- 动机：单账户串行纸交易验证周期太长。改为管理多个虚拟账户，各跑不同模型/策略配置，相同行情下并行对标，选最优指导实盘。
- 账户粒度 = 一套完整可部署配置（模型组合 + 套利门槛 + 配比 + 风险档）。`virtual_accounts` 注册表 + 账户级 `account_orders/positions/nav/decisions/performance` 表（复用已账户化的 cashbook）。5 个种子账户。
- 真隔离执行引擎 `src/accounts/engine.py`：每账户用自己配置独立套利（复用 `arbiter._build_decisions` 纯函数）、在隔离现金/持仓上成交（复用 `estimate_buy_execution`/`check_open_tradeable`），不碰现有 default 链路。
- 历史回放预热 `src/accounts/replay.py`：解决"周期太长"。alpha158 用 `walk_forward` 预测（构造上 point-in-time，覆盖 2024+；非 `production_inference`(仅5个月)、非 `selected`(全FALSE)）按月度 top-N 轮动；规则策略确定性重算；as-of 套利 + T+1 成交 + 按日 mark-to-market；信号池只算一次跨账户复用。
- 竞赛榜 + 晋级闸门 `src/accounts/leaderboard.py` / `promotion.py`：年化/超额/Sharpe/回撤/换手/命中率/IR；闸门含选择偏差守卫（N选1冠军需明显优于亚军）。
- Dashboard V2 `/tournament` 页（`build_tournament_snapshot` + `/api/v2/tournament` + 前端 TournamentPage）。
- 接入 `daily_close.sh` 步骤15（前向执行，非阻塞）+ CLI `python -m src.accounts.daily {seed,forward,replay,metrics}`。
- 真实首跑（2024-2026，基准 000300）：5 账户全部跑输 CSI300（超额 -7.4%~-24.2%），晋级闸门正确推荐冠军=None。alpha158_pure 相对最优（年化9.6%/Sharpe0.54/回撤-25%/命中46%/103平仓）。真实执行约束下当前配置都不够格上实盘——系统在诚实工作。
- 待办：后续可加更多账户配置（不同 top_n/调仓频率/风险档）继续对标；前向链累积真实战绩后与回放对照。
- 验证：新增 `test_accounts_registry/engine/replay/leaderboard` + dashboard tournament 测试；`ruff check` 干净；前端 `build` + `test` 通过。
