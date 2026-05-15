"""Historical index membership archive import helpers."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from src.data_pipeline.index_membership import MEMBERSHIP_COLUMNS, merge_membership_ranges, upsert_index_member_history

INDEX_COLUMNS = ("index_code", "指数代码", "指数", "index")
SYMBOL_COLUMNS = ("symbol", "成分券代码", "证券代码", "品种代码", "code")
START_COLUMNS = ("start_date", "纳入日期", "生效日期", "加入日期", "entry_date")
END_COLUMNS = ("end_date", "剔除日期", "调出日期", "移除日期", "exit_date")
SOURCE_COLUMNS = ("source", "来源")


def normalize_membership_archive(raw: pd.DataFrame, source: str = "manual_archive") -> pd.DataFrame:
    """Normalize manually curated or official archive interval rows."""
    if raw.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    index_col = _pick_column(raw, INDEX_COLUMNS)
    symbol_col = _pick_column(raw, SYMBOL_COLUMNS)
    start_col = _pick_column(raw, START_COLUMNS)
    if index_col is None or symbol_col is None or start_col is None:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)

    end_col = _pick_column(raw, END_COLUMNS)
    source_col = _pick_column(raw, SOURCE_COLUMNS)
    out = pd.DataFrame({
        "index_code": raw[index_col].map(_normalize_index_code),
        "symbol": raw[symbol_col].map(_normalize_symbol),
        "start_date": pd.to_datetime(raw[start_col], errors="coerce").dt.date,
        "end_date": pd.to_datetime(raw[end_col], errors="coerce").dt.date if end_col else None,
        "source": raw[source_col].fillna(source).astype(str) if source_col else source,
    })
    out = out.dropna(subset=["index_code", "symbol", "start_date"])
    return merge_membership_ranges(out) if not out.empty else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)


def import_membership_archive(
    conn: duckdb.DuckDBPyConnection,
    paths: Iterable[str | Path],
    source: str = "manual_archive",
) -> dict[str, int]:
    """Import one or more CSV/XLS/XLSX membership interval archives."""
    frames: list[pd.DataFrame] = []
    files = 0
    input_rows = 0
    for path in paths:
        p = Path(path)
        raw = _read_archive_file(p)
        files += 1
        input_rows += len(raw)
        normalized = normalize_membership_archive(raw, source=source)
        if not normalized.empty:
            frames.append(normalized)
    merged = merge_membership_ranges(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    written = upsert_index_member_history(conn, merged) if not merged.empty else 0
    return {"files": files, "input_rows": int(input_rows), "written_rows": int(written)}


def build_membership_coverage_report(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
) -> pd.DataFrame:
    """Summarize membership history coverage and active member counts."""
    as_of = as_of or date.today()
    return conn.execute("""
        SELECT
            index_code,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT symbol) AS total_symbols,
            MIN(start_date) AS earliest_start,
            MAX(COALESCE(end_date, DATE '2099-12-31')) AS latest_end,
            SUM(CASE WHEN start_date <= ? AND (end_date IS NULL OR end_date >= ?) THEN 1 ELSE 0 END) AS active_members,
            SUM(CASE WHEN end_date IS NULL THEN 1 ELSE 0 END) AS open_rows
        FROM index_member_history
        GROUP BY index_code
        ORDER BY index_code
    """, [as_of, as_of]).fetchdf()


def _read_archive_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(path)
    return pd.read_csv(path)


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


def _normalize_index_code(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _normalize_symbol(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text
