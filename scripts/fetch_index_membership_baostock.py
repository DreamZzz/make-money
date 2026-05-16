#!/usr/bin/env python3
"""Fetch free Baostock index membership snapshots and emit interval archives."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

MEMBERSHIP_COLUMNS = ["index_code", "symbol", "start_date", "end_date", "source"]
SNAPSHOT_COLUMNS = ["index_code", "symbol", "snapshot_date", "source"]
DEFAULT_INDEXES = ("000300", "000905")
INDEX_FETCHERS = {
    "000300": "query_hs300_stocks",
    "000905": "query_zz500_stocks",
}
DEFAULT_SOURCE = "baostock_monthly_snapshot"
DEFAULT_OUTPUT = Path("data/index_membership/baostock_csi_history.csv")


def build_monthly_snapshot_dates(start_date: str | date, end_date: str | date) -> list[date]:
    """Return month-end snapshot dates plus the exact final date when needed."""
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if start > end:
        raise ValueError("start_date must be <= end_date")

    month_ends = [ts.date() for ts in pd.date_range(start=start, end=end, freq="ME")]
    if not month_ends or month_ends[-1] != end:
        month_ends.append(end)
    return month_ends


def baostock_result_to_frame(result: Any) -> pd.DataFrame:
    """Convert a Baostock ResultData object to a DataFrame."""
    if getattr(result, "error_code", "0") != "0":
        raise RuntimeError(f"Baostock query failed: {getattr(result, 'error_msg', '')}")
    if hasattr(result, "get_data"):
        data = result.get_data()
        return data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)

    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=list(getattr(result, "fields", [])))


def normalize_baostock_snapshot(
    index_code: str,
    snapshot_date: str | date,
    raw: pd.DataFrame,
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Normalize a Baostock constituent snapshot to index/symbol/date rows."""
    if raw.empty:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    code_col = _pick_column(raw, ("code", "成分券代码", "证券代码", "symbol"))
    if code_col is None:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)

    out = pd.DataFrame({
        "index_code": str(index_code).zfill(6),
        "symbol": raw[code_col].map(_normalize_symbol),
        "snapshot_date": pd.Timestamp(snapshot_date).date(),
        "source": source,
    })
    out = out.dropna(subset=["symbol"])
    return out[SNAPSHOT_COLUMNS].drop_duplicates().reset_index(drop=True)


def snapshots_to_membership_ranges(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Convert point-in-time monthly snapshots into membership intervals."""
    if snapshots.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)

    df = snapshots.copy()
    df["index_code"] = df["index_code"].astype(str).str.zfill(6)
    df["symbol"] = df["symbol"].map(_normalize_symbol)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["source"] = df.get("source", DEFAULT_SOURCE).fillna(DEFAULT_SOURCE).astype(str)
    df = df.dropna(subset=["index_code", "symbol", "snapshot_date"]).drop_duplicates()

    rows: list[dict[str, Any]] = []
    for index_code, index_df in df.groupby("index_code", sort=True):
        dates = sorted(index_df["snapshot_date"].unique())
        symbols = sorted(index_df["symbol"].unique())
        present_by_symbol = {
            symbol: set(index_df.loc[index_df["symbol"] == symbol, "snapshot_date"])
            for symbol in symbols
        }
        sources_by_symbol = {
            symbol: ",".join(sorted(set(index_df.loc[index_df["symbol"] == symbol, "source"].astype(str))))
            for symbol in symbols
        }

        for symbol in symbols:
            current_start: date | None = None
            for snapshot_date in dates:
                present = snapshot_date in present_by_symbol[symbol]
                if present and current_start is None:
                    current_start = snapshot_date
                elif not present and current_start is not None:
                    rows.append({
                        "index_code": index_code,
                        "symbol": symbol,
                        "start_date": current_start,
                        "end_date": snapshot_date - timedelta(days=1),
                        "source": sources_by_symbol[symbol],
                    })
                    current_start = None
            if current_start is not None:
                rows.append({
                    "index_code": index_code,
                    "symbol": symbol,
                    "start_date": current_start,
                    "end_date": None,
                    "source": sources_by_symbol[symbol],
                })

    return pd.DataFrame(rows, columns=MEMBERSHIP_COLUMNS).sort_values(
        ["index_code", "symbol", "start_date"],
    ).reset_index(drop=True)


def fetch_index_membership_snapshots(
    baostock_client: Any,
    indexes: list[str],
    snapshot_dates: list[date],
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Fetch Baostock snapshots for requested indexes and dates."""
    frames = []
    for index_code in indexes:
        normalized_index = str(index_code).zfill(6)
        fetcher_name = INDEX_FETCHERS.get(normalized_index)
        if fetcher_name is None:
            raise ValueError(f"Unsupported index for Baostock fetcher: {index_code}")
        fetcher = getattr(baostock_client, fetcher_name)
        for snapshot_date in snapshot_dates:
            result = fetcher(date=snapshot_date.isoformat())
            raw = baostock_result_to_frame(result)
            normalized = normalize_baostock_snapshot(normalized_index, snapshot_date, raw, source=source)
            if not normalized.empty:
                frames.append(normalized)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=SNAPSHOT_COLUMNS)


def fetch_membership_ranges(
    baostock_client: Any,
    start_date: str | date,
    end_date: str | date,
    indexes: list[str] | None = None,
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Fetch monthly Baostock snapshots and convert them into importable ranges."""
    snapshot_dates = build_monthly_snapshot_dates(start_date, end_date)
    snapshots = fetch_index_membership_snapshots(
        baostock_client,
        indexes=indexes or list(DEFAULT_INDEXES),
        snapshot_dates=snapshot_dates,
        source=source,
    )
    return snapshots_to_membership_ranges(snapshots)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch free Baostock CSI300/CSI500 membership interval archives.")
    parser.add_argument("--start", default="2020-01-01", help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date, YYYY-MM-DD")
    parser.add_argument("--indexes", default=",".join(DEFAULT_INDEXES), help="Comma-separated index codes")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source label stored in the CSV")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output CSV path")
    args = parser.parse_args(argv)

    try:
        import baostock as bs  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Baostock is not installed. Install with: pip install baostock") from exc

    indexes = [part.strip().zfill(6) for part in args.indexes.split(",") if part.strip()]
    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise SystemExit(f"Baostock login failed: {getattr(login, 'error_msg', '')}")
    try:
        ranges = fetch_membership_ranges(
            bs,
            start_date=args.start,
            end_date=args.end,
            indexes=indexes,
            source=args.source,
        )
    finally:
        bs.logout()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ranges.to_csv(output, index=False)
    print(f"wrote={len(ranges)} output={output}")
    return 0 if not ranges.empty else 1


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in df.columns:
            return column
    lowered = {str(column).lower(): column for column in df.columns}
    for column in candidates:
        found = lowered.get(column.lower())
        if found is not None:
            return found
    return None


def _normalize_symbol(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "." in text:
        left, right = text.split(".", 1)
        text = right if len(right) == 6 else left
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


if __name__ == "__main__":
    raise SystemExit(main())
