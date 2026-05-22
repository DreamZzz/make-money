# Open Trade Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily close -> next open paper-trading chain idempotent, explainable, and consistent across generated signals, arbitration, execution, positions, and Dashboard V2.

**Architecture:** Keep strategy generation, global arbitration, execution sizing, and Dashboard presentation as separate responsibilities, but introduce shared execution-preview logic so UI and paper_engine use the same cash/lot/budget rules. Signal reruns must replace same-day semantic duplicates, arbitration must be refreshable against the current production model, and budget-blocked candidates must stop retrying blindly.

**Tech Stack:** Python 3.12, DuckDB, pandas, pytest, ruff, React/Vite TypeScript for Dashboard V2.

---

## Context From 2026-05-21 Open Review

- `open_paper_trade` ran successfully at `2026-05-21 09:40:03` and ended at `09:40:36`.
- No `paper_orders` were created on `2026-05-21`; cash stayed at `¥118,204.53`.
- Latest allocation plan had `satellite_budget = 0`, so no stock BUY was expected.
- Three alpha158 BUY candidates reached paper_engine:
  - `002281` 光迅科技: Qlib rank 1, blocked by target-position lot sizing.
  - `600563` 法拉电子: Qlib rank 2, blocked by target-position lot sizing.
  - `688126` 沪硅产业: Qlib rank 3, blocked by satellite budget.
- Rule strategy signals had same-day duplicate generations around `20:08` and `22:50`.
- Some arbitration rows were stale relative to the latest production prediction, indicating rerun/production-switch decision refresh is not strong enough.
- Dashboard currently says "一手门槛", while paper_engine actually executes by target position value and rounded board lots.

---

## File Structure

- Modify `src/signals/lifecycle.py`
  - Owns signal status transitions and same-day semantic replacement.
- Modify `src/signals/generator.py`
  - Calls same-day replacement before inserting freshly generated daily signals.
- Modify `src/signals/arbiter.py`
  - Adds refreshable same-date arbitration and guards against stale Qlib decision rows.
- Create `src/portfolio/execution_preview.py`
  - Shared source of truth for BUY sizing, board lots, required cash, budget status, and human-readable block reasons.
- Modify `src/portfolio/paper_engine.py`
  - Uses execution preview for BUY sizing and marks budget-blocked signals explicitly.
- Modify `scripts/open_paper_trade.py`
  - Ensures open-trade target quote updates populate or validate `pre_close`.
- Modify `src/dashboard_v2/service.py`
  - Replaces one-lot-only candidate view with execution-preview rows.
- Modify `frontend/dashboard-v2/src/pages/RebalancePage.tsx`
  - Renames "一手门槛" copy to target-position execution requirement.
- Modify tests:
  - `tests/test_signal_lifecycle.py`
  - `tests/test_signal_arbiter.py`
  - `tests/test_open_trade_workflow.py`
  - `tests/test_dashboard_v2_service.py`
  - `tests/test_dashboard_runtime_scripts.py`

---

## Priority Overview

| Priority | Work | Why |
|---|---|---|
| P0 | Same-day signal idempotency | Stops duplicated signals/outcomes and noisy dashboards. |
| P0 | Arbitration refresh after production/rerun | Ensures decisions reflect current production Qlib predictions. |
| P0 | Shared execution preview | Makes Dashboard and paper_engine explain the same result. |
| P1 | Budget-blocked signal status | Stops repeated open retries when satellite budget is zero. |
| P1 | `pre_close` validation for tradeability | Makes涨跌停/停牌 guard reliable at open. |
| P2 | Run-contract regression + daily audit | Keeps close/open responsibilities from drifting again. |

---

### Task 1: P0 Same-Day Signal Idempotency

**Files:**
- Modify: `src/signals/lifecycle.py`
- Modify: `src/signals/generator.py`
- Test: `tests/test_signal_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle test for same-day semantic replacement**

Create `tests/test_signal_lifecycle.py` with:

```python
from __future__ import annotations

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.signals.lifecycle import retire_same_day_replaced_signals


