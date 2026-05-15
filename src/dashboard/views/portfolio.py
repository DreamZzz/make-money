"""组合监控页面 — 纸交易持仓、净值曲线、风险指标"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.config import load_config
from src.dashboard.db import DuckDBError, db_error_widget, get_conn, query_df
from src.portfolio.exposure_monitor import DEFAULT_BENCHMARK_INDEX, load_exposure_snapshot

ACTION_LABELS = {
    "BUY": "买入",
    "ADD": "加仓",
    "HOLD": "持有",
    "REDUCE": "减仓",
    "PAUSE": "暂停",
}

SLEEVE_LABELS = {
    "core": "Core 指数基金",
    "satellite": "Satellite 个股策略",
}


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


@st.cache_data(ttl=300)
def _load_latest_allocation_plan() -> tuple[pd.DataFrame, pd.DataFrame]:
    plan = query_df("""
        SELECT *
        FROM allocation_plans
        WHERE account_id = 'default'
        ORDER BY plan_date DESC, created_at DESC
        LIMIT 1
    """)
    if plan.empty:
        return plan, pd.DataFrame()
    plan_id = str(plan.iloc[0]["plan_id"])
    items = query_df("""
        SELECT *
        FROM allocation_plan_items
        WHERE plan_id = ?
        ORDER BY priority, sleeve, instrument_id
    """, [plan_id])
    return plan, items


def show_allocation_plan():
    st.subheader("统一资金池")
    plan_df, items = _load_latest_allocation_plan()
    if plan_df.empty:
        st.info("暂无统一资金分配计划。运行 `python3 -m src.portfolio.allocator plan` 后可见。")
        return

    plan = plan_df.iloc[0]
    total_value = float(plan.get("total_value") or 0)
    core_value = float(plan.get("core_value") or 0)
    satellite_value = float(plan.get("satellite_value") or 0)
    core_target_pct = float(plan.get("core_target_pct") or 0)
    satellite_target_pct = float(plan.get("satellite_target_pct") or 0)
    core_pct = core_value / total_value if total_value > 0 else 0
    satellite_pct = satellite_value / total_value if total_value > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Core 当前/目标", f"{core_pct:.1%}", f"目标 {core_target_pct:.0%}")
    c2.metric("Satellite 当前/目标", f"{satellite_pct:.1%}", f"目标 {satellite_target_pct:.0%}")
    c3.metric("Core 可投入", f"{float(plan.get('core_budget') or 0):,.0f}")
    c4.metric("股票 BUY 预算", f"{float(plan.get('satellite_budget') or 0):,.0f}")
    st.caption(f"计划日期：{plan.get('plan_date')} · 状态：{plan.get('status')} · 现金：{float(plan.get('cash') or 0):,.0f}")

    if items.empty:
        return

    sleeve_items = items[items["instrument_type"] == "sleeve"].copy()
    if not sleeve_items.empty:
        sleeve_items["资产篮子"] = sleeve_items["sleeve"].map(SLEEVE_LABELS).fillna(sleeve_items["sleeve"])
        sleeve_items["动作"] = sleeve_items["action"].map(ACTION_LABELS).fillna(sleeve_items["action"])
        sleeve_display = sleeve_items.rename(columns={
            "current_value": "当前市值",
            "target_value": "目标市值",
            "budget_delta": "预算变化",
            "reason": "说明",
        })
        st.dataframe(
            sleeve_display[["资产篮子", "动作", "当前市值", "目标市值", "预算变化", "说明"]],
            hide_index=True,
            width="stretch",
            column_config={
                "当前市值": st.column_config.NumberColumn(format="%.0f"),
                "目标市值": st.column_config.NumberColumn(format="%.0f"),
                "预算变化": st.column_config.NumberColumn(format="%.0f"),
            },
        )

    core_items = items[items["instrument_type"] == "index_fund"].copy()
    if core_items.empty:
        st.caption("暂无基金级 core 执行计划")
        return
    core_items["动作"] = core_items["action"].map(ACTION_LABELS).fillna(core_items["action"])
    core_items["计划金额"] = core_items["budget_delta"]
    core_display = core_items.rename(columns={
        "instrument_id": "基金代码",
        "current_value": "当前市值",
        "target_value": "目标市值",
        "reason": "说明",
    })
    st.markdown("**Core 执行计划**")
    st.dataframe(
        core_display[["基金代码", "动作", "当前市值", "目标市值", "计划金额", "说明"]],
        hide_index=True,
        width="stretch",
        column_config={
            "当前市值": st.column_config.NumberColumn(format="%.0f"),
            "目标市值": st.column_config.NumberColumn(format="%.0f"),
            "计划金额": st.column_config.NumberColumn(format="%.0f"),
        },
    )


@st.cache_data(ttl=300)
def _load_portfolio_exposure() -> dict[str, pd.DataFrame]:
    exposure_cfg = load_config().get("portfolio", {}).get("exposure", {})
    benchmark_index = str(exposure_cfg.get("benchmark_index") or DEFAULT_BENCHMARK_INDEX)
    conn = get_conn()
    try:
        return load_exposure_snapshot(conn, benchmark_index=benchmark_index)
    finally:
        conn.close()


def show_exposure_monitor():
    st.subheader("持仓暴露")
    exposure_cfg = load_config().get("portfolio", {}).get("exposure", {})
    if exposure_cfg and not bool(exposure_cfg.get("enabled", True)):
        st.caption("持仓暴露监控未启用")
        return

    snapshot = _load_portfolio_exposure()
    summary = snapshot["summary"].iloc[0]
    if int(summary.get("position_count") or 0) == 0:
        st.info("暂无可计算的股票持仓暴露。")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("持仓数", f"{int(summary['position_count'])}")
    c2.metric("最大单票", f"{float(summary['top1_weight']):.1%}")
    c3.metric("Top5集中度", f"{float(summary['top5_weight']):.1%}")
    c4.metric("最大行业", f"{float(summary['max_industry_weight']):.1%}")
    pe_text = "—" if float(summary["pe_coverage"]) <= 0 else f"{float(summary['weighted_pe_ttm']):.1f}"
    pb_text = "—" if float(summary["pb_coverage"]) <= 0 else f"{float(summary['weighted_pb']):.2f}"
    c5.metric("PE / PB", f"{pe_text} / {pb_text}")

    industry = snapshot["industry"].copy()
    size = snapshot["size"].copy()
    positions = snapshot["positions"].copy()

    tab_industry, tab_size, tab_positions = st.tabs(["行业", "市值", "明细"])
    with tab_industry:
        display = industry.rename(columns={
            "industry": "行业",
            "market_value": "市值",
            "weight": "组合权重",
            "benchmark_weight": "基准权重",
            "relative_weight": "相对偏离",
            "position_count": "持仓数",
        })
        st.dataframe(
            display[["行业", "市值", "组合权重", "基准权重", "相对偏离", "持仓数"]],
            hide_index=True,
            width="stretch",
            column_config={
                "市值": st.column_config.NumberColumn(format="%.0f"),
                "组合权重": st.column_config.NumberColumn(format="%.1%"),
                "基准权重": st.column_config.NumberColumn(format="%.1%"),
                "相对偏离": st.column_config.NumberColumn(format="%+.1%"),
            },
        )

    with tab_size:
        display = size.rename(columns={
            "size_bucket": "市值分层",
            "market_value": "市值",
            "weight": "组合权重",
            "position_count": "持仓数",
        })
        st.dataframe(
            display[["市值分层", "市值", "组合权重", "持仓数"]],
            hide_index=True,
            width="stretch",
            column_config={
                "市值": st.column_config.NumberColumn(format="%.0f"),
                "组合权重": st.column_config.NumberColumn(format="%.1%"),
            },
        )

    with tab_positions:
        display = positions.rename(columns={
            "symbol": "代码",
            "name": "名称",
            "industry": "行业",
            "size_bucket": "市值分层",
            "market_value": "市值",
            "weight": "权重",
            "market_cap": "总市值",
            "pe_ttm": "PE",
            "pb": "PB",
            "strategies": "策略",
        })
        st.dataframe(
            display[["代码", "名称", "行业", "市值分层", "市值", "权重", "总市值", "PE", "PB", "策略"]],
            hide_index=True,
            width="stretch",
            column_config={
                "市值": st.column_config.NumberColumn(format="%.0f"),
                "权重": st.column_config.NumberColumn(format="%.1%"),
                "总市值": st.column_config.NumberColumn(format="%.0f"),
                "PE": st.column_config.NumberColumn(format="%.1f"),
                "PB": st.column_config.NumberColumn(format="%.2f"),
            },
        )


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
    show_allocation_plan()
    st.divider()
    show_exposure_monitor()
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
