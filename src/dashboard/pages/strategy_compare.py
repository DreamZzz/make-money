"""策略对比页面 — 多策略回测结果横向比较 + 最近选股信号预览 + 命令执行"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import get_conn, db_error_widget, DuckDBError


def show_backtest_results():
    st.subheader("回测结果对比")

    results = get_conn().execute("""
        SELECT strategy_name, market, start_date, end_date,
               annual_return, cumulative_return, sharpe_ratio,
               max_drawdown, turnover, info_ratio, excess_return
        FROM backtest_results
        ORDER BY sharpe_ratio DESC
    """).fetchdf()

    if results.empty:
        st.info("暂无回测结果。运行 `bash scripts/run_backtest.sh` 生成回测数据。")
        _show_placeholder_comparison()
        return

    # 指标格式化
    display = results.copy()
    for col in ["annual_return", "cumulative_return", "max_drawdown", "excess_return"]:
        if col in display.columns:
            display[col] = (display[col] * 100).round(2).astype(str) + "%"
    for col in ["sharpe_ratio", "sortino_ratio", "info_ratio"]:
        if col in display.columns:
            display[col] = display[col].round(2)

    st.dataframe(display, hide_index=True, width="stretch")

    # 柱状图对比
    fig = go.Figure()
    metrics = ["annual_return", "sharpe_ratio", "max_drawdown", "excess_return"]
    available = [m for m in metrics if m in results.columns]
    if available:
        for m in available:
            fig.add_trace(go.Bar(name=m, x=results["strategy_name"], y=results[m]))
        fig.update_layout(barmode="group", height=400)
        st.plotly_chart(fig, width="stretch")


def _show_placeholder_comparison():
    """无数据时展示策略框架"""
    tabs = st.tabs(["策略框架", "回测标准", "接入步骤"])

    with tabs[0]:
        st.markdown("""
        | 策略 | 类型 | 调仓频率 | 说明 |
        |------|------|----------|------|
        | Alpha158 | 多因子选股 | 周频 | 158个标准化因子 + LightGBM |
        | 趋势跟踪 | 技术分析 | 周频 | 双均线 + 通道突破 + ATR止损 |
        | 行业轮动 | 行业动量 | 月频 | 行业动量排名 + 成分股精选 |
        | 均值回归 | 统计套利 | 日/周频 | RSI + 布林带 超买超卖 |
        """)

    with tabs[1]:
        st.markdown("""
        - **股票池**：沪深300 + 恒生指数成分股
        - **时间窗口**：train 2019-2022 / valid 2023 / test 2024-至今
        - **成本模型**：A股 万2.5佣金 + 千1印花税 / 港股 千1佣金 + 千1印花税
        - **基准**：沪深300(50%) + 恒生指数(50%)
        """)

    with tabs[2]:
        st.markdown("""
        ```bash
        # Step 1: 数据准备
        python -m src.data_pipeline.main init

        # Step 2: Qlib 回测
        bash scripts/run_backtest.sh all

        # Step 3: 生成信号
        python -m src.signals.generator
        ```
        """)


def show_signal_preview():
    """展示各策略最近选股信号预览"""
    st.subheader("最近选股信号预览")
    st.caption("点击 [Qlib分析](/qlib_analysis) 查看详细因子和选股数据")

    signals_df = get_conn().execute("""
        SELECT model_name, symbol, side, score, confidence, horizon, thesis, signal_ts
        FROM signals
        ORDER BY signal_ts DESC, confidence DESC
        LIMIT 20
    """).fetchdf()

    if signals_df.empty:
        st.info("暂无调仓信号。策略对比完成后运行 `python -m src.signals.generator` 生成信号。")
        return

    # 按策略分组展示
    for strategy in signals_df["model_name"].unique():
        sub = signals_df[signals_df["model_name"] == strategy].head(5)
        with st.expander(f"{strategy} — 最近 {len(sub)} 条信号"):
            st.dataframe(
                sub.style.format({"score": "{:.3f}", "confidence": "{:.2f}"}),
                hide_index=True, width="stretch",
            )


def show_factor_importance():
    """因子重要性分析（摘要版，点击下钻到 Qlib 分析页）"""
    st.subheader("因子重要性（摘要）")

    has_data = get_conn().execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0] > 0

    if not has_data:
        st.info("暂无回测数据。Qlib 工作流运行后将自动展示因子 IC/IR 排名。")
        st.page_link("pages/qlib_analysis.py", label="→ 前往 Qlib分析页 查看详细因子框架", icon="🧠")
        return

    st.info("完整的因子分析请前往 [Qlib分析](/qlib_analysis) 页面查看。")


# ---- 命令执行 ----

import subprocess
from pathlib import Path as _Path

_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent
_JOBS_DIR = _PROJECT_ROOT / "data" / "jobs"

COMMANDS = {
    "update": {
        "label": "📡 更新行情数据",
        "desc": "增量拉取最新交易日数据（独立进程，不阻塞 Dashboard）",
        "cmd": ["python3", "-m", "src.data_pipeline.main", "update"],
    },
    "generate_signals": {
        "label": "🎯 生成调仓信号",
        "desc": "汇总所有策略信号写入数据库（独立进程）",
        "cmd": ["python3", "-m", "src.signals.generator"],
    },
}


def _start_job(key: str):
    """以独立子进程启动命令，stdout/stderr 写入日志文件。"""
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _JOBS_DIR / f"{key}.log"
    with open(log_path, "w") as lf:
        proc = subprocess.Popen(
            COMMANDS[key]["cmd"],
            stdout=lf,
            stderr=subprocess.STDOUT,
            cwd=str(_PROJECT_ROOT),
        )
    st.session_state[f"proc_{key}"] = proc
    st.session_state[f"log_{key}"] = str(log_path)
    st.session_state[f"running_{key}"] = True
    st.session_state[f"exit_{key}"] = None
    st.session_state["_cmd_running"] = True


def _poll_job(key: str) -> tuple:
    """检查作业状态，返回 (running: bool, retcode: int|None, log: str)。"""
    proc = st.session_state.get(f"proc_{key}")
    if proc is None:
        return False, st.session_state.get(f"exit_{key}"), _read_log(key)

    retcode = proc.poll()
    running = retcode is None
    if not running:
        st.session_state[f"running_{key}"] = False
        st.session_state[f"exit_{key}"] = retcode
        if not any(st.session_state.get(f"running_{k}", False) for k in COMMANDS):
            st.session_state["_cmd_running"] = False

    return running, retcode, _read_log(key)


def _read_log(key: str) -> str:
    log_path = st.session_state.get(f"log_{key}", "")
    if log_path and _Path(log_path).exists():
        return _Path(log_path).read_text(errors="replace")
    return ""


def show_command_panel():
    """命令执行面板 — 子进程模式，写操作与 Dashboard 读取完全隔离。"""
    st.subheader("🔧 命令执行")

    for key in COMMANDS:
        for attr, default in [("running", False), ("exit", None), ("proc", None), ("log", "")]:
            st.session_state.setdefault(f"{attr}_{key}", default)
    st.session_state.setdefault("_cmd_running", False)

    # 每次渲染前统一 poll 一次，避免重复 IO
    job_states = {key: _poll_job(key) for key in COMMANDS}

    with st.expander("展开命令面板", expanded=st.session_state.get("_cmd_running", False)):
        st.caption("命令以独立子进程运行，Streamlit 持只读锁，写操作无冲突。点击「刷新状态」获取最新输出。")

        cols = st.columns(len(COMMANDS))
        for i, (key, info) in enumerate(COMMANDS.items()):
            running, retcode, _ = job_states[key]
            with cols[i]:
                if st.button(info["label"], key=f"btn_{key}", disabled=running,
                             help=info["desc"], width="stretch"):
                    _start_job(key)
                    st.rerun()

                if running:
                    st.info("⏳ 执行中...")
                elif retcode == 0:
                    st.success("✅ 成功")
                elif retcode is not None:
                    st.error(f"❌ 失败 (退出码: {retcode})")
                else:
                    st.caption("⚪ 就绪")

        st.divider()
        from datetime import datetime

        refresh_col, status_col = st.columns([1, 3])
        with refresh_col:
            if st.button("🔄 刷新状态", key="btn_refresh_status", width="stretch"):
                st.rerun()

        parts = []
        for key, (running, retcode, _) in job_states.items():
            label = COMMANDS[key]["label"]
            if running:
                parts.append(f"{label}: 执行中")
            elif retcode == 0:
                parts.append(f"{label}: 成功")
            elif retcode is not None:
                parts.append(f"{label}: 失败")
        with status_col:
            st.caption(f"上次刷新: {datetime.now().strftime('%H:%M:%S')} | {' | '.join(parts) if parts else '空闲'}")

        st.markdown("**命令输出**")
        output_tabs = st.tabs([info["label"] for info in COMMANDS.values()])
        for tab, (key, info) in zip(output_tabs, COMMANDS.items()):
            with tab:
                running, retcode, log_content = job_states[key]
                if running:
                    st.warning("⏳ 执行中... 点击「刷新状态」获取最新输出")
                elif retcode == 0:
                    st.success("✅ 执行成功")
                elif retcode is not None:
                    st.error(f"❌ 退出码: {retcode}")
                if log_content:
                    st.code(log_content, language="text", line_numbers=False)


# ---- 绩效跟踪 ----

def show_performance_tracking():
    """绩效跟踪：净值曲线 + 指标卡片 + 对比基准"""
    st.subheader("绩效跟踪")

    nav_data = get_conn().execute("""
        SELECT strategy_name, trade_date, nav, daily_return, total_value, drawdown
        FROM portfolio_nav ORDER BY strategy_name, trade_date
    """).fetchdf()

    if nav_data.empty:
        st.info("暂无绩效数据。信号生成并执行纸交易后，运行 `python -m src.portfolio.paper_engine && python -m src.portfolio.nav_calculator`")
        _show_performance_placeholder()
        return

    nav_data["trade_date"] = pd.to_datetime(nav_data["trade_date"])
    strategies = nav_data["strategy_name"].unique()

    # 净值曲线 + 基准
    st.markdown("### 净值曲线")

    # 基准选择
    benchmark_choice = st.selectbox("对比基准", ["沪深300", "中证500", "恒生指数", "无"], index=0, key="perf_benchmark")
    bench_map = {"沪深300": "000300", "中证500": "000905", "恒生指数": "^HSI"}

    fig = go.Figure()
    for s in strategies:
        sub = nav_data[nav_data["strategy_name"] == s]
        fig.add_trace(go.Scatter(x=sub["trade_date"], y=sub["nav"], mode="lines", name=s))

    if benchmark_choice != "无":
        bench_code = bench_map[benchmark_choice]
        bench_df = get_conn().execute("""
            SELECT trade_date, close FROM index_daily WHERE index_code = ? ORDER BY trade_date
        """, [bench_code]).fetchdf()
        if not bench_df.empty:
            bench_df["trade_date"] = pd.to_datetime(bench_df["trade_date"])
            initial = bench_df["close"].iloc[0]
            bench_df["nav"] = bench_df["close"] / initial
            fig.add_trace(go.Scatter(
                x=bench_df["trade_date"], y=bench_df["nav"],
                mode="lines", name=benchmark_choice,
                line=dict(dash="dash", color="gray"),
            ))

    fig.update_layout(height=420, xaxis_title=None, yaxis_title="净值",
                      xaxis_tickformat="%m月%d日", hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    # 指标卡片
    st.markdown("### 策略绩效对比")
    metrics_rows = []
    for s in strategies:
        sub = nav_data[nav_data["strategy_name"] == s]
        if len(sub) < 2:
            continue
        rets = sub["daily_return"].dropna()
        ann_ret = rets.mean() * 252
        ann_vol = rets.std() * np.sqrt(252) if len(rets) > 1 else 0
        sharpe = (ann_ret - 0.03) / ann_vol if ann_vol > 0 else 0
        max_dd = sub["drawdown"].min()
        final_nav = sub["nav"].iloc[-1]
        metrics_rows.append({
            "策略": s, "最终净值": f"{final_nav:.3f}", "累计收益": f"{(final_nav-1)*100:+.1f}%",
            "年化收益": f"{ann_ret*100:.1f}%", "年化波动": f"{ann_vol*100:.1f}%",
            "夏普": f"{sharpe:.2f}", "最大回撤": f"{max_dd*100:.1f}%",
        })

    if metrics_rows:
        st.dataframe(pd.DataFrame(metrics_rows), hide_index=True, width="stretch")

    # 回撤曲线
    st.markdown("### 回撤曲线")
    fig2 = go.Figure()
    for s in strategies:
        sub = nav_data[nav_data["strategy_name"] == s]
        fig2.add_trace(go.Scatter(x=sub["trade_date"], y=sub["drawdown"] * 100, mode="lines", name=s))
    fig2.update_layout(height=250, yaxis_title="回撤 (%)", xaxis_tickformat="%m月%d日")
    st.plotly_chart(fig2, width="stretch")

    # 持仓明细
    st.markdown("### 最新持仓")
    latest_pos = get_conn().execute("""
        SELECT strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
               market_value, pnl, pnl_pct, weight
        FROM paper_positions
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM paper_positions GROUP BY strategy_name
        )
        AND quantity > 0
        ORDER BY strategy_name, weight DESC
    """).fetchdf()
    if not latest_pos.empty:
        for s in latest_pos["strategy_name"].unique():
            sub = latest_pos[latest_pos["strategy_name"] == s]
            with st.expander(f"{s} — {len(sub)} 只持仓"):
                st.dataframe(sub, hide_index=True, width="stretch")
    else:
        st.caption("暂无持仓记录")


def _show_performance_placeholder():
    st.markdown("""
    ### 绩效跟踪闭环

    信号生成后需要执行两步才能看到绩效：

    ```bash
    # 1. 纸交易：将信号转为模拟成交
    python -m src.portfolio.paper_engine

    # 2. 净值计算：基于持仓和收盘价计算净值曲线
    python -m src.portfolio.nav_calculator
    ```

    执行后刷新本页面即可看到净值曲线和绩效指标。
    """)


# ---- 页面渲染 ----
st.title("⚖️ 策略对比")
show_command_panel()
st.divider()

tab_backtest, tab_performance = st.tabs(["回测对比", "绩效跟踪"])

with tab_backtest:
    try:
        show_backtest_results()
        st.divider()
        show_signal_preview()
        st.divider()
        show_factor_importance()
    except DuckDBError as e:
        db_error_widget(e)

with tab_performance:
    try:
        show_performance_tracking()
    except DuckDBError as e:
        db_error_widget(e)
