# P1 Signal Outcomes Design

Review date: 2026-05-15
Status: Approved for implementation by ongoing P1 work request

## Goal

Persist forward returns for executed signals so strategy quality can be measured from paper-trading reality, not only backtest or latest signal lists.

## Scope

This release stores outcome rows and adds a CLI/daily workflow step. Dashboard attribution can build on the table later.

## Data Model

Add `signal_outcomes` keyed by `(signal_id, horizon_days)`:

- signal metadata: `model_name`, `model_version`, `symbol`, `side`, `signal_date`
- execution metadata: `execution_date`, `execution_price`
- outcome metadata: `horizon_days`, `outcome_date`, `outcome_price`, `return_pct`, `status`

`status = READY` when the horizon close exists; `PENDING` when there is not enough future price history yet.

## Return Semantics

- BUY: `outcome_price / execution_price - 1`
- SELL/SHORT: `execution_price / outcome_price - 1`

This makes a sell signal positive when price falls after the exit signal.

## Integration

Add `src.signals.outcome_tracker` with:

- pure calculation helpers;
- `update_signal_outcomes(conn, horizons=(1, 5, 20))`;
- CLI `python -m src.signals.outcome_tracker update`.

Daily close and Dashboard job workflow should run it after paper trading and NAV/performance updates.

## Acceptance

- BUY and SELL outcome math is tested.
- Missing future prices produce `PENDING` rows instead of crashing.
- The daily workflow includes the outcome tracker step.
