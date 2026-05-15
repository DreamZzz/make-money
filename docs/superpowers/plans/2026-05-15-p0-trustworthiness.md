# P0 Trustworthiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the biggest trust gaps in P0: survivorship-biased universes, impossible open fills, incomparable backtest engines, partial daily workflow entry points, and missing rolling IC decay views.

**Architecture:** Add historical membership and tradability as shared data/model boundaries, then consume them from Qlib conversion, Qlib simulations, paper trading, and Dashboard reporting. Keep changes additive and backward-compatible so existing local data remains usable.

**Tech Stack:** Python 3.12, DuckDB, pandas, AkShare/yfinance, Qlib file-storage instruments, pytest, Streamlit/Plotly.

## Execution Status

Updated: 2026-05-15

| Task | Status | Notes |
|---|---|---|
| Task 1 | Done | Added `index_member_history`, migrations, and membership helpers. |
| Task 2 | Done | `init_all` persists HS300/CSI500 snapshots with a price-start fallback; update runs can reconcile dated CSIndex snapshots into add/remove ranges. |
| Task 3 | Done | Qlib conversion writes dynamic `all/csi300/csi500/csi800` instrument files from membership ranges; Alpha158 defaults to `csi800`. |
| Task 4 | Done | Added shared `src/portfolio/execution_guards.py`. |
| Task 5 | Done | Qlib simulations now require tradeable entry and exit opens. |
| Task 6 | Done | Paper trading marks non-tradeable opens as `NO_ACTION` without inserting orders. |
| Task 7 | Done | Added `engine` and `decision_scope`; vectorbt is `research_only` by default. |
| Task 8 | Done | `scripts/daily_update.py` delegates to `scripts/daily_close.sh`. |
| Task 9 | Done | Dashboard IC view uses rolling 30/60/180 day IC and RankIC columns. |
| Task 10 | Done | Latest `pytest -q` passed with 131 tests; `ruff check .` unavailable because `ruff` is not installed. |

Remaining caveat: Task 1/2 now accumulate true add/remove ranges from repeated CSIndex dated snapshots, but older pre-existing historical adjustment archives still need a dedicated data-source ingestion task before survivorship bias is fully eliminated for long backtests.

---

## Task 1: Add Historical Index Membership Storage

**Files:**
- Modify: `src/data_pipeline/schema.sql`
- Modify: `src/data_pipeline/loader.py`
- Create: `src/data_pipeline/index_membership.py`
- Test: `tests/test_index_membership.py`

- [ ] **Step 1: Write membership tests**

Create `tests/test_index_membership.py` with tests for:

```python
from datetime import date

import pandas as pd

from src.data_pipeline.index_membership import active_members, merge_membership_ranges, normalize_current_snapshot


def test_normalize_current_snapshot_uses_price_start_as_snapshot_start():
    result = normalize_current_snapshot("000300", ["000001", "000002"], date(2021, 1, 4), source="akshare_snapshot")
    assert list(result["index_code"]) == ["000300", "000300"]
    assert list(result["symbol"]) == ["000001", "000002"]
    assert set(result["start_date"]) == {date(2021, 1, 4)}
    assert result["end_date"].isna().all()
    assert set(result["source"]) == {"akshare_snapshot"}


def test_merge_membership_ranges_merges_overlapping_ranges():
    df = pd.DataFrame(
        {
            "index_code": ["000300", "000300", "000300"],
            "symbol": ["000001", "000001", "000002"],
            "start_date": [date(2021, 1, 1), date(2021, 6, 1), date(2022, 1, 1)],
            "end_date": [date(2021, 12, 31), None, None],
            "source": ["x", "x", "x"],
        }
    )
    merged = merge_membership_ranges(df)
    row = merged[merged["symbol"] == "000001"].iloc[0]
    assert row["start_date"] == date(2021, 1, 1)
    assert pd.isna(row["end_date"])


def test_active_members_respects_date_ranges():
    df = pd.DataFrame(
        {
            "index_code": ["000300", "000300"],
            "symbol": ["A", "B"],
            "start_date": [date(2021, 1, 1), date(2022, 1, 1)],
            "end_date": [date(2021, 12, 31), None],
            "source": ["x", "x"],
        }
    )
    assert active_members(df, "000300", date(2021, 6, 1)) == {"A"}
    assert active_members(df, "000300", date(2022, 6, 1)) == {"B"}
```

- [ ] **Step 2: Run the new test and confirm failure**

Run: `pytest tests/test_index_membership.py -q`

Expected before implementation: import failure for `src.data_pipeline.index_membership`.

- [ ] **Step 3: Add additive schema**

Add `index_member_history` to `src/data_pipeline/schema.sql`. Add idempotent `ALTER TABLE` calls in `src/data_pipeline/loader.py` for the same columns so old DuckDB files migrate safely.

