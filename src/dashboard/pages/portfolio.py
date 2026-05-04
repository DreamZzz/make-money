"""组合监控页面 — 纸交易持仓、净值曲线、风险指标"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import get_conn


def show_portfolio_summary():
    """组合概览卡片"""
    st.subheader("组合概览")

    nav = get_conn().execute("""
        SELECT strategy_name, total_value, nav, daily_return, drawdown
        FROM portfolio_nav
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM portfolio_nav GROUP BY strategy_name
        )
    """).fetchdf()

    if nav.empty:
        st.info("暂无纸交易数据。运行 `python -m src.portfolio.paper_engine && python -m src.portfolio.nav_calculator` 后可见。")
        return

    cols = st.columns(min(len(nav), 4))
    for i, (_, row) in enumerate(nav.iterrows()):
        with cols[i]:
            ret = (row["nav"] - 1) * 100
            st.metric(
                row["strategy_name"],
                f"{row['total_value']:,.0f}",
                f"{ret:+.1f}%",
            )


def show_nav_curve():
    st.subheader("净值曲线")
    nav = get_conn().execute("""
        SELECT strategy_name, trade_date, nav, drawdown FROM portfolio_nav
        ORDER BY strategy_name, trade_date
    """).fetchdf()
    if nav.empty:
        st.info("暂无数据")
        return

    nav["trade_date"] = pd.to_datetime(nav["trade_date"])

    tab_nav, tab_dd = st.tabs(["净值", "回撤"])
    with tab_nav:
        fig = go.Figure()
        for s in nav["strategy_name"].unique():
            sub = nav[nav["strategy_name"] == s]
            fig.add_trace(go.Scatter(x=sub["trade_date"], y=sub["nav"], mode="lines", name=s))
        fig.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="基准线")
        fig.update_layout(height=380, xaxis_tickformat="%m月%d日", yaxis_title="净值")
        st.plotly_chart(fig, width="stretch")

    with tab_dd:
        fig2 = go.Figure()
        for s in nav["strategy_name"].unique():
            sub = nav[nav["strategy_name"] == s]
            fig2.add_trace(go.Scatter(x=sub["trade_date"], y=sub["drawdown"]*100, mode="lines", name=s))
        fig2.update_layout(height=300, yaxis_title="回撤 (%)", xaxis_tickformat="%m月%d日")
        st.plotly_chart(fig2, width="stretch")


def show_holdings():
    st.subheader("当前持仓")
    pos = get_conn().execute("""
        SELECT strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
               market_value, pnl, pnl_pct, weight
        FROM paper_positions
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM paper_positions GROUP BY strategy_name
        )
        AND quantity > 0
        ORDER BY strategy_name, weight DESC
    """).fetchdf()

    if pos.empty:
        st.info("暂无持仓")
        return

    # 资产配置饼图
    if len(pos) > 1:
        fig = px.pie(pos, values="market_value", names="symbol", title="持仓分布")
        fig.update_layout(height=350)
        st.plotly_chart(fig, width="stretch")

    for s in pos["strategy_name"].unique():
        sub = pos[pos["strategy_name"] == s]
        with st.expander(f"{s} — {len(sub)} 只, 总市值 {sub['market_value'].sum():,.0f}"):
            sub = sub.copy()
            sub["avg_cost"] = sub["avg_cost"].round(2)
            sub["market_value"] = sub["market_value"].round(0)
            sub["pnl_pct"] = (sub["pnl_pct"] * 100).round(1).astype(str) + "%"
            sub["weight"] = (sub["weight"] * 100).round(1).astype(str) + "%" if "weight" in sub.columns else ""
            st.dataframe(
                sub[["symbol", "quantity", "avg_cost", "current_price", "market_value", "pnl_pct"]],
                hide_index=True, width="stretch",
            )


def show_orders():
    st.subheader("最近交易记录")
    orders = get_conn().execute("""
        SELECT order_ts, symbol, side, order_qty, order_price, status
        FROM paper_orders ORDER BY order_ts DESC LIMIT 30
    """).fetchdf()
    if orders.empty:
        st.info("暂无交易记录")
        return
    st.dataframe(orders, hide_index=True, width="stretch")


# ----
st.title("💼 组合监控")
show_portfolio_summary()
st.divider()
show_nav_curve()
st.divider()
show_holdings()
st.divider()
show_orders()
