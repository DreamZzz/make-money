"""
将 DuckDB 中的日线数据转换为 Qlib 二进制格式。
用法: python scripts/convert_to_qlib.py [--market cn|hk|all]
"""
import sys
sys.path.insert(0, "/Users/zhaoqiang/Documents/Project/make-money")

import click
import pandas as pd
from loguru import logger


@click.command()
@click.option("--market", default="all", help="cn / hk / all")
def main(market: str):
    # 确保 qlib 数据目录存在
    base = Path(__file__).parent.parent
    cn_path = base / "qlib_data" / "cn_data"
    hk_path = base / "qlib_data" / "hk_data"

    if market in ("cn", "all"):
        convert_cn(base, cn_path)

    if market in ("hk", "all"):
        convert_hk(base, hk_path)


def convert_cn(base: Path, target_dir: Path):
    """将A股数据导出为 Qlib 格式"""
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 导出日线 CSV
    csv_dir = base / "qlib_data" / "_tmp_csv_cn"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / "cn_daily.csv"

    df = conn.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume, adj_close, adj_factor
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
        ORDER BY symbol, trade_date
    """).fetchdf()

    if df.empty:
        conn.close()
        logger.warning("No CN data found")
        return

    # Qlib 要求列名: symbol, date, open, high, low, close, volume, adjclose, factor
    df = df.rename(columns={"trade_date": "date", "adj_close": "adjclose", "adj_factor": "factor"})
    df.to_csv(csv_file, index=False)
    logger.info(f"Exported {len(df)} CN rows to {csv_file}")

    # 使用 Qlib dump_bin 转换
    try:
        import qlib
        qlib.init()
        from scripts.dump_bin import DumpData
        # 使用 Qlib 内置工具
        logger.info("Please use: qlib dump_bin --csv-path qlib_data/_tmp_csv_cn/cn_daily.csv --qlib-data-dir qlib_data/cn_data --symbol-field-name symbol --date-field-name date --include-fields open,high,low,close,volume,adjclose,factor")
    except Exception as e:
        logger.warning(f"Qlib not initialized, use manual command: {e}")

    conn.close()


def convert_hk(base: Path, target_dir: Path):
    """将港股数据导出为 Qlib 格式"""
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    csv_dir = base / "qlib_data" / "_tmp_csv_hk"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / "hk_daily.csv"

    df = conn.execute("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')
        ORDER BY symbol, trade_date
    """).fetchdf()

    if df.empty:
        conn.close()
        logger.warning("No HK data found")
        return

    df = df.rename(columns={"trade_date": "date"})
    df.to_csv(csv_file, index=False)
    logger.info(f"Exported {len(df)} HK rows to {csv_file}")

    conn.close()


if __name__ == "__main__":
    main()
