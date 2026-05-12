"""调仓信号页面 — 查看最新交易信号"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import streamlit as st

from src.dashboard.db import query_df, db_error_widget, DuckDBError




def show_signals():
    st.subheader("最新调仓信号")

    df = query_df("""
        SELECT signal_ts, model_name, symbol, side, score, confidence,
               horizon, max_position_pct, COALESCE(status, 'ACTIVE') AS status,
               status_reason, thesis
        FROM signals
        ORDER BY signal_ts DESC, confidence DESC
        LIMIT 100
    """)

    if df.empty:
        st.info("暂无调仓信号。回测验证后运行 `python -m src.signals.generator` 生成信号。")
        _show_signal_format_info()
        return

    # 颜色标记买卖
    def color_side(val):
        if val == "BUY":
            return "color: green; font-weight: bold"
        elif val == "SELL":
            return "color: red; font-weight: bold"
        return ""

    styled = (df.style
              .map(color_side, subset=["side"])
              .format({"score": "{:.3f}", "confidence": "{:.2f}", "max_position_pct": "{:.1%}"}))
    st.dataframe(styled, hide_index=True, width="stretch")

    # 下载按钮
    csv = df.to_csv(index=False)
    st.download_button("下载信号 CSV", csv, "signals.csv", "text/csv")


def _show_signal_format_info():
    st.markdown("""
    ### 信号表格式说明

    信号生成后将包含以下字段：

    | 字段 | 说明 | 示例 |
    |------|------|------|
    | signal_ts | 信号生成时间 | 2025-01-03 15:30:00 |
    | model_name | 策略名称 | alpha158 / trend_following |
    | symbol | 股票代码 | 600519.SH / 0700.HK |
    | side | 方向 | BUY / SELL / HOLD |
    | score | 信号强度 | 0.85 |
    | confidence | 置信度 | 0.72 |
    | horizon | 预期持仓周期 | 5d / 20d |
    | max_position_pct | 最大仓位 | 5% |
    | thesis | 信号理由 | 低估值+动量向上 |
    """)


def show_manual_order_entry():
    """手动订单录入（暂不可用：Dashboard 为只读模式）"""
    st.subheader("手动订单录入")
    st.info("Dashboard 当前以只读模式运行，写操作请使用命令行：\n\n"
            "```bash\npython3 -m src.portfolio.paper_engine\n```")


st.title("🎯 调仓信号")
try:
    show_signals()
except DuckDBError as e:
    db_error_widget(e)
st.divider()
show_manual_order_entry()
