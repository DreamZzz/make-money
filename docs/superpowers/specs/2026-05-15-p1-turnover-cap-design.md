# P1 Daily Turnover Cap Design

Review date: 2026-05-15
Status: Approved for implementation by ongoing P1 work request

## Goal

Keep the stock satellite sleeve aligned with the low/medium-frequency objective by capping same-day new BUY turnover in paper execution.

## Scope

This release enforces one-way BUY turnover, not gross sell-plus-buy turnover. SELL/SHORT orders remain exempt so the system can still de-risk or close positions even when the BUY budget is exhausted. A future rebalance planner can add full gross-turnover optimization once sell proceeds and replacement orders are modeled together.

## Policy

- Config key: `portfolio.max_daily_turnover_pct`, default `0.30`.
- Daily BUY turnover budget: `account_total_value * max_daily_turnover_pct`.
- Budget resets by execution date.
- BUY orders are sorted after sells by confidence and score so stronger signals consume limited turnover first.
- If a BUY order exceeds remaining daily turnover budget, it is skipped for this run and left `ACTIVE`.

## Acceptance

- Unit tests prove higher-confidence BUY orders are kept when turnover is limited.
- Unit tests prove SELL orders remain executable under a low BUY-turnover cap.
- Paper-engine result stats include `skipped_turnover`.
