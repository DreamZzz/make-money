"""Dashboard DuckDB 连接管理：按需开启只读连接，查完即关，不持久占锁。

设计原则：Streamlit 是纯读端，所有写操作由 CLI 脚本独立进程完成。
短生命周期只读连接确保回测/更新脚本随时可获取写锁，互不阻塞。
"""
import re
import time
from collections.abc import Sequence

import duckdb
import pandas as pd
import streamlit as st

from src.data_pipeline.loader import get_db_path

# 供各页面直接 catch，无需额外 import duckdb
DuckDBError = duckdb.IOException


def get_conn() -> duckdb.DuckDBPyConnection:
    """返回新的只读连接，遇到短暂写锁时自动重试（最多等 6 秒）。

    DuckDB 规则：写锁存在时所有连接（含只读）均被阻塞。
    - 数据更新、信号生成等短操作（秒级）：重试后自动恢复。
    - 回测等长操作（分钟级）：6 秒后仍失败，页面显示友好提示。
    """
    db_path = get_db_path()
    for attempt in range(4):
        try:
            return duckdb.connect(db_path, read_only=True)
        except duckdb.IOException as e:
            if "Conflicting lock" in str(e) and attempt < 3:
                time.sleep(2)
            else:
                raise


def query_df(sql: str, params: Sequence | None = None) -> pd.DataFrame:
    """执行只读查询并立即关闭连接，避免 Streamlit 长时间持有 DuckDB 连接。"""
    conn = get_conn()
    try:
        return conn.execute(sql, params or []).fetchdf()
    finally:
        conn.close()


def query_one(sql: str, params: Sequence | None = None):
    """执行只读标量/单行查询并立即关闭连接。"""
    conn = get_conn()
    try:
        return conn.execute(sql, params or []).fetchone()
    finally:
        conn.close()


def db_error_widget(exc: Exception) -> None:
    """渲染友好的数据库错误提示，然后终止当前页面渲染。

    用法：
        try:
            show_xxx()
        except DuckDBError as e:
            db_error_widget(e)
    """
    msg = str(exc)
    if "Conflicting lock" in msg:
        pid_match = re.search(r"PID (\d+)", msg)
        if pid_match:
            pid = pid_match.group(1)
            hint = f"占用进程 **PID {pid}**，可在终端运行 `kill {pid}` 强制释放锁。"
        else:
            hint = "回测或数据更新脚本运行期间数据库不可读。"
        st.warning(
            "⏳ **数据库正被另一个进程占用**\n\n"
            f"{hint}\n\n"
            "脚本完成后刷新页面即可恢复，通常需等待 1 ~ 10 分钟。"
        )
        if st.button("🔄 稍后刷新", key="_db_err_refresh"):
            st.rerun()
        st.stop()
    else:
        st.error(f"❌ 数据库连接失败：{exc}")
        st.stop()
