# Production Model Monitoring Design

Date: 2026-05-17

## Goal

Track whether the currently published Qlib production model continues to behave as expected after it starts producing production signals, paper trades, and realized signal outcomes. The first version must be local, auditable, and visible in Dashboard V2 without depending on an external notification channel.

## Scope

This design covers the first monitoring loop:

- Evaluate the active `qlib_model_registry.status='production'` model.
- Check production prediction freshness, signal creation, paper execution, and `signal_outcomes`.
- Persist current alerts to DuckDB for audit and Dashboard display.
- Run automatically at the end of the daily close workflow.
- Expose a compact summary in Dashboard V2 `today` and `health`.

It does not send email, chat, or mobile push notifications yet. Those channels can consume the same alert table later.

## Data Model

Add `model_monitor_alerts`:

- `alert_id`: deterministic key for one model/date/metric.
- `model_name`, `model_version`, `experiment_id`.
- `alert_date`, `severity`, `metric_name`, `observed_value`, `threshold_value`.
- `status`: `ACTIVE` or `RESOLVED`.
- `message`, `context_json`, timestamps.

The monitor writes active alerts for the current evaluation date and resolves older active alerts for the same model/version when the metric is no longer failing.

## Evaluation Rules

The first version uses conservative local thresholds:

- Production prediction must exist in `qlib_predictions` with `mode='production_inference'`.
- Latest production prediction should cover the latest local A-share data date.
- Production signals should exist for the current `model_version` after prediction.
- If enough realized outcomes exist, average benchmark-relative alpha should not be negative.
- If enough realized outcomes exist, hit rate should not fall below 45%.
- If enough signals exist, `NO_ACTION` rate should not exceed 50%.

No outcome alert is only informational until there are enough filled and matured paper trades.

## Interfaces

Add `src.monitoring.model_monitor`:

- `evaluate_production_model(conn, as_of=None) -> dict`
- `update_production_model_monitor(conn=None, as_of=None) -> dict`
- CLI: `python -m src.monitoring.model_monitor update`

Dashboard V2 reads the same service through a safe wrapper and returns:

- `model_monitor.status`: `ok`, `degraded`, or `failed`
- `model_monitor.alerts`: active alerts
- `model_monitor.metrics`: prediction, signal, execution, and outcome summary

## Workflow Integration

Append the monitor after `signal_outcomes` in:

- `scripts/daily_close.sh`
- `src.dashboard.job_manager` `daily_close_workflow`

This keeps monitoring downstream of prediction, paper trading, NAV rebuild, and outcome updates.

## Testing

Add tests that prove:

- Missing production predictions create an active alert.
- Healthy prediction/signal/outcome samples produce no critical alert and resolve previous stale alerts.
- Dashboard V2 health exposes monitor alerts.
- Daily close workflow runs model monitoring after signal outcomes.

