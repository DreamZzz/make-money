"""
将 DuckDB 中的日线数据转换为 Qlib 二进制格式。
用法: python scripts/convert_to_qlib.py [--market cn|hk|all]

Qlib 二进制格式要求通过 qlib.run.dump_bin 工具生成，
本脚本先导出 CSV，再调 dump_bin 完成转换。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

INSTRUMENT_COLUMNS = ["symbol", "start", "end"]
OPEN_END_DATE = "2099-12-31"
CN_INDEX_TO_INSTRUMENT = {"000300": "csi300", "000905": "csi500"}


def _dump_bin(csv_file: Path, target_dir: Path, include_fields: str) -> bool:
    """调用 Qlib dump_bin 将 CSV 转为二进制格式"""
    module_candidates = ["qlib.run.dump_bin", "qlib.scripts.dump_bin"]
    for module in module_candidates:
        cmd = [
            sys.executable, "-m", module, "dump_all",
            "--csv-path", str(csv_file),
            "--qlib-data-dir", str(target_dir),
            "--symbol-field-name", "symbol",
            "--date-field-name", "date",
            "--include-fields", include_fields,
            "--freq", "day",
        ]
        logger.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("dump_bin completed")
            return True
        logger.warning(f"{module} unavailable or failed:\n{result.stderr[-1200:]}")
    return False


def _write_feature_bin(path: Path, start_idx: int, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.hstack([[float(start_idx)], values.astype(np.float32)]).astype("<f").tofile(path)


def build_dynamic_instruments(price_df: pd.DataFrame, membership_df: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Build Qlib instrument ranges from price availability and index membership history."""
    all_rows = _build_price_instrument_rows(price_df)
    membership_df = membership_df if membership_df is not None else pd.DataFrame()
    if membership_df.empty:
        return {name: all_rows.copy() for name in ("all", "csi300", "csi500", "csi800")}

    instruments = {"all": all_rows}
    price_ranges = _price_ranges(all_rows)
    for index_code, instrument_name in CN_INDEX_TO_INSTRUMENT.items():
        instruments[instrument_name] = _build_membership_instrument_rows(membership_df, index_code, price_ranges)

    csi800_membership = membership_df[membership_df["index_code"].astype(str).isin(CN_INDEX_TO_INSTRUMENT)]
    if csi800_membership.empty:
        instruments["csi800"] = _empty_instrument_frame()
    else:
        csi800_membership = csi800_membership.copy()
        csi800_membership["index_code"] = "csi800"
        from src.data_pipeline.index_membership import merge_membership_ranges

        merged = merge_membership_ranges(csi800_membership)
        instruments["csi800"] = _build_membership_instrument_rows(merged, "csi800", price_ranges)
    return instruments


def write_instrument_files(instrument_dir: Path, instruments: dict[str, pd.DataFrame]) -> None:
    instrument_dir.mkdir(parents=True, exist_ok=True)
    for name in ("all", "csi300", "csi500", "csi800"):
        df = instruments.get(name, _empty_instrument_frame()).copy()
        df = df[INSTRUMENT_COLUMNS] if not df.empty else _empty_instrument_frame()
        df.to_csv(instrument_dir / f"{name}.txt", sep="\t", index=False, header=False)


def _build_price_instrument_rows(price_df: pd.DataFrame) -> pd.DataFrame:
    if price_df.empty:
        return _empty_instrument_frame()
    df = price_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)
    rows = []
    for symbol, sub in df.groupby("symbol", sort=True):
        rows.append((symbol, sub["date"].min().strftime("%Y-%m-%d"), sub["date"].max().strftime("%Y-%m-%d")))
    return pd.DataFrame(rows, columns=INSTRUMENT_COLUMNS)


def _build_membership_instrument_rows(
    membership_df: pd.DataFrame,
    index_code: str,
    price_ranges: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> pd.DataFrame:
    if membership_df.empty:
        return _empty_instrument_frame()
    df = membership_df.copy()
    df["index_code"] = df["index_code"].astype(str)
    df = df[df["index_code"] == str(index_code)]
    if df.empty:
        return _empty_instrument_frame()

    rows = []
    for _, row in df.iterrows():
        symbol = str(row["symbol"])
        if symbol not in price_ranges:
            continue
        price_start, price_end = price_ranges[symbol]
        start = pd.to_datetime(row["start_date"])
        end = pd.to_datetime(row["end_date"], errors="coerce")
        if pd.notna(end) and end < price_start:
            continue
        if start > price_end:
            continue
        start = max(start, price_start)
        end_text = OPEN_END_DATE if pd.isna(end) else end.strftime("%Y-%m-%d")
        rows.append((symbol, start.strftime("%Y-%m-%d"), end_text))
    if not rows:
        return _empty_instrument_frame()
    return pd.DataFrame(rows, columns=INSTRUMENT_COLUMNS).sort_values(["symbol", "start", "end"]).reset_index(drop=True)


def _price_ranges(all_rows: pd.DataFrame) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    return {
        str(row["symbol"]): (pd.to_datetime(row["start"]), pd.to_datetime(row["end"]))
        for _, row in all_rows.iterrows()
    }


def _empty_instrument_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=INSTRUMENT_COLUMNS)


