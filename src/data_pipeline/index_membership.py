"""Index membership history helpers."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import duckdb
import pandas as pd

MEMBERSHIP_COLUMNS = ["index_code", "symbol", "start_date", "end_date", "source"]
SYMBOL_COLUMNS = ("成分券代码", "品种代码", "symbol", "code", "证券代码")
DATE_COLUMNS = ("日期", "snapshot_date", "trade_date", "date", "生效日期")


def normalize_current_snapshot(
    index_code: str,
    symbols: Iterable[str],
    price_start: date,
    source: str = "akshare_snapshot",
) -> pd.DataFrame:
    """Create open-ended membership rows from a current constituent snapshot."""
    clean_symbols = sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
    rows = [
        {
            "index_code": str(index_code),
            "symbol": symbol,
            "start_date": _as_date(price_start),
            "end_date": None,
            "source": source,
        }
        for symbol in clean_symbols
    ]
    return pd.DataFrame(rows, columns=MEMBERSHIP_COLUMNS)


def normalize_index_constituent_snapshot(
    index_code: str,
    raw: pd.DataFrame,
    source: str = "csindex_snapshot",
    fallback_date: date | None = None,
) -> pd.DataFrame:
    """Normalize a current constituent snapshot from common AkShare/CSIndex columns."""
    if raw.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)

    symbol_col = _pick_column(raw, SYMBOL_COLUMNS)
    if symbol_col is None:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    date_col = _pick_column(raw, DATE_COLUMNS)
    if date_col is None and fallback_date is None:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)

    df = raw.copy()
    df["symbol"] = df[symbol_col].map(_normalize_symbol)
    if date_col is not None:
        df["start_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    else:
        df["start_date"] = _as_date(fallback_date)
    df = df.dropna(subset=["symbol", "start_date"])
    rows = pd.DataFrame({
        "index_code": str(index_code),
        "symbol": df["symbol"],
        "start_date": df["start_date"],
        "end_date": None,
        "source": source,
    })
    return _normalize_frame(rows) if not rows.empty else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)


def reconcile_index_member_snapshot(
    conn: duckdb.DuckDBPyConnection,
    index_code: str,
    symbols: Iterable[str],
    snapshot_date: date,
    *,
    initial_start_date: date | None = None,
    source: str = "csindex_snapshot",
) -> int:
    """Reconcile a dated current snapshot into membership ranges.

    On first ingest, all members start from `initial_start_date` when provided.
    On subsequent ingests, missing active members are closed at T-1 and newly
    appearing members are opened on T.
    """
    clean_symbols = sorted({_normalize_symbol(symbol) for symbol in symbols if _normalize_symbol(symbol)})
    if not clean_symbols:
        return 0

    index_code = str(index_code)
    snapshot = _as_date(snapshot_date)
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM index_member_history WHERE index_code = ?",
        [index_code],
    ).fetchone()[0]

    active_df = conn.execute("""
        SELECT symbol, start_date, end_date, source
        FROM index_member_history
        WHERE index_code = ?
          AND start_date <= ?
          AND (end_date IS NULL OR end_date >= ?)
    """, [index_code, snapshot, snapshot]).fetchdf()
    active_symbols = set(active_df["symbol"].astype(str)) if not active_df.empty else set()
    snapshot_symbols = set(clean_symbols)

    changed = 0
    removed = sorted(active_symbols - snapshot_symbols)
    close_date = snapshot - timedelta(days=1)
    for symbol in removed:
        conn.execute("""
            UPDATE index_member_history
            SET end_date = ?,
                source = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE index_code = ?
              AND symbol = ?
              AND start_date <= ?
              AND (end_date IS NULL OR end_date >= ?)
        """, [
            close_date,
            _append_source(_source_for_symbol(active_df, symbol), source),
            index_code,
            symbol,
            snapshot,
            snapshot,
        ])
        changed += 1

    new_start = _as_date(initial_start_date) if existing_count == 0 and initial_start_date is not None else snapshot
    added = sorted(snapshot_symbols - active_symbols)
    if added:
        changed += upsert_index_member_history(
            conn,
            normalize_current_snapshot(index_code, added, new_start, source=source),
        )
    return changed


def merge_membership_ranges(df: pd.DataFrame) -> pd.DataFrame:
    """Merge overlapping or adjacent membership ranges per index and symbol."""
    if df.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)

    clean = _normalize_frame(df)
    merged_rows = []
    for (index_code, symbol), group in clean.groupby(["index_code", "symbol"], sort=True):
        current_start: date | None = None
        current_end: date | None = None
        sources: set[str] = set()

        for _, row in group.sort_values(["start_date", "end_date"]).iterrows():
            start = row["start_date"]
            end = row["end_date"] if pd.notna(row["end_date"]) else None
            source = str(row.get("source") or "")
            if source:
                sources.add(source)

            if current_start is None:
                current_start = start
                current_end = end
                continue

            if _ranges_touch(current_end, start):
                current_end = _max_open_end(current_end, end)
            else:
                merged_rows.append(_row(index_code, symbol, current_start, current_end, sources))
                current_start = start
                current_end = end

        if current_start is not None:
            merged_rows.append(_row(index_code, symbol, current_start, current_end, sources))

    return pd.DataFrame(merged_rows, columns=MEMBERSHIP_COLUMNS)


def upsert_index_member_history(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Persist membership history rows into DuckDB."""
    if df.empty:
        return 0
    rows = merge_membership_ranges(df)
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_index_member_history AS SELECT * FROM rows")
    conn.execute("""
        INSERT OR REPLACE INTO index_member_history (
            index_code, symbol, start_date, end_date, source, updated_at
        )
        SELECT index_code, symbol, start_date, end_date, source, CURRENT_TIMESTAMP
        FROM _tmp_index_member_history
    """)
    return len(rows)