def test_retire_same_day_replaced_signals_supersedes_non_filled_same_key():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, executed, status, status_reason
        )
        VALUES
          ('old_active', 'trend_following', '1.0', '000001',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.7, 0.8, FALSE, 'ACTIVE', NULL),
          ('old_no_action', 'trend_following', '1.0', '000001',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.6, 0.7, TRUE, 'NO_ACTION', '旧仲裁拒绝'),
          ('old_filled', 'trend_following', '1.0', '000002',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.9, 0.9, TRUE, 'FILLED', '成交')
    """)
    new_signals = pd.DataFrame([
        {
            "signal_id": "new_buy",
            "model_name": "trend_following",
            "symbol": "000001",
            "side": "BUY",
            "signal_ts": pd.Timestamp("2026-05-20"),
        },
        {
            "signal_id": "new_filled_key",
            "model_name": "trend_following",
            "symbol": "000002",
            "side": "BUY",
            "signal_ts": pd.Timestamp("2026-05-20"),
        },
    ])

    count = retire_same_day_replaced_signals(conn, new_signals)

    rows = conn.execute("""
        SELECT signal_id, status, superseded_by
        FROM signals
        ORDER BY signal_id
    """).fetchall()
    assert count == 2
    assert rows == [
        ("old_active", "SUPERSEDED", "new_buy"),
        ("old_filled", "FILLED", None),
        ("old_no_action", "SUPERSEDED", "new_buy"),
    ]
    conn.close()
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
python3.12 -m pytest tests/test_signal_lifecycle.py::test_retire_same_day_replaced_signals_supersedes_non_filled_same_key -q
```

Expected: FAIL because `retire_same_day_replaced_signals` does not exist.

- [ ] **Step 3: Implement same-day replacement helper**

Add to `src/signals/lifecycle.py`:

```python
def retire_same_day_replaced_signals(conn: duckdb.DuckDBPyConnection, new_signals: pd.DataFrame) -> int:
    """Supersede non-filled signals with the same model/symbol/side/signal date.

    This is intentionally stronger than retire_replaced_signals: daily reruns
    often reuse the same signal_ts date, so timestamp-only replacement misses
    same-day duplicates.
    """
    if new_signals.empty:
        return 0

    required = {"signal_id", "model_name", "symbol", "side", "signal_ts"}
    if not required.issubset(set(new_signals.columns)):
        return 0

    replacements = new_signals[list(required)].copy()
    replacements["signal_date"] = pd.to_datetime(replacements["signal_ts"]).dt.date
    replacements = replacements.drop_duplicates(["model_name", "symbol", "side", "signal_date"], keep="first")
    conn.execute("CREATE OR REPLACE TEMP TABLE _same_day_signal_replacements AS SELECT * FROM replacements")
    rows = conn.execute("""
        UPDATE signals old
        SET executed = TRUE,
            status = 'SUPERSEDED',
            status_reason = '同日重新生成信号，已被最新同口径信号替代',
            execution_date = new.signal_date,
            superseded_by = new.signal_id,
            updated_at = CURRENT_TIMESTAMP
        FROM _same_day_signal_replacements new
        WHERE old.signal_id <> new.signal_id
          AND old.model_name = new.model_name
          AND old.symbol = new.symbol
          AND UPPER(old.side) = UPPER(new.side)
          AND CAST(old.signal_ts AS DATE) = new.signal_date
          AND COALESCE(old.status, 'ACTIVE') IN ('ACTIVE', 'NO_ACTION', 'SUPERSEDED')
          AND old.signal_id NOT IN (
              SELECT signal_id FROM paper_orders WHERE status = 'FILLED'
          )
        RETURNING old.signal_id
    """).fetchall()
    return len(rows)
```

- [ ] **Step 4: Wire helper into signal saving**

In `src/signals/generator.py`, change the import:

```python
from src.signals.lifecycle import expire_stale_signals, retire_replaced_signals, retire_same_day_replaced_signals
```

Then in `save_to_db`, after `expire_stale_signals(conn)` and before `retire_replaced_signals(conn, insert_df)`, add:

```python
    retire_same_day_replaced_signals(conn, insert_df)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3.12 -m pytest tests/test_signal_lifecycle.py tests/test_architecture.py::test_retire_replaced_signals_marks_old_active_signal -q
python3.12 -m ruff check src/signals/lifecycle.py src/signals/generator.py tests/test_signal_lifecycle.py
```

Expected: PASS.

---

### Task 2: P0 Refresh Arbitration Against Current Production Predictions

**Files:**
- Modify: `src/signals/arbiter.py`
- Test: `tests/test_signal_arbiter.py`

- [ ] **Step 1: Write failing test for same-date stale decision prevention**

Append to `tests/test_signal_arbiter.py`:

```python
def test_arbiter_refreshes_decision_when_fresh_same_day_qlib_prediction_exists():
    conn = duckdb.connect(":memory:")
    _seed_base(conn)
    conn.execute("""
        INSERT INTO qlib_model_registry (
            model_version, experiment_id, model_name, status, market, published_at
        )
        VALUES ('alpha158-new', 'EXP-NEW', 'alpha158', 'production', 'CN', TIMESTAMP '2026-05-20 22:30:00')
    """)
    conn.execute("""
        INSERT INTO qlib_predictions (
            experiment_id, model_name, model_version, mode, prediction_date,
            symbol, score, rank, confidence, selected
        )
        VALUES
          ('EXP-OLD', 'alpha158', 'alpha158-old', 'production_inference',
           DATE '2026-05-15', '000001', 0.1, 650, 0.30, FALSE),
          ('EXP-NEW', 'alpha158', 'alpha158-new', 'production_inference',
           DATE '2026-05-20', '000001', 0.9, 1, 0.95, TRUE)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('rule_buy_refresh', 'trend_following', '1.0', '000001',
                TIMESTAMP '2026-05-20 15:00:00', 'BUY', 1, 0.95, 0.10, FALSE, 'ACTIVE')
    """)
    conn.execute("""
        INSERT INTO signal_decisions (
            decision_id, signal_id, decision_date, model_name, symbol, side,
            signal_ts, decision, decision_reason, consensus_status,
            arbiter_version, qlib_prediction_date, qlib_rank, qlib_confidence
        )
        VALUES (
            'DEC-OLD', 'rule_buy_refresh', DATE '2026-05-20', 'trend_following', '000001', 'BUY',
            TIMESTAMP '2026-05-20 15:00:00', 'REJECTED', '旧模型过期', 'STALE',
            'signal_arbiter_v1', DATE '2026-05-15', 650, 0.30
        )
    """)

    result = arbitrate_pending_signals(conn, as_of=date(2026, 5, 20), config=DEFAULT_CONFIG)

    row = conn.execute("""
        SELECT decision, consensus_status, qlib_prediction_date, qlib_rank, qlib_confidence
        FROM signal_decisions
        WHERE signal_id = 'rule_buy_refresh'
        ORDER BY updated_at DESC
        LIMIT 1
    """).fetchone()
    assert result.accepted == 1
    assert row == ("ACCEPTED", "CONSENSUS", date(2026, 5, 20), 1, 0.95)
    conn.close()
```

- [ ] **Step 2: Run the test and verify current behavior**

Run:

```bash
python3.12 -m pytest tests/test_signal_arbiter.py::test_arbiter_refreshes_decision_when_fresh_same_day_qlib_prediction_exists -q
```

Expected before fix: FAIL if old decision is not replaced or old production rows are preferred.

- [ ] **Step 3: Restrict Qlib consensus to the current production model when registry exists**

Replace `_load_latest_qlib_predictions` in `src/signals/arbiter.py` with logic that prefers `qlib_model_registry.status='production'`:

```python
def _load_latest_qlib_predictions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    try:
        return conn.execute("""
            WITH production AS (
                SELECT model_version
                FROM qlib_model_registry
                WHERE model_name = 'alpha158'
                  AND status = 'production'
                ORDER BY published_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT 1
            ),
            candidate_predictions AS (
                SELECT qp.symbol, qp.prediction_date, qp.rank, qp.confidence, qp.score, qp.model_version, qp.selected
                FROM qlib_predictions qp
                WHERE qp.model_name = 'alpha158'
                  AND qp.mode = 'production_inference'
                  AND (
                      NOT EXISTS (SELECT 1 FROM production)
                      OR qp.model_version = (SELECT model_version FROM production)
                  )
            )
            SELECT symbol, prediction_date, rank, confidence, score, model_version
            FROM candidate_predictions
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY prediction_date DESC, selected DESC, rank ASC
            ) = 1
        """).fetchdf()
    except Exception:
        return pd.DataFrame(columns=["symbol", "prediction_date", "rank", "confidence", "score", "model_version"])
```

- [ ] **Step 4: Make decision ids stable by signal id and arbiter version**

In `_base_decision`, replace random `decision_id`:

```python
"decision_id": f"DEC-{signal.get('signal_id')}-{ARBITER_VERSION}",
```

This makes `INSERT OR REPLACE` actually refresh the same signal decision instead of accumulating old rows for the same signal.

- [ ] **Step 5: Run arbiter regression tests**

Run:

```bash
python3.12 -m pytest tests/test_signal_arbiter.py -q
python3.12 -m ruff check src/signals/arbiter.py tests/test_signal_arbiter.py
```

Expected: PASS.

---

### Task 3: P0 Shared Execution Preview For Dashboard And Paper Engine

**Files:**
- Create: `src/portfolio/execution_preview.py`
- Modify: `src/portfolio/paper_engine.py`
- Modify: `src/dashboard_v2/service.py`
- Modify: `frontend/dashboard-v2/src/pages/RebalancePage.tsx`
- Test: `tests/test_open_trade_workflow.py`
- Test: `tests/test_dashboard_v2_service.py`

- [ ] **Step 1: Write failing sizing test**

Append to `tests/test_open_trade_workflow.py`:

```python
def test_execution_preview_explains_target_position_lot_requirement(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('002281', 'CN', '光迅科技')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, close, pre_close, high, low, volume)
        VALUES ('002281', DATE '2026-05-21', 230.15, 221.52, 229.01, 232, 220, 1000)
    """)
    conn.close()

    from src.portfolio.execution_preview import estimate_buy_execution

    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = estimate_buy_execution(
            conn=conn,
            symbol="002281",
            trade_date=date(2026, 5, 21),
            current_total=289_977,
            max_position_pct=0.05,
            available_cash=118_205,
            satellite_budget=0,
            market="CN",
        )
    finally:
        conn.close()

    assert row["display_name"] == "光迅科技（002281）"
    assert row["one_lot_cash"] == 23015.0
    assert row["target_position_cash"] == 14498.85
    assert row["rounded_qty"] == 0
    assert row["required_cash"] == 0
    assert row["status"] == "BLOCKED_LOT"
    assert "5%目标仓位" in row["block_reason"]
