# Daily Production Chain Review

P0-09 tracks the daily production chain from data pull to paper-trade execution.
The goal is not just "job succeeded"; each review should surface whether the whole
decision loop is trustworthy, explain any degradation, and turn repeated problems
into backlog items.

## Monitoring Window

- Primary window: every trading day after the 20:00 close workflow has either
  finished or failed.
- Baseline run: 2026-05-16 weekend run is recorded below, but it does not count toward the 5 trading-day target.
- Data-source rule: keep the six-month validation stance. Do not introduce paid data sources for monitoring unless explicitly approved.
- Automation: `make-money-p0-09-daily-close-monitor` performs the weekday review
  after the close workflow window.

## What To Check

- Scheduler status:
  - 09:40 open paper-trade watchdog state, start/end time, exit code, and missed/duplicate-run evidence.
  - 20:00 close workflow watchdog state, start/end time, exit code, and missed/duplicate-run evidence.
- Data pull:
  - A-share/HK/index attempted/updated/failed counts.
  - Data-source health rows and DNS/rate-limit/provider failures.
  - Latest trade date freshness used by Dashboard V2.
- Signal and model:
  - Signal generation counts by model and side.
  - Qlib production prediction freshness and publish-gate/monitor alerts.
  - Any duplicate signals or stale pending signals.
- Allocation and execution:
  - Latest `core_budget` and `satellite_budget`.
  - Paper engine summary: `executed`, `handled_without_order`, `pending`,
    `skipped_no_trading_day`, `skipped_untradeable`, `skipped_threshold`,
    `skipped_profile`, `skipped_lot`, `skipped_budget`, `skipped_turnover`,
    `skipped_cash`.
  - Filled orders grouped by strategy/symbol/side, including duplicate-order checks.
  - SELL cash release and BUY budget consumption if both sides appear.
- Portfolio and feedback:
  - Current holdings count and value consistency between Dashboard, `portfolio_nav`,
    and paper engine.
  - Exposure/risk warnings that require a concrete holding-level action.
  - Signal outcome updater counts: `updated`, `ready`, `pending`, and any model
    drift alerts.
- Any failed or degraded step should include command, exit code, and a short log excerpt.

## Review Output Contract

Every daily review should update this file with:

- One run row in `Runs`.
- One or more rows in `Issue Register` when a new or repeated problem is found.
- Updated acceptance progress.
- A concrete next action if the issue is actionable.

## Runs

| Date | Counted | Open Task | Close Task | Data Pull | Signal/Model | Paper Execution | Allocation/Portfolio | Issues / Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-05-16 | No | Weekend baseline | `JOB-DAILY_CLOSE_WORKFLOW-20260516085129-2666B2` SUCCEEDED | weekend/no next trading day | no production decision | executed 0/261; no_trading_day 261; untradeable 0; budget 0; turnover 0 | core 96,421; satellite 26,648 | Baseline only. The paper engine could not find a trading day after 2026-05-15, expected for Saturday before 2026-05-18 data. |
| 2026-05-18 | Yes | Not yet under review scope | `JOB-DAILY_CLOSE_WORKFLOW-20260518185840-1E97A8` FAILED | `update` failed at step 1 | not reached | not reached | latest persisted plan: core 96,421; satellite 26,648 (plan_date 2026-05-15) | Job-manager CLI missing `--run-id` first failed; fallback sync run failed with Sina DNS resolution failure for `finance.sina.com.cn`. |
| 2026-05-19 | Yes | 09:41 open task SUCCEEDED but degraded | 20:02 scheduler close task SUCCEEDED; earlier manual `JOB-DAILY_CLOSE_WORKFLOW-20260519174625-AUTO19` FAILED | open target update succeeded for 121 symbols; 17:46 manual close `update` failed with DNS/provider failures; 20:02 scheduler update completed with degraded free-source coverage: CN 571/587, HK 84/85, index 4 updated | 20:00 close generated 52 active 2026-05-19 signals (26 BUY, 26 SELL); production monitoring ran after outcomes | open paper trade filled 2 duplicate BUY orders for `600808`; 14 BUY symbols were incorrectly filtered by stale holding-count gate; 19 BUY symbols filtered by execution threshold; 20:00 paper execution found no next trading day after 2026-05-19, so executed 0/52 | latest plan: core 97,112; satellite 165,806 (plan_date 2026-05-19); current-holding value mismatch found and fixed locally by unified current-holdings helper; signal outcomes repaired to 114 rows (46 READY, 68 PENDING) | Exposed execution bugs: duplicate same-day same-strategy orders, stale `paper_positions` holdings after a strategy went flat, and terminal signal statuses with `executed=FALSE`. Also confirmed Dashboard/job-manager view can disagree with scheduler state because scheduler shell runs are not represented as `job_manager` runs. |

