"""
Streamlit 主入口 — 量化评估系统 Dashboard
启动: streamlit run src/dashboard/app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="量化评估系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 页面路由
pages = {
    "行情概览": [
        st.Page("views/market_overview.py", title="市场行情", icon="📈"),
    ],
    "策略研究": [
        st.Page("views/strategy_compare.py", title="策略对比", icon="⚖️"),
        st.Page("views/qlib_analysis.py", title="Qlib分析", icon="🧠"),
    ],
    "信号 & 调仓": [
        st.Page("views/weekly_report.py", title="本周建议", icon="📋"),
        st.Page("views/signals.py", title="调仓信号", icon="🎯"),
        st.Page("views/portfolio.py", title="组合监控", icon="💼"),
    ],
    "指数基金": [
        st.Page("views/index_funds.py", title="指数基金", icon="🧭"),
    ],
}

pg = st.navigation(pages)
pg.run()
