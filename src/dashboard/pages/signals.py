"""调仓信号页面 — 查看最新交易信号"""
import sys
sys.path.insert(0, "/Users/zhaoqiang/Documents/Project/make-money")

import duckdb
import pandas as pd
import streamlit as st

from src.dashboard.db import get_conn




def show_signals():
    st.subheader("最新调仓信号")

    df = get_conn().execute("""
        SELECT signal_ts, model_name, symbol, side, score, confidence,
               horizon, max_position_pct, thesis
        FROM signals
        ORDER BY signal_ts DESC, confidence DESC
        LIMIT 100
    """).fetchdf()

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
    """手动订单录入（记录实际下单）"""
    st.subheader("手动订单录入")

    with st.form("manual_order"):
        col1, col2, col3 = st.columns(3)
        with col1:
            symbol = st.text_input("股票代码", placeholder="600519.SH")
        with col2:
            side = st.selectbox("方向", ["BUY", "SELL"])
        with col3:
            qty = st.number_input("数量（股）", min_value=0, step=100)

        price = st.number_input("成交价", min_value=0.0, step=0.01)
        note = st.text_input("备注")

        if st.form_submit_button("记录订单"):
            import uuid
            from datetime import datetime
            conn.execute("""
                INSERT INTO paper_orders (order_id, signal_id, symbol, side, order_qty, order_price, order_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [str(uuid.uuid4())[:8], None, symbol, side, qty, price, datetime.now()])
            conn.close()
            st.success(f"订单已记录: {side} {symbol} {qty}股 @ {price}")


st.title("🎯 调仓信号")
show_signals()
st.divider()
show_manual_order_entry()
