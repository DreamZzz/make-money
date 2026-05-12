"""指数基金页面 — 独立信号、持仓快照收益和配置状态。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import DuckDBError, db_error_widget, query_df
from src.index_funds.config import get_watchlist, watchlist_to_frame
from src.index_funds.performance import add_snapshot, evaluate_holdings
from src.index_funds.signals import ACTION_LABELS


def _ensure_tables() -> None:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300)
def _load_latest_signals() -> pd.DataFrame:
    df = query_df("""
        SELECT signal_date, fund_code, index_code, action, target_weight,
               confidence, thesis, risk_tags, created_at
        FROM index_fund_signals
        WHERE signal_date = (SELECT MAX(signal_date) FROM index_fund_signals)
        ORDER BY confidence DESC, index_code
    """)
    if df.empty:
        return df
    watch = watchlist_to_frame(get_watchlist())
    name_map = {}
    index_name_map = {}
    for _, row in watch.iterrows():
        key = str(row.get("fund_code") or "")
        index_code = str(row.get("tracking_index") or "")
        if key:
            name_map[key] = row.get("name")
        if index_code:
            index_name_map[index_code] = row.get("tracking_index_name") or index_code
    df["建议"] = df["action"].map(ACTION_LABELS).fillna(df["action"])
    df["基金名称"] = df["fund_code"].map(name_map).fillna("")
    df["指数名称"] = df["index_code"].map(index_name_map).fillna(df["index_code"])
    return df


@st.cache_data(ttl=300)
def _load_holdings() -> pd.DataFrame:
    from src.data_pipeline.loader import get_connection

    conn = get_connection(read_only=True)
    try:
        return evaluate_holdings(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300)
def _load_chart_data(fund_code: str, index_code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    fund_df = pd.DataFrame()
    if fund_code:
        fund_df = query_df("""
            SELECT trade_date, COALESCE(close, nav) AS close
            FROM fund_nav
            WHERE fund_code = ?
            ORDER BY trade_date
        """, [fund_code])
    index_df = query_df("""
        SELECT trade_date, close
        FROM index_daily
        WHERE index_code = ?
        ORDER BY trade_date
    """, [index_code])
    return fund_df, index_df


def _render_snapshot_form() -> None:
    watch = [item for item in get_watchlist() if item.fund_code]
    with st.expander("更新持仓快照", expanded=False):
        if watch:
            labels = [f"{item.fund_code} · {item.name}" for item in watch]
            selected = st.selectbox("基金", labels)
            fund_code = watch[labels.index(selected)].fund_code
        else:
            fund_code = st.text_input("基金代码", placeholder="例如 510300")
        c1, c2, c3 = st.columns(3)
        snapshot_date = c1.date_input("快照日期", value=date.today())
        shares = c2.number_input("当前份额", min_value=0.0, value=0.0, step=100.0)
        cost_amount = c3.number_input("累计成本金额", min_value=0.0, value=0.0, step=1000.0)
        note = st.text_input("备注", value="")
        if st.button("保存快照", type="primary"):
            if not fund_code:
                st.warning("请先填写基金代码。")
            else:
                add_snapshot(fund_code=fund_code, snapshot_date=snapshot_date, shares=shares, cost_amount=cost_amount, note=note)
                st.cache_data.clear()
                st.success("持仓快照已保存。")
                st.rerun()


def _render_signals() -> None:
    st.subheader("买卖建议")
    df = _load_latest_signals()
    if df.empty:
        st.info("暂无指数基金信号。运行 `python3 -m src.index_funds.signals generate` 后可见。")
        return
    latest = df["signal_date"].max()
    st.caption(f"最新信号日期：{latest}")
    display = df[["指数名称", "fund_code", "基金名称", "建议", "target_weight", "confidence", "thesis"]].rename(
        columns={
            "fund_code": "基金代码",
            "target_weight": "目标权重",
            "confidence": "置信度",
            "thesis": "理由",
        }
    )
    for col in ["目标权重", "置信度"]:
        display[col] = display[col] * 100
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "目标权重": st.column_config.NumberColumn(format="%.1f%%"),
            "置信度": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )


def _render_holdings() -> None:
    st.subheader("持仓收益")
    holdings = _load_holdings()
    if holdings.empty:
        st.info("暂无持仓快照。可先在上方录入当前份额和累计成本。")
        return
    total_value = holdings["market_value"].fillna(0).sum()
    total_cost = holdings["cost_amount"].fillna(0).sum()
    total_return = total_value / total_cost - 1 if total_cost > 0 else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("指数基金市值", f"{total_value:,.2f}")
    c2.metric("累计成本", f"{total_cost:,.2f}")
    c3.metric("持仓收益率", f"{total_return:.2%}")

    display = holdings.rename(
        columns={
            "fund_code": "基金代码",
            "name": "名称",
            "tracking_index": "跟踪指数",
            "snapshot_date": "快照日期",
            "shares": "份额",
            "cost_amount": "成本",
            "nav_date": "净值日期",
            "latest_nav": "最新净值",
            "market_value": "市值",
            "current_weight": "当前权重",
            "holding_return": "持仓收益",
            "tracking_index_return": "指数同期",
            "excess_return": "相对收益",
            "max_drawdown": "最大回撤",
        }
    )
    for col in ["当前权重", "持仓收益", "指数同期", "相对收益", "最大回撤"]:
        display[col] = display[col] * 100
    st.dataframe(
        display[["基金代码", "名称", "跟踪指数", "快照日期", "份额", "成本", "净值日期", "最新净值", "市值", "当前权重", "持仓收益", "指数同期", "相对收益", "最大回撤"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "当前权重": st.column_config.NumberColumn(format="%.1f%%"),
            "持仓收益": st.column_config.NumberColumn(format="%.2f%%"),
            "指数同期": st.column_config.NumberColumn(format="%.2f%%"),
            "相对收益": st.column_config.NumberColumn(format="%.2f%%"),
            "最大回撤": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )


def _render_chart() -> None:
    st.subheader("基金与指数走势")
    watch = get_watchlist()
    if not watch:
        st.info("暂无指数基金配置。")
        return
    labels = [f"{item.tracking_index_name} · {item.fund_code or '未配置基金'}" for item in watch]
    selected = st.selectbox("观察标的", labels, key="index_fund_chart_item")
    item = watch[labels.index(selected)]
    fund_df, index_df = _load_chart_data(item.fund_code, item.tracking_index)
    if fund_df.empty and index_df.empty:
        st.info("暂无基金或指数走势数据。")
        return
    fig = go.Figure()
    if not index_df.empty:
        idx = index_df.copy()
        idx["trade_date"] = pd.to_datetime(idx["trade_date"])
        idx["norm"] = idx["close"] / idx["close"].iloc[0]
        fig.add_trace(go.Scatter(x=idx["trade_date"], y=idx["norm"], mode="lines", name=item.tracking_index_name))
    if not fund_df.empty:
        fund = fund_df.copy()
        fund["trade_date"] = pd.to_datetime(fund["trade_date"])
        fund["norm"] = fund["close"] / fund["close"].iloc[0]
        fig.add_trace(go.Scatter(x=fund["trade_date"], y=fund["norm"], mode="lines", name=item.name))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="归一化净值")
    st.plotly_chart(fig, use_container_width=True)


def _render_config_status() -> None:
    st.subheader("配置状态")
    watch = watchlist_to_frame(get_watchlist(active_only=False))
    if watch.empty:
        st.info("未配置指数基金观察列表。")
        return
    display = watch.rename(
        columns={
            "fund_code": "基金代码",
            "name": "名称",
            "fund_type": "类型",
            "tracking_index": "跟踪指数",
            "tracking_index_name": "指数名称",
            "target_weight": "目标权重",
            "enabled": "启用",
        }
    )
    display["目标权重"] = display["目标权重"] * 100
    st.dataframe(
        display[["基金代码", "名称", "类型", "跟踪指数", "指数名称", "目标权重", "启用"]],
        hide_index=True,
        use_container_width=True,
        column_config={"目标权重": st.column_config.NumberColumn(format="%.1f%%")},
    )


st.title("指数基金")

try:
    _ensure_tables()
    _render_snapshot_form()
    tab_signals, tab_holdings, tab_chart, tab_config = st.tabs(["信号建议", "持仓收益", "走势对比", "配置状态"])
    with tab_signals:
        _render_signals()
    with tab_holdings:
        _render_holdings()
    with tab_chart:
        _render_chart()
    with tab_config:
        _render_config_status()
except DuckDBError as exc:
    db_error_widget(exc)
