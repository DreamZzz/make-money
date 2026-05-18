# Daily Close Monitoring

P0-09 tracks one week of daily close workflow health. The goal is not just "job succeeded"; each run should also surface whether the execution loop is behaving as intended.

## Monitoring Window

- Primary window: 2026-05-18 to 2026-05-22, after China/Hong Kong market close.
- Baseline run: 2026-05-16 weekend run is recorded below, but it does not count toward the 5 trading-day target.
- Data-source rule: keep the six-month validation stance. Do not introduce paid data sources for monitoring unless explicitly approved.
- Automation: `make-money-p0-09-daily-close-monitor` is scheduled for the next five weekday close checks.

## What To Check

- `daily_close_workflow` final status and each step exit code.
- Paper engine summary: `executed`, `pending`, `skipped_no_trading_day`, `skipped_untradeable`, `skipped_budget`, `skipped_turnover`.
- Latest allocation plan: `core_budget` and `satellite_budget`.
- Signal outcomes updater: `updated`, `ready`, `pending`.
- Any failed step should include command, exit code, and a short log excerpt.

## Runs

| Date | Counted | Run ID | Status | Step Summary | Paper Engine | Allocation Budget | Signal Outcomes | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-05-16 | No | `JOB-DAILY_CLOSE_WORKFLOW-20260516085129-2666B2` | SUCCEEDED | 11/11 succeeded | executed 0/261; no_trading_day 261; untradeable 0; budget 0; turnover 0 | core 96,421; satellite 26,648 | updated 0; ready 0; pending 0 | Weekend baseline. The paper engine could not find a trading day after 2026-05-15, which is expected for a Saturday run with no 2026-05-18 data yet. |
| 2026-05-18 | Yes | `JOB-DAILY_CLOSE_WORKFLOW-20260518185840-1E97A8` | FAILED | 0/13 succeeded; `update` failed (exit 1) | not reached (workflow stopped at step 1) | latest persisted plan: core 96,421; satellite 26,648 (plan_date 2026-05-15) | not reached (step not executed) | Trigger command `/opt/homebrew/bin/python3.12 -m src.dashboard.job_manager run --job-key daily_close_workflow` failed first (CLI now requires `--run-id`, exit 2). Fallback sync run via `run_job(\"daily_close_workflow\")` started, then `update` failed: `Error: 增量更新存在 1 个失败项，超过阈值 0。` with Sina DNS resolution failure for `finance.sina.com.cn`. |

## Acceptance Progress

- Trading-day successful/diagnosed runs: 1/5.
- Current status: 2026-05-18 close run diagnosed with actionable upstream data-source/DNS failure; continue next close checks.