## Issue Register

| First Seen | Severity | Area | Symptom | Root Cause | Status | Next Action |
|---|---|---|---|---|---|---|
| 2026-05-18 | P0 | Scheduler/job manager | Dashboard/manual job start first failed with CLI requiring `--run-id` | Job-manager CLI contract changed but caller still used old command | Fixed locally | Job-manager CLI now accepts omitted `--run-id` and creates the run record itself; verify future scheduled/manual job starts no longer fail before fallback. |
| 2026-05-18 | P0 | Data source | Manual close workflow can stop at data update due DNS/provider failures | Free sources `finance.sina.com.cn`, `push2his.eastmoney.com`, `33.push2his.eastmoney.com` are intermittently unreachable locally; later 20:00 scheduler retry may succeed with degraded coverage | Open | Add resilient free-source fallback/degraded policy and clearer Dashboard distinction between failed manual job-manager runs and successful scheduler shell runs. |
| 2026-05-19 | P0 | Paper execution | `600808` was bought twice in the same open batch | Same strategy + symbol + side + execution date duplicate signals were executed independently | Fixed locally | Keep regression test for duplicate BUY; verify next open task produces at most one order per strategy/symbol/side/date. |
| 2026-05-19 | P0 | Current holdings | BUY candidates were blocked by `当前 13 只 / 上限 10 只` even though alpha158 had gone flat | `paper_positions` stores positive rows only; using its latest date revived stale holdings after `portfolio_nav` showed zero position value | Fixed locally | Use shared current-holdings helper everywhere; verify next open task no longer counts cleared alpha158 positions. |
| 2026-05-19 | P1 | Dashboard/portfolio | Dashboard position value and current holding details did not match | Dashboard, exposure, coverage, and paper engine used different current-holding SQL | Fixed locally | Keep all current-holding reads on the shared helper and check Dashboard total equals holding detail sum daily. |
| 2026-05-19 | P1 | Signal outcomes | Filled/NO_ACTION signals had terminal `status` but `executed=FALSE`, so outcome tracking and model monitoring could miss them | Historical/migrated signal status fields were not reconciled back to the legacy `executed` flag | Fixed locally | `init_db` reconciles terminal statuses to `executed=TRUE`; ran `outcome_tracker update` and produced 114 outcome rows (46 READY, 68 PENDING). |
| 2026-05-19 | P1 | Observability | Dashboard/job-manager latest run showed the 17:46 manual close as FAILED while scheduler state showed the 20:02 close as SUCCEEDED | The scheduler runs `scripts/daily_update.py` directly and writes `output/scheduler_state.json`/`output/cron.log`; job-manager history only sees `data/jobs/runs` | Partially fixed locally | `/today` now prefers the parsed scheduler latest run for evidence status, matching `/health`; future work can persist scheduler runs into the same job run store. |

## Acceptance Progress

- Trading-day successful/diagnosed runs: 2/5 for the initial stabilization window.
- Current status: 2026-05-19 scheduled open and close tasks both exited 0, but the run is counted as degraded because open paper execution exposed duplicate-order/current-holding bugs and observability still disagrees between scheduler state and job-manager history. Continue daily chain review until open and close are both stable for 5 consecutive trading days, then keep the review cadence as a lightweight ongoing operations check.
