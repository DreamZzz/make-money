"""本周操作建议 — 决策就绪格式的信号摘要与调仓计划"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard.db import get_conn, db_error_widget, DuckDBError


@st.cache_data(ttl=3600)
def _load_stock_name_map() -> dict[str, tuple[str, str]]:
    """返回 {symbol: (中文名, 市场)} 的字典，缓存 1 小时"""
    conn = get_conn()
    df = conn.execute("SELECT symbol, name, country FROM stock_info").fetchdf()
    conn.close()
    result = {}
    for _, row in df.iterrows():
        market = "A股" if row["country"] == "CN" else "H股" if row["country"] == "HK" else (row["country"] or "—")
        result[str(row["symbol"])] = (row["name"] or str(row["symbol"]), market)
    return result


# ── 数据加载 ──────────────────────────────────────────────────────────────────

def _load_signals(days_back: int = 7) -> pd.DataFrame:
    """加载最近 N 天的信号；若无则回退到最新一批（按 signal_ts 日期部分）"""
    conn = get_conn()
    cutoff = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    df = conn.execute("""
        SELECT signal_id, model_name, symbol, side, score, confidence,
               horizon, max_position_pct, thesis, signal_ts,
               CAST(signal_ts AS DATE) AS trade_date
        FROM signals
        WHERE signal_ts >= ?
        ORDER BY confidence DESC, score DESC
    """, [cutoff]).fetchdf()

    if df.empty:
        df = conn.execute("""
            SELECT signal_id, model_name, symbol, side, score, confidence,
                   horizon, max_position_pct, thesis, signal_ts,
                   CAST(signal_ts AS DATE) AS trade_date
            FROM signals
            WHERE CAST(signal_ts AS DATE) = (
                SELECT MAX(CAST(signal_ts AS DATE)) FROM signals
            )
            ORDER BY confidence DESC, score DESC
        """).fetchdf()

    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    return df


def _load_current_weights() -> dict[str, float]:
    """从 paper_positions 读取最新纸交易持仓权重"""
    pos = get_conn().execute("""
        SELECT symbol, weight
        FROM paper_positions
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date)
            FROM paper_positions
            GROUP BY strategy_name
        )
        AND quantity > 0
    """).fetchdf()

    if pos.empty:
        return {}
    return dict(zip(pos["symbol"], pos["weight"].fillna(0)))


# ── 各区块渲染 ────────────────────────────────────────────────────────────────

def _render_summary_metrics(df: pd.DataFrame):
    buy_n  = (df["side"] == "BUY").sum()
    sell_n = df["side"].isin(["SELL", "SHORT"]).sum()
    latest = df["trade_date"].dt.date.max() if not df.empty else "—"
    avg_conf = df["confidence"].mean() if not df.empty else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("买入信号", f"{buy_n} 只")
    c2.metric("减仓信号", f"{sell_n} 只")
    c3.metric("信号日期", str(latest))
    c4.metric("平均置信度", f"{avg_conf:.0%}")


def _render_signal_table(sub: pd.DataFrame, side_label: str, name_map: dict):
    """渲染单组信号的标准表格"""
    st.markdown(f"#### {side_label}（{len(sub)} 只）")
    if sub.empty:
        st.info("本期无此类信号")
        return

    display = sub[["symbol", "confidence", "max_position_pct", "model_name", "horizon", "thesis"]].copy()
    display.insert(1, "名称", display["symbol"].map(lambda s: name_map.get(s, (s, "—"))[0]))
    display.insert(2, "市场", display["symbol"].map(lambda s: name_map.get(s, ("", "—"))[1]))
    display.columns = ["代码", "名称", "市场", "置信度", "建议仓位", "策略", "持仓周期", "理由"]

    styled = (
        display.style
        .format({"置信度": "{:.0%}", "建议仓位": "{:.0%}"})
        .background_gradient(subset=["置信度"], cmap="Greens", vmin=0, vmax=1)
    )
    st.dataframe(styled, hide_index=True, use_container_width=True)


def _render_confidence_chart(df: pd.DataFrame, name_map: dict):
    buy = df[df["side"] == "BUY"].head(20).sort_values("confidence").copy()
    if buy.empty:
        return

    buy["label"] = buy["symbol"].map(
        lambda s: f"{name_map.get(s, (s,))[0]}({s})"
    )
    fig = px.bar(
        buy, x="confidence", y="label", orientation="h",
        color="confidence", color_continuous_scale="Greens",
        labels={"confidence": "置信度", "label": ""},
        title="买入信号置信度分布",
    )
    fig.update_layout(
        height=max(200, 28 * len(buy) + 80),
        coloraxis_showscale=False,
        xaxis_tickformat=".0%",
        xaxis_range=[0, 1],
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_rebalance_plan(df: pd.DataFrame, name_map: dict):
    st.subheader("调仓计划")

    current_weights = _load_current_weights()
    if not current_weights:
        st.info(
            "暂无纸交易持仓记录，无法生成调仓计划。\n\n"
            "运行 `python -m src.portfolio.paper_engine` 后可见。"
        )
        return

    from src.portfolio.optimizer import compute_target_positions

    buy = df[df["side"] == "BUY"]
    target_weights: dict[str, float] = {
        row["symbol"]: float(row["max_position_pct"] or 0.05)
        for _, row in buy.iterrows()
    }

    plan = compute_target_positions(current_weights, target_weights)

    if plan.empty:
        st.success("当前持仓与目标权重一致，无需调仓。")
        return

    plan.insert(1, "名称", plan["symbol"].map(lambda s: name_map.get(s, (s, "—"))[0]))
    plan.insert(2, "市场", plan["symbol"].map(lambda s: name_map.get(s, ("", "—"))[1]))

    fmt = {"current_weight": "{:.1%}", "target_weight": "{:.1%}", "delta_weight": "{:+.1%}"}

    buy_plan    = plan[plan["action"] == "买入"]
    reduce_plan = plan[plan["action"].isin(["减仓", "清仓"])]

    if not buy_plan.empty:
        st.markdown("**需买入**")
        st.dataframe(buy_plan.style.format(fmt), hide_index=True, use_container_width=True)

    if not reduce_plan.empty:
        st.markdown("**需减仓 / 清仓**")
        st.dataframe(reduce_plan.style.format(fmt), hide_index=True, use_container_width=True)

    st.caption(
        "建议仓位 = 信号 `max_position_pct`，以组合总市值为基准。"
        "实际下单前请结合流动性与风控规则二次确认。"
    )


# ── 页面主体 ──────────────────────────────────────────────────────────────────

st.title("📋 本周操作建议")

try:
    with st.spinner("加载信号数据…"):
        df_all = _load_signals(days_back=7)
        name_map = _load_stock_name_map()
except DuckDBError as e:
    db_error_widget(e)

if df_all.empty:
    st.warning(
        "数据库中暂无信号。请先运行：\n\n"
        "```bash\nbash scripts/run_backtest.sh\n"
        "# 或\npython -m src.signals.generator\n```"
    )
    st.stop()

# 日期选择器
available_dates = sorted(df_all["trade_date"].dt.date.unique(), reverse=True)
selected_date = st.selectbox(
    "查看日期",
    available_dates,
    format_func=lambda d: d.strftime("%Y-%m-%d (%A)") if hasattr(d, "strftime") else str(d),
)
df_view = df_all[df_all["trade_date"].dt.date == selected_date]

_render_summary_metrics(df_view)
st.divider()

col_left, col_right = st.columns([3, 2])
with col_left:
    _render_signal_table(df_view[df_view["side"] == "BUY"].head(20), "买入建议", name_map)
    st.divider()
    _render_signal_table(
        df_view[df_view["side"].isin(["SELL", "SHORT"])].head(10),
        "减仓 / 清仓建议",
        name_map,
    )
with col_right:
    _render_confidence_chart(df_view, name_map)

st.divider()
_render_rebalance_plan(df_view, name_map)
