"""Dashboard 共享 DuckDB 连接管理。全部使用默认 read_write 配置，避免连接模式冲突。"""
import duckdb
import streamlit as st

from src.data_pipeline.loader import get_db_path

_CACHE_KEY = "_db_conn"


def get_conn():
    """获取连接（统一使用默认配置，不做 read_only 限制）"""
    if _CACHE_KEY not in st.session_state or st.session_state[_CACHE_KEY] is None:
        st.session_state[_CACHE_KEY] = duckdb.connect(get_db_path())
    return st.session_state[_CACHE_KEY]
