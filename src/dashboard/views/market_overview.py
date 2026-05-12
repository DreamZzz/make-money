"""市场行情工作台 — 收盘复盘与研究分析。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.dashboard.db import DuckDBError, db_error_widget
from src.dashboard.market_service import (
    DISTRIBUTION_BUCKETS,
    INDEX_DEFS,
    load_data_quality_status,
    load_field_coverage,
    load_index_benchmarks,
    load_market_breadth,
    load_market_movers,
    load_market_overview,
    load_sector_style,
)

PERIODS = {
    "1月": {"days": 22},
    "3月": {"days": 66},
    "6月": {"days": 132},
    "1年": {"days": 252},
    "YTD": {"start_date": date(date.today().year, 1, 1)},
    "全部": {"days": 9999},
}

UP_COLOR = "#d94c4c"
DOWN_COLOR = "#1b9e77"
NEUTRAL_COLOR = "#64748b"


def _pct(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):+.{digits}f}%"


def _ratio_pct(value, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{digits}f}"


def _amount_yi(value, currency: str = "") -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    # AkShare 多数成交额为元，部分历史数据可能已是万元；用数量级做保守展示。
    amount_yi = value / 1e8 if abs(value) >= 1e7 else value / 1e4
    suffix = f" {currency}" if currency else ""
    return f"{amount_yi:,.2f} 亿{suffix}"


@st.cache_data(ttl=300)
def _cached_overview():
    return load_market_overview()


@st.cache_data(ttl=300)
def _cached_breadth():
    return load_market_breadth()


@st.cache_data(ttl=300)
def _cached_movers(limit: int):
    return load_market_movers(limit=limit)


@st.cache_data(ttl=300)
def _cached_sector_style():
    return load_sector_style()


@st.cache_data(ttl=300)
def _cached_field_coverage():
    return load_field_coverage()


@st.cache_data(ttl=300)
def _cached_quality():
    return load_data_quality_status()


@st.cache_data(ttl=300)
def _cached_indices(days: int, start_date):
    return load_index_benchmarks(days=days, start_date=start_date)


def _render_market_cards(overview: dict):
    markets = overview.get("markets", {})
    if not markets:
        st.info("暂无股票行情数据。可先运行 `python -m src.data_pipeline.main update`。")
        return
    cols = st.columns(len(markets))
    for col, (market, item) in zip(cols, markets.items()):
        with col:
            with st.container(border=True):
                st.markdown(f"#### {item['label']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("最新交易日", str(item["latest_date"]))
                c2.metric("覆盖", f"{item['total']} 只")
                c3.metric("市场温度", item["temperature"])
                c4, c5, c6 = st.columns(3)
                c4.metric("上涨", item["advancers"])
                c5.metric("下跌", item["decliners"])
                c6.metric("上涨占比", _ratio_pct(item["up_ratio"]))
                c7, c8, c9 = st.columns(3)
                c7.metric("中位涨跌", _pct(item["median_pct_chg"]))
                c8.metric("平均涨跌", _pct(item["avg_pct_chg"]))
                c9.metric("成交额", _amount_yi(item["total_amount"], item.get("currency", "")))
                st.caption(f"来源：{item.get('source', 'daily_price')}；A/H 使用各自最新交易日，不混合币种。")


def _render_distribution(distribution: pd.DataFrame):
    if distribution.empty:
        return
    fig = go.Figure()
    for market_label, sub in distribution.groupby("market_label"):
        sub = sub.set_index("bucket").reindex(DISTRIBUTION_BUCKETS, fill_value=0).reset_index()
        fig.add_trace(go.Bar(x=sub["bucket"], y=sub["count"], name=market_label))
    fig.update_layout(
        height=360,
        barmode="group",
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h"),
        yaxis_title="股票数量",
    )
    st.plotly_chart(fig, width="stretch")


def show_market_overview():
    st.subheader("市场总览")
    overview = _cached_overview()
    _render_market_cards(overview)
    st.markdown("#### 涨跌幅分布")
    _render_distribution(overview.get("distribution", pd.DataFrame()))


def _render_index_cards(summary: pd.DataFrame):
    if summary.empty:
        return
    cols = st.columns(min(4, len(summary)))
    for col, (_, row) in zip(cols, summary.iterrows()):
        with col:
            st.metric(
                row["name"],
                _num(row["latest_close"]),
                _pct(float(row["one_day_return"]) * 100 if pd.notna(row.get("one_day_return")) else None),
                help=f"区间收益 {_pct(float(row['period_return']) * 100)}；最大回撤 {_pct(float(row['max_drawdown']) * 100)}",
            )


def _render_index_normalized(series: pd.DataFrame):
    if series.empty:
        return
    fig = go.Figure()
    for name, sub in series.groupby("name"):
        fig.add_trace(go.Scatter(
            x=sub["trade_date"],
            y=(sub["normalized"] - 1) * 100,
            mode="lines",
            name=name,
        ))
    fig.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="区间涨跌幅（%）",
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, width="stretch")


def _render_index_candlestick(series: pd.DataFrame):
    if series.empty:
        return
    index_options = {item["name"]: item["index_code"] for item in INDEX_DEFS}
    selected_name = st.selectbox("K线标的", list(index_options.keys()), key="market_index_kline")
    code = index_options[selected_name]
    df = series[series["index_code"] == code].sort_values("trade_date")
    if df.empty:
        st.info(f"暂无 {selected_name} K线数据。")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.70, 0.30], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(
        x=df["trade_date"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="K线",
    ), row=1, col=1)
    for ma, color in [("ma5", "#eab308"), ("ma20", "#ec4899"), ("ma60", "#2563eb")]:
        if ma in df:
            fig.add_trace(go.Scatter(x=df["trade_date"], y=df[ma], mode="lines", name=ma.upper(), line=dict(color=color, width=1.2)), row=1, col=1)
    colors = [UP_COLOR if close >= open_ else DOWN_COLOR for close, open_ in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=df["trade_date"], y=df["volume"], name="成交量", marker_color=colors, opacity=0.45), row=2, col=1)
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False, legend=dict(orientation="h"))
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    st.plotly_chart(fig, width="stretch")


def show_index_benchmarks():
    st.subheader("指数与基准")
    period = st.radio("区间", list(PERIODS.keys()), horizontal=True, index=2, key="market_index_period")
    config = PERIODS[period]
    result = _cached_indices(config.get("days", 9999), config.get("start_date"))
    series = result["series"]
    summary = result["summary"]
    if series.empty:
        st.info("暂无指数数据。可运行 `python -m src.data_pipeline.main update`。")
        return
    _render_index_cards(summary)
    st.markdown("#### 归一化收益对比")
    _render_index_normalized(series)
    st.markdown("#### K线与量能")
    _render_index_candlestick(series)
    display = summary.copy()
    display["区间收益"] = display["period_return"].map(lambda x: _pct(float(x) * 100))
    display["最大回撤"] = display["max_drawdown"].map(lambda x: _pct(float(x) * 100))
    display["最新收盘"] = display["latest_close"].map(_num)
    st.dataframe(display[["name", "latest_date", "最新收盘", "区间收益", "最大回撤"]], hide_index=True, width="stretch")


def show_market_breadth():
    st.subheader("市场广度")
    breadth = _cached_breadth()
    if breadth.empty:
        st.info("暂无足够日线数据计算市场广度。")
        return
    display = breadth.copy()
    for col in ["above_ma20_pct", "above_ma60_pct", "above_ma120_pct"]:
        display[col] = display[col] * 100
    fig = go.Figure()
    for col, label in [("above_ma20_pct", "站上MA20"), ("above_ma60_pct", "站上MA60"), ("above_ma120_pct", "站上MA120")]:
        fig.add_trace(go.Bar(x=display["market_label"], y=display[col], name=label))
    fig.update_layout(height=360, barmode="group", margin=dict(l=10, r=10, t=30, b=10), yaxis_title="占比（%）", legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")

    table = breadth.copy()
    table["站上MA20"] = table["above_ma20_pct"].map(_ratio_pct)
    table["站上MA60"] = table["above_ma60_pct"].map(_ratio_pct)
    table["站上MA120"] = table["above_ma120_pct"].map(_ratio_pct)
    table["中位涨跌"] = table["median_pct_chg"].map(_pct)
    st.dataframe(
        table[[
            "market_label", "latest_date", "total", "站上MA20", "站上MA60", "站上MA120",
            "new_high_20", "new_low_20", "new_high_60", "new_low_60",
            "volume_expand", "volume_contract", "中位涨跌",
        ]],
        column_config={
            "market_label": "市场",
            "latest_date": "最新日期",
            "total": "覆盖",
            "new_high_20": "20日新高",
            "new_low_20": "20日新低",
            "new_high_60": "60日新高",
            "new_low_60": "60日新低",
            "volume_expand": "放量",
            "volume_contract": "缩量",
        },
        hide_index=True,
        width="stretch",
    )


def show_sector_style():
    st.subheader("板块与风格")
    coverage = _cached_field_coverage()
    industry_cov = coverage[coverage["field"] == "industry"] if not coverage.empty else pd.DataFrame()
    if not industry_cov.empty and (industry_cov["coverage_pct"] < 0.3).any():
        st.info("行业字段覆盖不足，当前按已有行业/板块和市场分组降级展示；后续补齐行业元数据后热力图会更有参考价值。")

    sector = _cached_sector_style()
    if sector.empty:
        st.info("暂无板块风格数据。")
        return
    top = sector.sort_values("count", ascending=False).head(40).copy()
    top["avg_pct_chg_display"] = top["avg_pct_chg"].map(_pct)
    top["amount_display"] = top["amount"].map(_amount_yi)
    fig = go.Figure(go.Treemap(
        labels=top["group"],
        parents=top["market_label"],
        values=top["count"],
        marker=dict(colors=top["avg_pct_chg"], colorscale=[[0, DOWN_COLOR], [0.5, "#f8fafc"], [1, UP_COLOR]], cmid=0),
        texttemplate="%{label}<br>%{value}只",
        hovertemplate="%{label}<br>股票数 %{value}<br>平均涨跌 %{color:.2f}%<extra></extra>",
    ))
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(
        top[["market_label", "group", "count", "avg_pct_chg_display", "advancers", "amount_display"]],
        column_config={
            "market_label": "市场",
            "group": "分组",
            "count": "股票数",
            "avg_pct_chg_display": "平均涨跌",
            "advancers": "上涨数",
            "amount_display": "成交额",
        },
        hide_index=True,
        width="stretch",
    )


def _render_mover_table(df: pd.DataFrame):
    if df.empty:
        st.info("暂无数据。")
        return
    display = df.copy()
    display["涨跌幅"] = display["pct_chg"].map(_pct)
    display["成交额"] = display["amount"].map(_amount_yi)
    display["换手率"] = display["turnover_rate"].map(lambda x: _pct(x, digits=2) if pd.notna(x) else "—")
    display["量比"] = display["volume_ratio"].map(_num)
    st.dataframe(
        display[["symbol", "name", "market_label", "industry", "last_price", "涨跌幅", "成交额", "换手率", "量比", "links"]],
        column_config={
            "symbol": "代码",
            "name": "名称",
            "market_label": "市场",
            "industry": "行业",
            "last_price": st.column_config.NumberColumn("最新价", format="%.2f"),
            "links": st.column_config.LinkColumn("外链", width="medium"),
        },
        hide_index=True,
        width="stretch",
    )


def show_movers():
    st.subheader("榜单与异动")
    movers = _cached_movers(20)
    tabs = st.tabs(["涨幅榜", "跌幅榜", "成交额", "量比"])
    for tab, key in zip(tabs, ["gainers", "losers", "turnover", "volume_ratio"]):
        with tab:
            _render_mover_table(movers.get(key, pd.DataFrame()))


def show_data_quality():
    st.subheader("数据口径与质量")
    coverage = _cached_field_coverage()
    if coverage.empty:
        st.info("暂无可统计字段覆盖率。")
    else:
        fig = go.Figure()
        for market_label, sub in coverage.groupby("market_label"):
            fig.add_trace(go.Bar(x=sub["label"], y=sub["coverage_pct"] * 100, name=market_label))
        fig.update_layout(height=360, barmode="group", margin=dict(l=10, r=10, t=30, b=10), yaxis_title="覆盖率（%）", legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        display = coverage.copy()
        display["覆盖率"] = display["coverage_pct"].map(_ratio_pct)
        st.dataframe(
            display[["market_label", "label", "available", "total", "覆盖率", "status"]],
            column_config={
                "market_label": "市场",
                "label": "字段",
                "available": "有效数",
                "total": "总数",
                "status": "状态",
            },
            hide_index=True,
            width="stretch",
        )

    st.markdown("#### 固定口径")
    st.markdown(
        "- 涨跌幅优先使用快照/日线中的昨收；缺失时使用上一交易日收盘价推导。\n"
        "- A股与港股按各自最新交易日展示，成交额保留 CNY/HKD 标签，不做跨币种汇总。\n"
        "- 换手率、量比、PE/PB、市值、行业字段按覆盖率显示可信度；缺失不阻塞页面。\n"
        "- 当前是日终复盘工作台，不接 Level-2、盘口、逐笔成交或实时 WebSocket。"
    )

    quality = _cached_quality()
    if quality.empty:
        st.info("尚未运行结构化数据质量检查。可执行 `python -m src.data_pipeline.main check --full`。")
        return
    latest_check = quality["check_ts"].max()
    warn = quality[quality["status"] != "PASS"]
    if warn.empty:
        st.success(f"数据质量：PASS（检查时间 {latest_check}）")
    else:
        st.warning(f"数据质量：{len(warn)} 项告警（检查时间 {latest_check}）")
        st.dataframe(warn[["metric", "value", "threshold", "detail"]], hide_index=True, width="stretch")


st.title("市场行情")
st.caption("收盘复盘 + 研究分析工作台。数据以本地日线和日终快照为准。")

try:
    tab_overview, tab_index, tab_breadth, tab_sector, tab_movers, tab_quality = st.tabs([
        "市场总览",
        "指数与基准",
        "市场广度",
        "板块与风格",
        "榜单与异动",
        "数据口径",
    ])
    with tab_overview:
        show_market_overview()
    with tab_index:
        show_index_benchmarks()
    with tab_breadth:
        show_market_breadth()
    with tab_sector:
        show_sector_style()
    with tab_movers:
        show_movers()
    with tab_quality:
        show_data_quality()
except DuckDBError as e:
    db_error_widget(e)