- [ ] **Step 4: Implement membership helpers**

Create `src/data_pipeline/index_membership.py` with:

- `normalize_current_snapshot(index_code, symbols, price_start, source)`
- `merge_membership_ranges(df)`
- `upsert_index_member_history(conn, df)`
- `load_index_member_history(conn, index_codes=None)`
- `active_members(df, index_code, as_of)`

Use pandas date conversion, stable sorting, and open-ended `end_date` as active through the future.

- [ ] **Step 5: Pass membership tests**

Run: `pytest tests/test_index_membership.py -q`

Expected: all tests pass.

## Task 2: Persist Membership Snapshots During Data Init

**Files:**
- Modify: `src/data_pipeline/main.py`
- Test: `tests/test_index_membership.py`

- [ ] **Step 1: Add a test for snapshot persistence**

Extend `tests/test_index_membership.py` with a DuckDB-backed test that initializes schema, upserts a snapshot, reloads it, and verifies active membership.

- [ ] **Step 2: Add persistence in `init_all`**

After fetching HS300 and CSI500 symbols, compute the earliest local A-share price date if available, otherwise use the configured start date. Persist both snapshots with `source='akshare_snapshot'`.

- [ ] **Step 3: Preserve current coverage behavior**

Do not remove existing coverage checks in `src/data_pipeline/main.py`; add membership persistence as an additional step.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_index_membership.py tests/test_history_backfill.py -q`

Expected: all selected tests pass.

## Task 3: Generate Dynamic Qlib Instruments

**Files:**
- Modify: `scripts/convert_to_qlib.py`
- Test: `tests/test_index_membership.py`

- [ ] **Step 1: Add instrument writer tests**

Add tests for a pure helper that converts membership ranges into Qlib instrument rows:

```python
from datetime import date

import pandas as pd

from scripts.convert_to_qlib import build_instrument_rows


def test_build_instrument_rows_uses_membership_ranges():
    membership = pd.DataFrame(
        {
            "index_code": ["000300", "000905"],
            "symbol": ["A", "B"],
            "start_date": [date(2021, 1, 1), date(2021, 2, 1)],
            "end_date": [date(2021, 12, 31), None],
        }
    )
    rows = build_instrument_rows(membership)
    assert set(rows["symbol"]) == {"A", "B"}
    assert rows.loc[rows["symbol"] == "A", "start"].iloc[0] == "2021-01-01"
    assert rows.loc[rows["symbol"] == "B", "end"].iloc[0] >= "2099-12-31"
```

- [ ] **Step 2: Implement instrument helpers**

Add helpers in `scripts/convert_to_qlib.py`:

- `build_price_instrument_rows(df)`
- `build_instrument_rows(membership)`
- `write_instrument_files(instrument_dir, price_rows, membership_rows)`

Write `all.txt`, `csi300.txt`, `csi500.txt`, and `csi800.txt`.

- [ ] **Step 3: Update manual dump path**

Replace the current loop that writes identical `all`, `csi300`, and `csi500` files with calls to `write_instrument_files`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_index_membership.py -q`

Expected: all tests pass.

## Task 4: Add Shared Open-Tradability Guard

**Files:**
- Create: `src/portfolio/tradability.py`
- Test: `tests/test_tradability.py`

- [ ] **Step 1: Write guard tests**

Create `tests/test_tradability.py` covering normal open, suspended row, ST row, limit-up open, limit-down open, and missing previous close.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_tradability.py -q`

Expected before implementation: import failure for `src.portfolio.tradability`.

- [ ] **Step 3: Implement `src/portfolio/tradability.py`**

Implement:

- `derive_prev_close(df)`
- `is_cn_open_limit(open_price, prev_close, threshold=0.095)`
- `annotate_open_tradability(df, market_col='market', threshold=0.095)`

Return `tradable_at_open=True` by default, then set false with reason `suspended`, `st`, or `open_limit`.

- [ ] **Step 4: Pass guard tests**

Run: `pytest tests/test_tradability.py -q`

Expected: all tests pass.

## Task 5: Use Tradability Guard in Qlib Simulations

**Files:**
- Modify: `src/backtest/qlib_runner.py`
- Test: `tests/test_architecture.py`

- [ ] **Step 1: Add regression test**

Add a test where a high-scoring symbol opens at limit-up on T+1 and a lower-scoring normal symbol is selected instead.

- [ ] **Step 2: Load guard fields**

Update `_load_price_frame` to select `pre_close`, `is_st`, and `is_suspended`; add derived `market='CN'`.

- [ ] **Step 3: Apply guard to entry candidates**

In `simulate_topn_open` and `simulate_topn_open_constrained`, filter ranked candidates using `tradable_at_open` for the T+1 entry date.

- [ ] **Step 4: Track skip metrics**

Add return attrs for skipped open-limit, suspended, and ST counts. Include them in grid metrics JSON without changing core return field names.

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_architecture.py::test_qlib_topn_skips_limit_up_entry -q`