```

- [ ] **Step 2: Run the sizing test and verify it fails**

Run:

```bash
python3.12 -m pytest tests/test_open_trade_workflow.py::test_execution_preview_explains_target_position_lot_requirement -q
```

Expected: FAIL because `src.portfolio.execution_preview` does not exist.

- [ ] **Step 3: Implement `estimate_buy_execution`**

Create `src/portfolio/execution_preview.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any

import duckdb


def estimate_buy_execution(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    trade_date: date,
    current_total: float,
    max_position_pct: float,
    available_cash: float,
    satellite_budget: float | None,
    market: str = "CN",
    price: float | None = None,
    commission_rate: float = 0.00025,
    min_fee: float = 5.0,
) -> dict[str, Any]:
    name = _load_name(conn, symbol)
    execution_price = float(price or _load_open_or_close(conn, symbol, trade_date) or 0.0)
    one_lot_qty = 100 if market == "CN" else 1
    one_lot_cash = execution_price * one_lot_qty if execution_price > 0 else 0.0
    target_position_cash = max(float(current_total) * float(max_position_pct), 0.0)
    rounded_qty = int(target_position_cash / execution_price / one_lot_qty) * one_lot_qty if execution_price > 0 else 0
    execution_value = float(rounded_qty * execution_price)
    fee = max(execution_value * float(commission_rate), float(min_fee)) if execution_value > 0 else 0.0
    required_cash = execution_value + fee

    status = "EXECUTABLE"
    block_reason = "预算和整手约束均通过"
    budget_gap = 0.0
    if execution_price <= 0:
        status = "NO_PRICE"
        block_reason = "缺少开盘价/收盘价，无法计算执行金额"
    elif rounded_qty <= 0:
        status = "BLOCKED_LOT"
        block_reason = (
            f"{max_position_pct:.0%}目标仓位约 {target_position_cash:,.0f} 元，"
            f"不足一手所需 {one_lot_cash:,.0f} 元"
        )
    elif satellite_budget is not None and required_cash > satellite_budget + 1e-9:
        status = "BLOCKED_BUDGET"
        budget_gap = required_cash - max(float(satellite_budget), 0.0)
        block_reason = f"Satellite预算不足：需要 {required_cash:,.0f} 元，剩余 {float(satellite_budget):,.0f} 元"
    elif required_cash > available_cash + 1e-9:
        status = "BLOCKED_CASH"
        budget_gap = required_cash - max(float(available_cash), 0.0)
        block_reason = f"现金不足：需要 {required_cash:,.0f} 元，可用 {float(available_cash):,.0f} 元"

    return {
        "symbol": symbol,
        "name": name,
        "display_name": f"{name}（{symbol}）" if name and name != symbol else symbol,
        "trade_date": trade_date.isoformat(),
        "market": market,
        "execution_price": round(execution_price, 4),
        "one_lot_cash": round(one_lot_cash, 2),
        "target_position_cash": round(target_position_cash, 2),
        "rounded_qty": rounded_qty,
        "execution_value": round(execution_value, 2),
        "fee": round(fee, 2),
        "required_cash": round(required_cash, 2),
        "available_cash": round(float(available_cash), 2),
        "satellite_budget": None if satellite_budget is None else round(float(satellite_budget), 2),
        "budget_gap": round(max(budget_gap, 0.0), 2),
        "status": status,
        "block_reason": block_reason,
    }


