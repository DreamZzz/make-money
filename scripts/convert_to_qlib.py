"""
将 DuckDB 中的日线数据转换为 Qlib 二进制格式。
用法: python scripts/convert_to_qlib.py [--market cn|hk|all]

Qlib 二进制格式要求通过 qlib.run.dump_bin 工具生成，
本脚本先导出 CSV，再调 dump_bin 完成转换。
"""
import subprocess
import sys
from pathlib import Path

import click
import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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

    inst_rows = []
    for symbol, sub in df.groupby("symbol"):
        dates = pd.to_datetime(sub["date"])
        inst_rows.append((symbol, dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")))
    inst = pd.DataFrame(inst_rows, columns=["symbol", "start", "end"]).sort_values("symbol")
    for name in ("all", "csi300", "csi500"):
        inst.to_csv(instrument_dir / f"{name}.txt", sep="\t", index=False, header=False)

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
        f"Manual Qlib data ready: calendar={len(calendar)}, instruments={len(inst)}, "
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
    if ok:
        logger.info(f"Qlib {market} data ready at {target_dir}")
    else:
        logger.warning("Packaged Qlib dump_bin entrypoint is unavailable; using local file-storage writer.")
        _manual_dump_bin(df, target_dir, include_fields)


if __name__ == "__main__":
    main()