def _manual_dump_bin(df: pd.DataFrame, target_dir: Path, include_fields: str) -> None:
    """Write the minimal Qlib file storage format when dump_bin is not packaged."""
    fields = [field.strip().lower() for field in include_fields.split(",") if field.strip()]
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)
    calendar = pd.Index(sorted(df["date"].dropna().unique()))
    calendar_map = {dt: i for i, dt in enumerate(calendar)}

    calendar_dir = target_dir / "calendars"
    instrument_dir = target_dir / "instruments"
    feature_dir = target_dir / "features"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    instrument_dir.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)

    pd.Series(calendar.strftime("%Y-%m-%d")).to_csv(calendar_dir / "day.txt", index=False, header=False)

    instruments = build_dynamic_instruments(df, pd.DataFrame())
    write_instrument_files(instrument_dir, instruments)

    for symbol, sub in df.groupby("symbol"):
        sub = sub.sort_values("date")
        idx = sub["date"].map(calendar_map).astype(int)
        start_idx = int(idx.min())
        full_index = pd.RangeIndex(start_idx, int(idx.max()) + 1)
        for field in fields:
            if field not in sub.columns:
                continue
            values = pd.Series(pd.to_numeric(sub[field], errors="coerce").values, index=idx)
            values = values.groupby(level=0).last().reindex(full_index).astype("float32").values
            _write_feature_bin(feature_dir / symbol.lower() / f"{field}.day.bin", start_idx, values)

    logger.info(
        f"Manual Qlib data ready: calendar={len(calendar)}, instruments={len(instruments['all'])}, "
        f"fields={fields}, dir={target_dir}"
    )


@click.command()
@click.option("--market", default="all", type=click.Choice(["cn", "hk", "all"]), help="导出市场")
def main(market: str):
    """将 DuckDB 日线数据转换为 Qlib 二进制格式"""
    if market in ("cn", "all"):
        _convert(market="CN", target_dir=PROJECT_ROOT / "qlib_data" / "cn_data",
                 include_fields="open,high,low,close,volume,factor")
    if market in ("hk", "all"):
        _convert(market="HK", target_dir=PROJECT_ROOT / "qlib_data" / "hk_data",
                 include_fields="open,high,low,close,volume")


def _convert(market: str, target_dir: Path, include_fields: str):
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)

    adj_cols = "adj_close, adj_factor" if market == "CN" else ""
    df = conn.execute(f"""
        SELECT symbol, trade_date AS date,
               open, high, low, close, volume
               {"," + adj_cols if adj_cols else ""}
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country=?)
        ORDER BY symbol, date
    """, [market]).fetchdf()
    membership_df = pd.DataFrame()
    if market == "CN":
        try:
            from src.data_pipeline.index_membership import load_index_member_history

            membership_df = load_index_member_history(conn, CN_INDEX_TO_INSTRUMENT)
        except Exception as exc:
            logger.warning(f"Index membership history unavailable; using broad instruments: {exc}")
    conn.close()

    if df.empty:
        logger.warning(f"No {market} data in DB, skipping")
        return

    # Qlib 要求复权因子列名为 factor
    if "adj_factor" in df.columns:
        df = df.rename(columns={"adj_factor": "factor"})
        df["factor"] = df["factor"].fillna(1.0)
    if "adj_close" in df.columns:
        df = df.drop(columns=["adj_close"])

    # 导出临时 CSV
    tmp_dir = PROJECT_ROOT / "qlib_data" / f"_tmp_csv_{market.lower()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    csv_file = tmp_dir / f"{market.lower()}_daily.csv"
    df.to_csv(csv_file, index=False)
    logger.info(f"Exported {len(df):,} rows → {csv_file}")

    target_dir.mkdir(parents=True, exist_ok=True)
    ok = _dump_bin(csv_file, target_dir, include_fields)
    instruments = build_dynamic_instruments(df, membership_df)
    if ok:
        write_instrument_files(target_dir / "instruments", instruments)
        logger.info(f"Qlib {market} data ready at {target_dir}")
    else:
        logger.warning("Packaged Qlib dump_bin entrypoint is unavailable; using local file-storage writer.")
        _manual_dump_bin(df, target_dir, include_fields)
        write_instrument_files(target_dir / "instruments", instruments)


if __name__ == "__main__":
    main()