def load_index_member_history(
    conn: duckdb.DuckDBPyConnection,
    index_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Load membership history, optionally filtered by index code."""
    params: list[object] = []
    where = ""
    if index_codes is not None:
        codes = [str(code) for code in index_codes]
        if not codes:
            return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
        placeholders = ",".join(["?"] * len(codes))
        where = f"WHERE index_code IN ({placeholders})"
        params.extend(codes)

    df = conn.execute(f"""
        SELECT index_code, symbol, start_date, end_date, source
        FROM index_member_history
        {where}
        ORDER BY index_code, symbol, start_date
    """, params).fetchdf()
    return _normalize_frame(df) if not df.empty else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)


def active_members(df: pd.DataFrame, index_code: str, as_of: date) -> set[str]:
    """Return symbols active for an index on a given date."""
    if df.empty:
        return set()
    clean = _normalize_frame(df)
    query_date = _as_date(as_of)
    sub = clean[
        (clean["index_code"] == str(index_code))
        & (clean["start_date"] <= query_date)
        & (clean["end_date"].isna() | (clean["end_date"] >= query_date))
    ]
    return set(sub["symbol"].astype(str))


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    clean = df.copy()
    for col in MEMBERSHIP_COLUMNS:
        if col not in clean.columns:
            clean[col] = None
    clean = clean[MEMBERSHIP_COLUMNS]
    clean["index_code"] = clean["index_code"].astype(str)
    clean["symbol"] = clean["symbol"].astype(str)
    clean["start_date"] = pd.to_datetime(clean["start_date"]).dt.date
    clean["end_date"] = pd.to_datetime(clean["end_date"], errors="coerce").dt.date
    return clean.sort_values(["index_code", "symbol", "start_date"]).reset_index(drop=True)


def _pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for column in candidates:
        if column in df.columns:
            return column
    lowered = {str(col).lower(): col for col in df.columns}
    for column in candidates:
        found = lowered.get(str(column).lower())
        if found is not None:
            return found
    return None


def _normalize_symbol(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def _source_for_symbol(active_df: pd.DataFrame, symbol: str) -> str | None:
    if active_df.empty:
        return None
    rows = active_df[active_df["symbol"].astype(str) == str(symbol)]
    if rows.empty:
        return None
    value = rows.iloc[0].get("source")
    return str(value) if pd.notna(value) and str(value) else None


def _append_source(existing: str | None, source: str) -> str:
    if not existing:
        return source
    parts = [part for part in str(existing).split(",") if part]
    if source not in parts:
        parts.append(source)
    return ",".join(parts)


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return pd.to_datetime(value).date()


def _ranges_touch(current_end: date | None, next_start: date) -> bool:
    if current_end is None:
        return True
    return next_start <= current_end + timedelta(days=1)


def _max_open_end(left: date | None, right: date | None) -> date | None:
    if left is None or right is None:
        return None
    return max(left, right)


def _row(index_code: str, symbol: str, start: date, end: date | None, sources: set[str]) -> dict:
    return {
        "index_code": index_code,
        "symbol": symbol,
        "start_date": start,
        "end_date": end,
        "source": ",".join(sorted(sources)) if sources else None,
    }