def _load_name(conn: duckdb.DuckDBPyConnection, symbol: str) -> str:
    row = conn.execute("SELECT COALESCE(name, symbol) FROM stock_info WHERE symbol = ? LIMIT 1", [symbol]).fetchone()
    return str(row[0]) if row and row[0] else symbol


def _load_open_or_close(conn: duckdb.DuckDBPyConnection, symbol: str, trade_date: date) -> float | None:
    row = conn.execute("""
        SELECT COALESCE(open, close)
        FROM daily_price
        WHERE symbol = ? AND trade_date = ?
        LIMIT 1
    """, [symbol, trade_date]).fetchone()
    return float(row[0]) if row and row[0] is not None else None
```

- [ ] **Step 4: Replace duplicated paper_engine BUY sizing with shared helper**

In `src/portfolio/paper_engine.py`, import:

```python
from src.portfolio.execution_preview import estimate_buy_execution
```

Inside the BUY branch, after `max_position` is finalized and before `qty` is assigned, call:

```python
preview = estimate_buy_execution(
    conn=conn,
    symbol=sym,
    trade_date=next_day,
    current_total=current_total,
    max_position_pct=max_position,
    available_cash=session_cash,
    satellite_budget=satellite_budget_remaining,
    market=signal_market,
    price=price,
    commission_rate=commission,
    min_fee=5.0 if signal_market == "CN" else 10.0,
)
```

Use `preview["rounded_qty"]` as `qty`, `preview["required_cash"]` as the required amount, and `preview["block_reason"]` for user-facing skip reasons. Keep SELL behavior unchanged.

- [ ] **Step 5: Update Dashboard V2 candidate rows to expose execution requirement**

In `src/dashboard_v2/service.py`, update `_load_one_lot_gaps` or replace it with `_load_satellite_execution_candidates` so each row includes:

```python
{
    "one_lot_cash": 23015.0,
    "target_position_cash": 14498.85,
    "rounded_qty": 0,
    "required_cash": 0.0,
    "satellite_budget": 0.0,
    "budget_gap": 0.0,
    "execution_status": "EXECUTABLE|BLOCKED_LOT|BLOCKED_BUDGET|BLOCKED_CASH|NO_PRICE",
    "decision": "5%目标仓位约 14,499 元，不足一手所需 23,015 元",
}
```

- [ ] **Step 6: Rename frontend copy away from one-lot-only language**

In `frontend/dashboard-v2/src/pages/RebalancePage.tsx`, change compact candidate detail from:

```tsx
return `一手约 ${formatValueForField("one_lot_cash", row.one_lot_cash, row)}${confidence}${model}`;
```

to:

```tsx
const required = row.required_cash === undefined ? "" : ` · 执行需 ${formatValueForField("required_cash", row.required_cash, row)}`;
const target = row.target_position_cash === undefined ? "" : ` · 目标仓位 ${formatValueForField("target_position_cash", row.target_position_cash, row)}`;
return `一手约 ${formatValueForField("one_lot_cash", row.one_lot_cash, row)}${target}${required}${confidence}${model}`;
```

- [ ] **Step 7: Run focused backend/frontend tests**

Run:

```bash
python3.12 -m pytest tests/test_open_trade_workflow.py tests/test_dashboard_v2_service.py -q
cd frontend/dashboard-v2 && npm test -- --run && npm run build
python3.12 -m ruff check src/portfolio/execution_preview.py src/portfolio/paper_engine.py src/dashboard_v2/service.py tests/test_open_trade_workflow.py tests/test_dashboard_v2_service.py
```

Expected: PASS.

---

### Task 4: P1 Explicit Budget-Blocked Signal State

**Files:**
- Modify: `src/signals/lifecycle.py`
- Modify: `src/portfolio/paper_engine.py`
- Modify: `scripts/open_paper_trade.py`
- Test: `tests/test_open_trade_workflow.py`

- [ ] **Step 1: Write failing test that budget-blocked signals stop retrying**

Append to `tests/test_open_trade_workflow.py`:

```python
def test_paper_engine_marks_budget_blocked_signal_as_deferred(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('688126', 'CN', '沪硅产业')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, close, pre_close, high, low, volume)
        VALUES ('688126', DATE '2026-05-21', 30.01, 30.39, 30.00, 30.50, 29.80, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-20', 118205, 171772, 289977, 300000, 0.9666, 0, -0.0334)
    """)
    conn.execute("""
        INSERT INTO allocation_plans (
            plan_id, plan_date, account_id, total_value, cash, core_target_pct, satellite_target_pct,
            core_value, satellite_value, core_budget, satellite_budget, core_drift_pct, satellite_drift_pct
        )
        VALUES ('PLAN-ZERO-SAT', DATE '2026-05-20', 'default', 485580, 118205, 0.6, 0.4,
                195603, 171772, 95745, 0, -0.19, -0.04)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('budget_blocked_buy', 'alpha158', 'alpha158-prod', '688126',
                TIMESTAMP '2026-05-20 15:00:00', 'BUY', 0.9, 0.9, 0.05, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 0
    assert result["skipped_budget"] == 1
    assert result["pending"] == 0

    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = conn.execute("""
            SELECT executed, status, status_reason
            FROM signals WHERE signal_id = 'budget_blocked_buy'
        """).fetchone()
    finally:
        conn.close()

    assert row[0] is False
    assert row[1] == "DEFERRED_BUDGET"
    assert "Satellite预算不足" in row[2]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
python3.12 -m pytest tests/test_open_trade_workflow.py::test_paper_engine_marks_budget_blocked_signal_as_deferred -q
```

Expected: FAIL because current budget skip leaves the signal ACTIVE/pending.

- [ ] **Step 3: Add status constant**

In `src/signals/lifecycle.py`, add:

```python
DEFERRED_BUDGET = "DEFERRED_BUDGET"
```

- [ ] **Step 4: Add helper for non-terminal deferral**

In `src/portfolio/paper_engine.py`, add:

```python
def _mark_signal_deferred(
    conn: duckdb.DuckDBPyConnection,
    signal_id: str,
    execution_date: date,
    status: str,
    status_reason: str,
) -> None:
    conn.execute("""
        UPDATE signals
        SET executed = FALSE,
            execution_date = ?,
            status = ?,
            status_reason = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE signal_id = ?
    """, [execution_date, status, status_reason, signal_id])
```

Then in the satellite budget skip branch, call:

```python
_mark_signal_deferred(
    conn,
    sig["signal_id"],
    next_day,
    status="DEFERRED_BUDGET",
    status_reason=reason,
)
stats["handled_without_order"] += 1
handled_trade_keys.add(trade_key)
continue
```

- [ ] **Step 5: Ensure open target loader does not retry deferred budget rows**

In `scripts/open_paper_trade.py`, `_load_target_symbols` already filters:

```sql
AND COALESCE(status, 'ACTIVE') = 'ACTIVE'
```

Add a regression assertion to `tests/test_open_trade_workflow.py` or a focused script test if available: a `DEFERRED_BUDGET` signal must not be included in pending open targets.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3.12 -m pytest tests/test_open_trade_workflow.py -q
python3.12 -m ruff check src/signals/lifecycle.py src/portfolio/paper_engine.py scripts/open_paper_trade.py tests/test_open_trade_workflow.py
```

Expected: PASS.

---

### Task 5: P1 Open Tradeability Data Completeness

**Files:**
- Modify: `scripts/open_paper_trade.py`
- Modify: `src/portfolio/paper_engine.py`
- Test: `tests/test_open_trade_workflow.py`

- [ ] **Step 1: Write failing test for missing `pre_close` blocking CN BUY**

Append to `tests/test_open_trade_workflow.py`:

```python
def test_paper_engine_blocks_cn_buy_when_pre_close_missing(monkeypatch, tmp_path):
    db_path = _patch_temp_db(monkeypatch, tmp_path)
    conn = duckdb.connect(db_path)
    init_db(conn)
    conn.execute("INSERT INTO stock_info (symbol, country, name) VALUES ('000001', 'CN', '缺昨收')")
    conn.execute("""
        INSERT INTO daily_price (symbol, trade_date, open, close, pre_close, high, low, volume)
        VALUES ('000001', DATE '2026-05-21', 11, 11, NULL, 11, 11, 1000)
    """)
    conn.execute("""
        INSERT INTO account_daily (
            account_id, trade_date, cash, position_value, total_value,
            net_contribution, nav, daily_return, drawdown
        )
        VALUES ('default', DATE '2026-05-20', 100000, 0, 100000, 100000, 1, 0, 0)
    """)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, max_position_pct, executed, status
        )
        VALUES ('missing_preclose_buy', 'alpha158', 'alpha158-prod', '000001',
                TIMESTAMP '2026-05-20 15:00:00', 'BUY', 1, 1, 0.10, FALSE, 'ACTIVE')
    """)
    conn.close()

    result = pe.run("alpha158", market="CN")

    assert result["executed"] == 0
    assert result["skipped_untradeable"] == 1
    conn = duckdb.connect(db_path, read_only=True)
    try:
        reason = conn.execute("SELECT status_reason FROM signals WHERE signal_id = 'missing_preclose_buy'").fetchone()[0]
    finally:
        conn.close()
    assert "缺少昨收价" in reason
```

- [ ] **Step 2: Implement strict missing-preclose guard**

In `src/portfolio/paper_engine.py`, before `check_open_tradeable`, add:

```python
            if signal_market == "CN" and order_side == "BUY" and quote.get("pre_close") is None:
                stats["skipped_untradeable"] += 1
                _mark_signal_handled(
                    conn,
                    sig["signal_id"],
                    next_day,
                    status="NO_ACTION",
                    status_reason="缺少昨收价，无法判断A股涨跌停，开盘BUY跳过",
                )
                stats["handled_without_order"] += 1
                handled_trade_keys.add(trade_key)
                continue
```

- [ ] **Step 3: Backfill `pre_close` during open target update**

In `scripts/open_paper_trade.py`, after `upsert_daily_price(conn, df)`, call a helper:

```python
def _fill_missing_pre_close(conn, symbol: str, trade_date: date) -> None:
    conn.execute("""
        UPDATE daily_price cur
        SET pre_close = prev.close
        FROM (
            SELECT close
            FROM daily_price
            WHERE symbol = ?
              AND trade_date < ?
              AND close IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 1
        ) prev
        WHERE cur.symbol = ?
          AND cur.trade_date = ?
          AND cur.pre_close IS NULL
    """, [symbol, trade_date, symbol, trade_date])
```

Call it for every updated target date row, using today when the fetch includes today.

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3.12 -m pytest tests/test_open_trade_workflow.py::test_paper_engine_blocks_cn_buy_when_pre_close_missing tests/test_open_trade_workflow.py::test_paper_engine_marks_cn_limit_open_as_no_action -q
python3.12 -m ruff check scripts/open_paper_trade.py src/portfolio/paper_engine.py tests/test_open_trade_workflow.py
```

Expected: PASS.

---

### Task 6: P2 Run Contract Regression And Daily Audit Report

**Files:**
- Modify: `tests/test_dashboard_runtime_scripts.py`
- Create: `scripts/review_open_trade_day.py`
- Test: `tests/test_dashboard_runtime_scripts.py`

- [ ] **Step 1: Add regression test that close does not run paper_engine**

In `tests/test_dashboard_runtime_scripts.py`, add:

```python
def test_daily_close_does_not_execute_stock_paper_engine():
    script = Path("scripts/daily_close.sh").read_text()
    assert "src.portfolio.paper_engine" not in script
    assert "纸交易只能由 scripts/open_paper_trade.py 在开盘窗口执行" in script
```

If `Path` is not imported in that file, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Add a daily review helper script**

Create `scripts/review_open_trade_day.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date

from src.data_pipeline.loader import get_connection, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one open paper-trading day.")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date)

    conn = get_connection(read_only=True)
    try:
        init_db(conn)
        orders = conn.execute("""
            SELECT po.symbol, COALESCE(si.name, po.symbol) AS name, po.side,
                   po.order_qty, po.order_price, po.order_value, po.fee, s.model_name, s.status_reason
            FROM paper_orders po
            LEFT JOIN signals s ON po.signal_id = s.signal_id
            LEFT JOIN stock_info si ON po.symbol = si.symbol
            WHERE CAST(po.order_ts AS DATE) = ?
            ORDER BY po.order_ts, po.symbol
        """, [trade_date]).fetchdf()
        blocked = conn.execute("""
            SELECT s.symbol, COALESCE(si.name, s.symbol) AS name, s.side, s.model_name,
                   s.status, s.status_reason
            FROM signals s
            LEFT JOIN stock_info si ON s.symbol = si.symbol
            WHERE s.execution_date = ?
              AND s.status IN ('NO_ACTION', 'DEFERRED_BUDGET')
            ORDER BY s.status, s.model_name, s.symbol
        """, [trade_date]).fetchdf()
    finally:
        conn.close()

    print(f"# 开盘纸交易复盘 {trade_date}")
    print(f"成交订单: {len(orders)}")
    print(orders.to_string(index=False) if not orders.empty else "无成交订单")
    print()
    print(f"拦截/暂缓信号: {len(blocked)}")
    print(blocked.to_string(index=False) if not blocked.empty else "无拦截/暂缓信号")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run script smoke test**

Run:

```bash
python3.12 scripts/review_open_trade_day.py --date 2026-05-21
```

Expected: prints a readable summary with order count and blocked/deferred signals.

- [ ] **Step 4: Run script/runtime tests**

Run:

```bash
python3.12 -m pytest tests/test_dashboard_runtime_scripts.py -q
python3.12 -m ruff check scripts/review_open_trade_day.py tests/test_dashboard_runtime_scripts.py
```

Expected: PASS.

---

## Final Verification

After all tasks:

```bash
python3.12 -m pytest \
  tests/test_signal_lifecycle.py \
  tests/test_signal_arbiter.py \
  tests/test_open_trade_workflow.py \
  tests/test_dashboard_v2_service.py \
  tests/test_dashboard_runtime_scripts.py \
  -q

python3.12 -m ruff check \
  src/signals/lifecycle.py \
  src/signals/generator.py \
  src/signals/arbiter.py \
  src/portfolio/execution_preview.py \
  src/portfolio/paper_engine.py \
  src/dashboard_v2/service.py \
  scripts/open_paper_trade.py \
  scripts/review_open_trade_day.py \
  tests/test_signal_lifecycle.py \
  tests/test_signal_arbiter.py \
  tests/test_open_trade_workflow.py \
  tests/test_dashboard_v2_service.py \
  tests/test_dashboard_runtime_scripts.py

cd frontend/dashboard-v2 && npm test -- --run && npm run build
```

Expected:

- Same-day reruns do not leave duplicate active/no-action signals for the same semantic key.
- Arbitration decisions use the current production model and stable decision ids.
- Dashboard candidate rows explain one-lot cash, target-position cash, rounded quantity, required cash, and exact block reason.
- Budget-blocked signals are visible but not retried as ACTIVE every open.
- A-share BUY is blocked when `pre_close` is missing.
- `daily_close.sh` remains generation/planning only; `open_paper_trade.py` remains the only stock paper execution entrypoint.

---

## Rollout Order

1. Task 1 and Task 2 first; they clean the signal source of truth.
2. Task 3 next; it aligns Dashboard and execution engine.
3. Task 4 and Task 5 next; they reduce noisy retries and tradeability blind spots.
4. Task 6 last; it locks the operational contract and adds the daily review tool.

## Commit Plan

- Commit 1: `fix: make daily signal reruns idempotent`
- Commit 2: `fix: refresh arbitration with current production model`
- Commit 3: `feat: share execution preview between dashboard and paper engine`
- Commit 4: `fix: defer budget-blocked paper signals`
- Commit 5: `fix: require pre-close for cn open buy checks`
- Commit 6: `chore: add open trade review guardrails`
