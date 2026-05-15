# make-money Iteration Backlog

Review date: 2026-05-15
Last reviewed: 2026-05-15
Review cadence: weekly after Friday close, plus ad-hoc review after any P0 production change

This file is the durable project backlog. Keep it small enough to review every week, and link each active item to a design, implementation plan, test evidence, and final outcome.

## Operating Rules

- Status values: `Proposed`, `Ready`, `In Progress`, `Blocked`, `Done`, `Deferred`.
- Only `Ready` items may enter implementation. `Ready` means scope, files, acceptance checks, and rollback notes are written down.
- Each active item must have one owner, one priority, and one next action.
- When an item is completed, record the verification command and the observed result.
- Do not mix research-only metrics with production decision metrics unless the comparison scope is explicitly labeled.
- P0 work protects trustworthiness: survivorship control, tradability realism, engine comparability, and daily workflow consistency.

## Current Baseline

- Latest reviewed commit: `fea9f60`
- Existing dirty worktree note: `src/dashboard/job_manager.py` has unrelated local edits and should not be overwritten by backlog work.
- Test baseline from review: `pytest -q` passed with 112 tests.
- Lint baseline from review: `ruff check .` could not run because `ruff` was not installed in the active environment.

## Active P0 Track

| ID | Item | Status | Owner | Design | Acceptance |
|---|---|---|---|---|---|
| P0-01 | Add historical index membership and use it for Qlib instruments | In Progress | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Snapshot-backed `index_member_history`, dynamic Qlib instruments, and CSIndex snapshot reconciliation are implemented; next action is older historical adjustment archive ingestion |
| P0-02 | Apply open-tradability guards for backtest and paper trading | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | ST, suspended, and A-share limit-up/limit-down opens are not treated as fillable orders |
| P0-03 | Make vectorbt results non-comparable by default, with explicit engine labels | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Strategy comparison excludes research-only vectorbt rows unless explicitly requested |
| P0-04 | Unify daily production entry points around the full close workflow | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Daily update entry point cannot skip signal generation, Qlib prediction, paper trading, or NAV rebuild by accident |
| P0-05 | Add rolling IC decay view on existing `qlib_daily_metrics` | Done | Codex | `docs/superpowers/specs/2026-05-15-p0-trustworthiness-design.md` | Dashboard shows 30/60/180 trading-day rolling RankIC and IC means for successful experiments |

## Implementation Notes

### 2026-05-15 P0 Trustworthiness Pass

- Landed: `index_member_history` schema, membership helper module, snapshot persistence in `init_all`, dynamic Qlib `all/csi300/csi500/csi800` instrument files, and Alpha158 default universe switched to `csi800`.
- Landed next step: CSIndex dated snapshot ingestion and reconciliation. Repeated updates now accumulate real add/remove ranges by closing removed members at T-1 and opening new members at T.
- Landed: shared open execution guard for invalid open price, ST, suspension, and A-share opening limit moves; consumed by Qlib simulations and paper trading.
- Landed: `backtest_results.engine` and `backtest_results.decision_scope`; vectorbt saves as `research_only`, comparator excludes it by default.
- Landed: `scripts/daily_update.py` delegates to `scripts/daily_close.sh`, preserving proxy cleanup and full close workflow.
- Landed: Dashboard IC tab now shows 30/60/180 trading-day rolling RankIC and IC decay columns from `qlib_daily_metrics`.
- Remaining P0 risk: pre-existing older historical add/remove archives still need a data-source ingestion pass before long-horizon survivorship bias is fully removed.
- Verification: `pytest -q` passed with 131 tests on 2026-05-15; `ruff check .` could not run because `ruff` is not installed in the active shell.

## P1 Candidates

| ID | Item | Status | Owner | Next Action | Acceptance |
|---|---|---|---|---|---|
| P1-01 | Core-satellite allocator for index funds and stock strategies | Proposed | Codex | Write design after P0 | One account-level budget governs both index-fund signals and stock strategy orders |
| P1-02 | Portfolio exposure monitor | Proposed | Codex | Write design after allocator data model is known | Dashboard shows industry, size, valuation, and benchmark-relative concentration |
| P1-03 | Enforce daily turnover cap in executable rebalance plan | Proposed | Codex | Decide whether turnover cap applies to gross or one-way notional | Orders above cap are dropped by confidence/rank priority |
| P1-04 | Persist signal outcomes | Proposed | Codex | Reconcile with existing rule/Qlib A-B tracking | T+1/T+5/T+20 outcomes are stored by signal and model version |

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
| P3-02 | Add pre-commit for pytest smoke and ruff | Proposed | Codex | Install tooling decision | Local hook runs fast checks before commits |
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
- Blocked: true historical constituent-change ingestion still needs a reliable source mapping and validation sample.
- Newly discovered risk: `scripts/daily_update.py` is a partial workflow and can drift from `scripts/daily_close.sh`.
- Metrics to watch: Qlib excess return, max drawdown, annual turnover, rolling RankIC, skipped tradability count, skipped lot count.
- Next P0/P1 action: finish P0-01 by ingesting true historical constituent changes; then move to P1-01 core-satellite allocator design.
- Verification evidence: focused P0 regression suite passed with 15 tests; latest `pytest -q` passed with 131 tests; `ruff check .` unavailable because `ruff` is not installed.
