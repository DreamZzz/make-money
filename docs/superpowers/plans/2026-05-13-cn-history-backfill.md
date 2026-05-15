# CN History Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and run a resumable CN historical daily-price backfill from 2016 so Qlib can train before the 2021-04-30 test window.

**Architecture:** Add a focused `src.data_pipeline.history_backfill` module that reads the current CN universe from DuckDB, fetches only missing pre-existing-min history via yfinance, and upserts into `daily_price` without overwriting existing recent rows. CN index backfill is handled separately via AkShare into `index_daily`, then Qlib data is regenerated through the existing runner.

**Tech Stack:** Python, DuckDB, pandas, yfinance fetcher, AkShare index fetcher, pytest.

---

### Task 1: Backfill Core

**Files:**
- Create: `src/data_pipeline/history_backfill.py`
- Create: `tests/test_history_backfill.py`

- [x] **Step 1: Write failing tests**

```python
def test_backfill_cn_history_only_inserts_dates_before_existing_min():
    # Existing local data starts on 2021-04-30.
    # Fake yfinance returns 2016 and 2021 rows.
    # Backfill must insert only rows earlier than 2021-04-30.
```

- [x] **Step 2: Implement minimal module**

```python
def backfill_cn_history(conn, start_date, end_date, fetch_daily=None, fetch_index=None, symbols=None, limit=None):
    # Select symbols, compute per-symbol fetch window, fetch, filter, upsert.
```

- [x] **Step 3: Add CLI**

```bash
python3 -m src.data_pipeline.history_backfill --start-date 2016-01-01 --end-date 2026-05-13
```

- [x] **Step 4: Verify**

```bash
pytest -q tests/test_history_backfill.py
python3 -m py_compile src/data_pipeline/history_backfill.py
```

### Task 2: Execute Backfill And Rebuild Qlib Data

**Files:**
- No code files required.

- [x] **Step 1: Run historical backfill**

```bash
python3 -m src.data_pipeline.history_backfill --start-date 2016-01-01 --end-date 2026-05-13
```

- [x] **Step 2: Verify local coverage**

```bash
python3 - <<'PY'
from src.data_pipeline.loader import get_connection
conn = get_connection(read_only=True)
print(conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT symbol), COUNT(*) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchall())
conn.close()
PY
```

- [x] **Step 3: Rebuild Qlib data**

```bash
/Applications/Xcode.app/Contents/Developer/usr/bin/python3 -m src.backtest.qlib_runner prepare-data --market cn
```

- [x] **Step 4: Final verification**

```bash
pytest -q
```
