"""Qlib 分析页 — 回测净值、因子重要性、选股详情、模型概览"""
import sys
sys.path.insert(0, "/Users/zhaoqiang/Documents/Project/make-money")

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import get_conn, db_error_widget, DuckDBError




def _has_data() -> bool:
    cnt = get_conn().execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
    return cnt > 0


def show_nav_curve():
    """回测净值曲线"""
    st.subheader("回测净值曲线")

    if not _has_data():
        st.info("暂无回测数据。请先运行 `bash scripts/run_backtest.sh all` 生成回测结果。")
        _show_demo_nav_chart()
        return

    results = get_conn().execute("""
        SELECT strategy_name, start_date, end_date,
               cumulative_return, annual_return, max_drawdown
        FROM backtest_results ORDER BY sharpe_ratio DESC
    """).fetchdf()

    # 净值对比柱状图
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=results["strategy_name"],
        y=results["cumulative_return"] * 100,
        text=results["cumulative_return"].apply(lambda x: f"{x*100:.1f}%"),
        textposition="outside",
        name="累计收益",
    ))
    fig.update_layout(height=350, yaxis_title="累计收益 (%)", xaxis_title=None)
    st.plotly_chart(fig, width="stretch")


def _show_demo_nav_chart():
    """无数据时展示示例图"""
    import numpy as np
    dates = pd.date_range("2024-01-01", "2025-04-01", freq="W")
    np.random.seed(42)
    bench = (1 + np.random.randn(len(dates)) * 0.02).cumprod()
    strat = (1 + np.random.randn(len(dates)) * 0.025 + 0.002).cumprod()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=bench, mode="lines", name="基准 (沪深300)"))
    fig.add_trace(go.Scatter(x=dates, y=strat, mode="lines", name="策略 Alpha158"))
    fig.update_layout(height=350, title="示例：回测净值曲线（模拟数据）",
                      xaxis_title="日期", yaxis_title="净值")
    st.plotly_chart(fig, width="stretch")
    st.caption("以上为模拟示例数据，实际回测完成后替换为真实净值曲线。")


def show_factor_importance():
    """因子重要性排名"""
    st.subheader("因子重要性分析")

    if not _has_data():
        st.info("暂无 Qlib 因子分析数据。配置 Qlib 工作流后，此区域将展示：")
        st.markdown("""
        - **IC (Information Coefficient)**：因子值与未来收益的相关系数
        - **ICIR**：IC 均值 / IC 标准差，衡量因子稳定性
        - **Rank IC**：排序相关性，对异常值更稳健
        - **累计 Rank IC**：因子长期预测能力的可视化
        """)
        _show_demo_factors()
        return

    st.info("因子数据将从 Qlib SigAnaRecord 读取，目前为占位展示。")


def _show_demo_factors():
    """展示因子分析示例数据"""
    demo_factors = pd.DataFrame({
        "因子": ["PE_TTM", "PB", "ROE_Q", "MOM_20", "VOL_5", "TURN_1M",
                 "REV_5", "SIZE", "ILLIQUIDITY", "BETA_60"],
        "Rank IC": [0.042, 0.035, 0.031, 0.028, -0.025, 0.022, -0.020, 0.018, 0.015, 0.012],
        "ICIR": [0.85, 0.72, 0.68, 0.55, -0.48, 0.42, -0.38, 0.35, 0.30, 0.25],
    })

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**因子 IC 排名（示例）**")
        st.dataframe(demo_factors.style.background_gradient(
            subset=["Rank IC", "ICIR"], cmap="RdYlGn"
        ), hide_index=True, width="stretch")
    with col2:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=demo_factors["Rank IC"].values,
            y=demo_factors["因子"].values,
            orientation="h",
            marker_color=["#26a69a" if v > 0 else "#ef5350" for v in demo_factors["Rank IC"]],
        ))
        fig.update_layout(height=350, title="Rank IC 排序", xaxis_title="Rank IC")
        st.plotly_chart(fig, width="stretch")
    st.caption("以上为示例数据，实际因子分析结果将在 Qlib 回测运行后自动填充。")


def show_stock_selection():
    """选股信号详情"""
    st.subheader("选股信号详情")

    signals_df = get_conn().execute("""
        SELECT signal_ts, model_name, symbol, side, score, confidence, thesis
        FROM signals ORDER BY signal_ts DESC, score DESC LIMIT 50
    """).fetchdf()

    if signals_df.empty:
        st.info("暂无选股信号。运行 `python -m src.signals.generator` 生成信号。")
        st.markdown("""
        ### 信号生成流程

        1. **数据准备**：`python -m src.data_pipeline.main init`
        2. **Qlib 回测**：`bash scripts/run_backtest.sh all`
        3. **信号生成**：`python -m src.signals.generator`

        信号将自动存入数据库并在本页展示。
        """)
        return

    tab1, tab2 = st.tabs(["信号明细", "按策略汇总"])

    with tab1:
        def _side_color(val):
            return "color: #26a69a; font-weight: bold" if val == "BUY" else "color: #ef5350; font-weight: bold"
        st.dataframe(
            signals_df.style.map(_side_color, subset=["side"])
            .format({"score": "{:.3f}", "confidence": "{:.2f}"}),
            hide_index=True, width="stretch",
        )

    with tab2:
        summary = signals_df.groupby("model_name").agg(
            信号数=("symbol", "count"),
            买入数=("side", lambda x: (x == "BUY").sum()),
            卖出数=("side", lambda x: (x == "SELL").sum()),
            平均置信度=("confidence", "mean"),
        ).round(3)
        st.dataframe(summary, width="stretch")


def show_model_overview():
    """模型训练概览"""
    st.subheader("模型训练概览")

    config = {
        "策略名称": "Alpha158 基线",
        "模型": "LightGBM",
        "因子集": "Alpha158 (158个标准化因子)",
        "训练周期": "2019-01-01 ~ 2022-12-31",
        "验证周期": "2023-01-01 ~ 2023-12-31",
        "测试周期": "2024-01-01 ~ 至今",
        "调仓频率": "周频（每周五）",
        "股票池": "沪深300 成分股",
        "成本模型": "佣金万2.5 + 印花税千1",
    }

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**当前模型配置**")
        for k, v in config.items():
            st.text(f"{k}: {v}")

    with col2:
        st.markdown("**待接入信息**")
        st.markdown("""
        Qlib 工作流运行后，此处将展示：
        - 模型超参数
        - 特征数量与缺失率
        - 训练/验证 Loss 曲线
        - 预测耗时统计
        - 数据集切分详情
        """)


# ---- 页面渲染 ----
st.title("🧠 Qlib 分析")
tab_a, tab_b, tab_c, tab_d = st.tabs(["净值曲线", "因子分析", "选股详情", "模型概览"])

with tab_a:
    try:
        show_nav_curve()
    except DuckDBError as e:
        db_error_widget(e)
with tab_b:
    try:
        show_factor_importance()
    except DuckDBError as e:
        db_error_widget(e)
with tab_c:
    try:
        show_stock_selection()
    except DuckDBError as e:
        db_error_widget(e)
with tab_d:
    show_model_overview()
