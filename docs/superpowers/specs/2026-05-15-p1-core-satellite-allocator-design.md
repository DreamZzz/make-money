# P1 Core-Satellite Allocator Design

Review date: 2026-05-15
Status: Phase 1 planner, satellite BUY cap, Dashboard card, and advisory core execution plan implemented

## Goal

Build one account-level allocator that coordinates index-fund core exposure and stock-strategy satellite exposure before execution. The allocator should answer a retail user's practical question: with one wallet, how much capital is available for core funds, how much for satellite stock signals, and what should be deferred when budgets conflict?

The default policy is configurable `core:satellite = 60:40`, with core assigned to index funds and satellite assigned to Alpha158 plus existing rule strategies. The first implementation should produce allocation recommendations and execution budgets; it should not replace `paper_engine` or index-fund signal generation in the same step.

## Current State

- Stock strategies write to `signals`; `src.portfolio.paper_engine` executes pending stock signals against a global cash ledger and `account_daily`.
- Index funds write independent `index_fund_signals` and manual `index_fund_snapshots`; they do not reserve cash in the same wallet.
- `cashbook` already provides a global account summary, available cash, and cash-flow-adjusted NAV.
- There is no table that records an account-level target split, sleeve drift, or per-sleeve execution budget.

## Proposed Architecture

Add `src/portfolio/allocator.py` as a planning layer with three responsibilities:

1. Load current account value, cash, stock market value, and index-fund market value.
2. Compute sleeve targets and drift from config.
3. Produce a budget plan for downstream execution.

The allocator writes durable planning rows to new tables:

- `allocation_plans(plan_id, plan_date, account_id, total_value, cash, core_target_pct, satellite_target_pct, core_value, satellite_value, core_budget, satellite_budget, status, created_at)`
- `allocation_plan_items(plan_id, sleeve, instrument_type, instrument_id, action, current_value, target_value, budget_delta, priority, reason)`

The first version is read-only from an execution perspective: it does not create `paper_orders` and does not mutate index-fund snapshots. It only constrains later execution by exposing `satellite_budget` and `core_budget`.

## Data Flow

1. Daily close workflow generates index-fund signals and stock signals as today.
2. Allocator runs after signal generation and before paper trading.
3. It calculates current sleeve values:
   - Satellite value from latest `paper_positions` or account stock position value.
   - Core value from `index_fund_snapshots` joined to latest `fund_nav`.
   - Cash from `account_daily`.
4. It assigns new cash first to underweight sleeves.
5. It ranks actions:
   - Risk-reducing sells/reduces first.
   - Underweight core ADD/BUY before satellite BUY when core is below tolerance.
   - Satellite BUY budget is capped by its sleeve budget.
6. Paper trading consumes `satellite_budget` as a stock BUY cap; index-fund planning consumes `core_budget` as advisory fund-level actions.

## Configuration

Add a portfolio allocation config block:

```yaml
portfolio:
  allocation:
    enabled: true
    core_target_pct: 0.60
    satellite_target_pct: 0.40
    rebalance_tolerance_pct: 0.05
    min_trade_amount: 1000
    core_cash_priority: true
```

If config is missing, allocator defaults to enabled=false for compatibility in code paths, but tests can instantiate it directly with defaults.

## Budget Semantics

Budget is one-way deployable cash, not gross turnover. A sleeve can have:

- Positive budget: cash allowed for new buys/adds.
- Zero budget: no new buy/add orders; sells/reduces still allowed.
- Negative drift: sleeve is overweight; allocator should recommend reduce/pause but not force liquidation in v1.

For v1, allocator should not automatically transfer proceeds between same-day stock sells and core buys unless those orders are explicitly modeled. It should rely on current available cash plus conservative sell-first ordering already present in `paper_engine`.

## Integration Plan

Phase 1: Planning only

- Add pure calculation helpers and unit tests. (Implemented)
- Add schema tables and persistence. (Implemented)
- Add CLI: `python -m src.portfolio.allocator plan`. (Implemented)
- Add daily job step after signal generation, before paper trading. (Implemented for Dashboard workflow; shell close script also generates a plan)

Phase 2: Execution caps

- Teach `paper_engine` to read latest active allocation plan and cap total BUY notional by `satellite_budget`. (Implemented for stock BUY cash requirement; SELL/SHORT remains unaffected)
- Add index-fund execution planning for `core_budget` without real brokerage integration. (Implemented as `allocation_plan_items.instrument_type = index_fund`)

Phase 3: Dashboard

- Add allocator card showing target/current core-satellite split, deployable budget, sleeve actions, and core fund execution plan. (Implemented in Portfolio Dashboard)
- Defer skipped-signal count until skipped stock signals are persisted as outcomes or execution audit rows.

## Testing

Unit tests should cover:

- Core underweight receives cash before satellite buys.
- Satellite overweight sets satellite buy budget to zero.
- Missing index-fund holdings do not crash and treat core value as zero.
- Plan persistence is idempotent by `plan_date/account_id` or uses immutable plan IDs with latest query.
- Paper-engine cap integration leaves SELL orders unaffected. (Covered by regression tests)

## Acceptance Criteria

P1-01 can be marked Done when:

- A generated allocation plan records current core/satellite values and budgets. (Implemented)
- The daily close workflow can create the plan without disrupting existing signal generation or paper trading. (Implemented)
- Dashboard and CLI expose the latest plan clearly enough for a retail user to see whether this week is core-heavy, satellite-heavy, or balanced. (Implemented)
- Existing tests pass, and new allocator tests cover the budget semantics above.

## Open Decisions

- Stock BUY execution enforces the latest active `satellite_budget`; core fund execution remains advisory in this release.
- Whether index-fund `REDUCE` should create a synthetic cash increase before satellite buys in a future executor.
- Whether target weights should be one global core/satellite split or per-market splits for CN/HK.
