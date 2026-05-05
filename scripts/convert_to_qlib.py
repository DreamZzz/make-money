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
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _dump_bin(csv_file: Path, target_dir: Path, include_fields: str) -> bool:
    """调用 Qlib dump_bin 将 CSV 转为二进制格式"""
    cmd = [
        sys.executable, "-m", "qlib.run.dump_bin", "dump_all",
        "--csv-path", str(csv_file),
        "--qlib-data-dir", str(target_dir),
        "--symbol-field-name", "symbol",
        "--date-field-name", "date",
        "--include-fields", include_fields,
        "--freq", "day",
    ]
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"dump_bin failed:\n{result.stderr[-2000:]}")
        return False
    logger.info("dump_bin completed")
    return True


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
        logger.warning(
            f"dump_bin failed. Manual command:\n"
            f"  python -m qlib.run.dump_bin dump_all \\\n"
            f"    --csv-path {csv_file} \\\n"
            f"    --qlib-data-dir {target_dir} \\\n"
            f"    --symbol-field-name symbol --date-field-name date \\\n"
            f"    --include-fields {include_fields} --freq day"
        )


if __name__ == "__main__":
    main()