Expected: test passes.

## Task 6: Use Tradability Guard in Paper Trading

**Files:**
- Modify: `src/portfolio/paper_engine.py`
- Test: `tests/test_open_trade_workflow.py`

- [ ] **Step 1: Add paper-trade regression test**

Seed a signal and T+1 daily price where `open / pre_close - 1 >= 0.095`. Assert no order is inserted and the signal is marked `NO_ACTION` with a reason containing `open_limit`.

- [ ] **Step 2: Load execution quote with guard fields**

Replace or complement `_get_open_price` with `_get_open_quote` returning `open`, `pre_close`, `is_st`, `is_suspended`, and `market`.

- [ ] **Step 3: Mark non-tradable signals handled**

Before cash and lot checks, call `annotate_open_tradability`. For non-tradable rows, call `_mark_signal_handled(..., status='NO_ACTION', status_reason='不可成交: <reason>')`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_open_trade_workflow.py -q`

Expected: all open-trade workflow tests pass.

## Task 7: Label Backtest Engines and Exclude Research Rows by Default

**Files:**
- Modify: `src/data_pipeline/schema.sql`
- Modify: `src/data_pipeline/loader.py`
- Modify: `src/backtest/results.py`
- Modify: `src/backtest/vectorbt_runner.py`
- Modify: `src/backtest/comparator.py`
- Test: `tests/test_architecture.py`

- [ ] **Step 1: Add comparator tests**

Add a test that inserts one `qlib_custom` row and one `vectorbt_research` row, then verifies default comparison returns only `qlib_custom`.

- [ ] **Step 2: Add nullable metadata columns**

Add `engine`, `cost_model`, and `tradability_model` to `backtest_results` and loader migrations.

- [ ] **Step 3: Extend save helper**

Update `save_backtest_result` to accept metadata defaults:

- `engine='qlib_custom'`
- `cost_model='commission_stamp_v1'`
- `tradability_model='open_guard_v1'`

Allow callers to override values.

- [ ] **Step 4: Label vectorbt**

Set vectorbt saved rows to:

- `engine='vectorbt_research'`
- `cost_model='simple_fee_v1'`
- `tradability_model='none'`

Add a simple fee/slippage deduction before metrics so research rows are less misleading even when shown.

- [ ] **Step 5: Filter comparator**

Change `load_all_results(include_research=False)` to exclude `vectorbt_research` by default. Keep report wording explicit.

- [ ] **Step 6: Run targeted tests**

Run: `pytest tests/test_architecture.py tests/test_qlib_report_service.py -q`

Expected: all selected tests pass.

## Task 8: Make Daily Update Delegate to the Full Close Workflow

**Files:**
- Modify: `scripts/daily_update.py`
- Test: `tests/test_architecture.py`

- [ ] **Step 1: Add script behavior test where feasible**

Add a small test for a helper function that returns the delegated command path for `scripts/daily_close.sh`.

- [ ] **Step 2: Refactor script into helper plus CLI**

Extract `daily_close_command(project_root, python)` and make `main()` call `bash scripts/daily_close.sh`.

- [ ] **Step 3: Preserve environment cleanup**

Keep proxy cleanup before delegation. Let `daily_close.sh` handle Dashboard stop/start.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_architecture.py -q`

Expected: all selected tests pass.

## Task 9: Add Rolling IC Decay Dashboard

**Files:**
- Modify: `src/dashboard/views/qlib_analysis.py`
- Test: `tests/test_qlib_report_service.py` or new service-level test if a helper is extracted

- [ ] **Step 1: Extract rolling helper**

Create a small pure helper in `src/dashboard/qlib_report_service.py` or inside the view if no reuse is needed:

- `add_rolling_ic_columns(df)`

- [ ] **Step 2: Test rolling columns**

Use deterministic `rank_ic` values and verify 30/60/180 rolling means with `min_periods=5`.

- [ ] **Step 3: Update IC tab**

Add latest summary cards and traces for rolling RankIC. Keep the existing daily RankIC and spread chart.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_qlib_report_service.py -q`

Expected: all selected tests pass.

## Task 10: Full Verification

**Files:**
- No code changes beyond prior tasks

- [ ] **Step 1: Run full tests**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run lint when available**

Run: `ruff check .`

Expected if installed: no lint errors. If `ruff` is not installed, record that the command is unavailable and do not claim lint passed.

- [ ] **Step 3: Update backlog**

Update `docs/iteration_backlog.md` with completed items, verification evidence, and remaining risks.

## Execution Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9
10. Task 10

This order keeps foundational data structures before consumers, then closes with UI and verification.
