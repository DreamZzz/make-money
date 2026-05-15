"""组合监控页面 — 纸交易持仓、净值曲线、风险指标"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import DuckDBError, db_error_widget, query_df


def show_cash_account():
    """全局资金账户：出入金、现金和净投入。"""
    st.subheader("资金账户")

    from src.portfolio.cashbook import add_cashflow, get_account_summary, load_cashflows
    from src.portfolio.nav_calculator import calculate_all_strategies

    summary = get_account_summary()
    cashflows = load_cashflows()
    investment_pnl = float(summary.get("total_value") or 0) - float(summary.get("net_contribution") or 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("可用现金", f"{float(summary.get('cash') or 0):,.0f}")
    c2.metric("持仓市值", f"{float(summary.get('position_value') or 0):,.0f}")
    c3.metric("总资产", f"{float(summary.get('total_value') or 0):,.0f}")
    c4.metric("净投入本金", f"{float(summary.get('net_contribution') or 0):,.0f}")
    c5.metric("投资盈亏", f"{investment_pnl:,.0f}")

    with st.expander("出入金", expanded=False):
        with st.form("cashflow_form", clear_on_submit=True):
            flow_date = st.date_input("日期", value=date.today())
            flow_label = st.selectbox("类型", ["入金", "出金"], index=0)
            amount = st.number_input("金额", min_value=0.0, step=1000.0, format="%.2f")
            note = st.text_input("备注", value="")
            submitted = st.form_submit_button("记录并重算净值", type="primary")
            if submitted:
                if amount <= 0:
                    st.error("金额必须大于 0")
                else:
                    flow_type = "DEPOSIT" if flow_label == "入金" else "WITHDRAW"
                    add_cashflow(flow_date=flow_date, flow_type=flow_type, amount=amount, note=note)
                    calculate_all_strategies()
                    st.cache_data.clear()
                    st.success("资金流水已记录，净值已重算。")
                    st.rerun()

    if not cashflows.empty:
        display = cashflows.tail(20).sort_values(["flow_date", "created_at"], ascending=False).copy()
        display["类型"] = display["flow_type"].map({"DEPOSIT": "入金", "WITHDRAW": "出金"})
        st.dataframe(
            display[["flow_date", "类型", "amount", "currency", "note", "created_at"]],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("暂无资金流水")


def show_portfolio_summary():
    """组合概览卡片"""
    st.subheader("组合概览")

    nav = query_df("""
        SELECT strategy_name, total_value, nav, daily_return, drawdown
        FROM portfolio_nav
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM portfolio_nav GROUP BY strategy_name
        )
    """)

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
    nav = query_df("""
        SELECT strategy_name, trade_date, nav, investment_nav, total_value, net_contribution, drawdown
        FROM portfolio_nav
        ORDER BY strategy_name, trade_date
    """)
    if nav.empty:
        st.info("暂无数据")
        return

    nav["trade_date"] = pd.to_datetime(nav["trade_date"])

    tab_nav, tab_dd = st.tabs(["净值", "回撤"])
    with tab_nav:
        fig = go.Figure()
        for s in nav["strategy_name"].unique():
            sub = nav[nav["strategy_name"] == s]
            y = sub["investment_nav"] if "investment_nav" in sub.columns else sub["nav"]
            fig.add_trace(go.Scatter(x=sub["trade_date"], y=y, mode="lines", name=s))
        fig.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="基准线")
        fig.update_layout(height=380, xaxis_tickformat="%m月%d日", yaxis_title="现金流校正净值")
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
    pos = query_df("""
        SELECT strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
               market_value, pnl, pnl_pct, weight
        FROM paper_positions
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM paper_positions GROUP BY strategy_name
        )
        AND quantity > 0
        ORDER BY strategy_name, weight DESC
    """)

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
    orders = query_df("""
        SELECT
            po.order_ts,
            po.symbol,
            COALESCE(si.name, po.symbol) AS stock_name,
            COALESCE(si.industry, si.sector, '') AS industry,
            po.side,
            po.order_qty,
            po.order_price,
            COALESCE(po.order_value, po.order_qty * po.order_price) AS order_value,
            po.fee,
            po.cash_before,
            po.cash_after,
            po.status,
            po.status_reason,
            s.model_name,
            s.confidence,
            s.score,
            s.thesis
        FROM paper_orders po
        LEFT JOIN stock_info si ON po.symbol = si.symbol
        LEFT JOIN signals s ON po.signal_id = s.signal_id
        ORDER BY po.order_ts DESC, po.created_at DESC
        LIMIT 30
    """)
    if orders.empty:
        st.info("暂无交易记录")
        return
    orders = orders.copy()
    orders["order_ts"] = pd.to_datetime(orders["order_ts"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    orders["状态"] = orders["status"].map({
        "FILLED": "已成交",
        "PENDING": "待成交",
        "CANCELLED": "已取消",
        "FAILED": "失败",
    }).fillna(orders["status"])
    orders["方向"] = orders["side"].map({"BUY": "买入", "SELL": "卖出", "SHORT": "卖出"}).fillna(orders["side"])
    display = orders.rename(columns={
        "order_ts": "成交时间",
        "symbol": "代码",
        "stock_name": "名称",
        "industry": "行业",
        "model_name": "策略",
        "order_qty": "数量",
        "order_price": "成交价",
        "order_value": "成交额",
        "fee": "费用",
        "cash_before": "成交前现金",
        "cash_after": "成交后现金",
        "confidence": "置信度",
        "score": "信号分",
        "thesis": "信号说明",
        "status_reason": "说明",
    })
    st.dataframe(
        display[[
            "成交时间", "状态", "说明", "代码", "名称", "行业", "策略", "方向",
            "数量", "成交价", "成交额", "费用", "成交前现金", "成交后现金",
            "置信度", "信号分", "信号说明",
        ]],
        hide_index=True,
        width="stretch",
        column_config={
            "成交价": st.column_config.NumberColumn(format="%.2f"),
            "成交额": st.column_config.NumberColumn(format="%.2f"),
            "费用": st.column_config.NumberColumn(format="%.2f"),
            "成交前现金": st.column_config.NumberColumn(format="%.2f"),
            "成交后现金": st.column_config.NumberColumn(format="%.2f"),
            "置信度": st.column_config.NumberColumn(format="%.2f"),
            "信号分": st.column_config.NumberColumn(format="%.2f"),
        },
    )


# ----
st.title("💼 组合监控")
try:
    show_cash_account()
    st.divider()
    show_portfolio_summary()
    st.divider()
    show_nav_curve()
    st.divider()
    show_holdings()
    st.divider()
    show_orders()
except DuckDBError as e:
    db_error_widget(e)
