"""
DuckDB 数据入库模块。
支持全量导入和增量更新，自动建表。
"""
import os
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
from loguru import logger

from src.config import PROJECT_ROOT, load_config


def get_config() -> dict:
    return load_config()


def get_db_path() -> str:
    config = get_config()
    db_rel = config["data"]["duckdb_path"]
    return str(PROJECT_ROOT / db_rel)


def init_db(conn: duckdb.DuckDBPyConnection) -> None:
    """执行 schema.sql 建表，并为已存在的旧库补充缺失字段"""
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path) as f:
        sql = f.read()
    conn.execute(sql)
    # 为旧库补充 signals 表的纸交易字段（新库由 schema.sql 覆盖，此处幂等）
    for col_ddl in [
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS execution_price DOUBLE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS execution_date DATE",
    ]:
        try:
            conn.execute(col_ddl)
        except Exception:
            pass
    logger.info("Database tables initialized")


def upsert_stock_info(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入股票基本信息（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["symbol", "country", "name"]
    existing = [c for c in columns if c in df.columns]
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_stock_info AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO stock_info ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_stock_info
    """)
    return len(df)


def upsert_daily_price(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入日线行情（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["symbol", "trade_date", "open", "high", "low", "close",
               "volume", "amount", "turnover_rate"]
    existing = [c for c in columns if c in df.columns]
    # 确保 trade_date 是 date 类型
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_daily AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO daily_price ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_daily
    """)
    return len(df)


def upsert_index_daily(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入指数日线"""
    if df.empty:
        return 0
    columns = ["index_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    existing = [c for c in columns if c in df.columns]
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_idx AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO index_daily ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_idx
    """)
    return len(df)


def upsert_financials(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入财务数据"""
    if df.empty:
        return 0
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fin AS SELECT * FROM df")
    conn.execute("""
        INSERT OR REPLACE INTO financials
        SELECT * FROM _tmp_fin
    """)
    return len(df)


def get_last_trade_date(conn: duckdb.DuckDBPyConnection, symbol: str) -> Optional[date]:
    """获取某股票已存储的最新交易日期，用于增量更新"""
    result = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE symbol = ?", [symbol]
    ).fetchone()
    return result[0] if result and result[0] else None


def get_all_symbols(conn: duckdb.DuckDBPyConnection, country: Optional[str] = None) -> list[str]:
    """获取已存储的所有股票代码"""
    if country:
        result = conn.execute(
            "SELECT DISTINCT symbol FROM stock_info WHERE country = ?", [country]
        ).fetchall()
    else:
        result = conn.execute("SELECT DISTINCT symbol FROM stock_info").fetchall()
    return [r[0] for r in result]


def export_to_csv(conn: duckdb.DuckDBPyConnection, table: str, output_path: str) -> str:
    """将表导出为 CSV（用于 Qlib 数据转换等）"""
    df = conn.execute(f"SELECT * FROM {table}").fetchdf()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info(f"Exported {table} → {output_path} ({len(df)} rows)")
    return output_path


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接，自动建表。写模式遇到锁冲突时自动重试。"""
    import time as _time
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    max_retries = 5 if not read_only else 1
    for attempt in range(max_retries):
        try:
            conn = duckdb.connect(db_path, read_only=read_only)
            return conn
        except duckdb.IOException as e:
            if "Conflicting lock" in str(e) and attempt < max_retries - 1:
                logger.warning(f"DB locked, retrying ({attempt + 1}/{max_retries})...")
                _time.sleep(2)
            else:
                raise
    raise RuntimeError("Failed to acquire DuckDB lock")
