"""策略对比页面 — 多策略回测结果横向比较 + 最近选股信号预览 + 命令执行"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import query_df, query_one, db_error_widget, DuckDBError
from src.dashboard.job_manager import (
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    SUCCEEDED,
    JobDefinition,
    JobRun,
    active_run,
    advanced_jobs,
    latest_run,
    poll_run,
    scenario_jobs,
    start_job,
    tail_log,
)


def show_backtest_results():
    st.subheader("回测结果对比")

    results = query_df("""
        SELECT strategy_name, market, start_date, end_date,
               annual_return, cumulative_return, sharpe_ratio,
               max_drawdown, turnover, info_ratio, excess_return
        FROM backtest_results
        ORDER BY sharpe_ratio DESC
    """)

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

    signals_df = query_df("""
        SELECT model_name, symbol, side, score, confidence, horizon, thesis, signal_ts
        FROM signals
        ORDER BY signal_ts DESC, confidence DESC
        LIMIT 20
    """)

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

    has_data = query_one("SELECT COUNT(*) FROM backtest_results")[0] > 0

    if not has_data:
        st.info("暂无回测数据。Qlib 工作流运行后将自动展示因子 IC/IR 排名。")
        st.page_link("views/qlib_analysis.py", label="→ 前往 Qlib分析页 查看详细因子框架", icon="🧠")
        return

    st.info("完整的因子分析请前往 [Qlib分析](/qlib_analysis) 页面查看。")


# ---- 命令执行 ----

_STATUS_TEXT = {
    PENDING: "待执行",
    RUNNING: "执行中",
    SUCCEEDED: "成功",
    FAILED: "失败",
    SKIPPED: "跳过",
}


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if pd.isna(value):
        return None
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _format_dt(value) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%m-%d %H:%M:%S") if dt else "—"


def _format_duration(run: JobRun | None) -> str:
    if run is None:
        return "—"
    start = _parse_dt(run.data.get("started_at"))
    end = _parse_dt(run.data.get("ended_at")) or datetime.now()
    if start is None:
        return "—"
    seconds = max(int((end - start).total_seconds()), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _step_progress(run: JobRun | None) -> tuple[int, int]:
    if run is None:
        return 0, 0
    total = len(run.steps)
    done = sum(1 for step in run.steps if step.get("status") in {SUCCEEDED, FAILED, SKIPPED})
    return done, total


def _log_summary(log_text: str) -> dict[str, int]:
    lines = log_text.splitlines()
    return {
        "lines": len(lines),
        "info": sum("INFO" in line for line in lines),
        "warnings": sum("WARNING" in line or " warning" in line.lower() for line in lines),
        "errors": sum("ERROR" in line or "Traceback" in line or "FAILED" in line for line in lines),
        "json_blocks": sum(line.strip().startswith("{") or line.strip().startswith("[") for line in lines),
    }


def _rerun_command_fragment() -> None:
    st.rerun(scope="fragment")


def _handle_start_job(job_key: str) -> None:
    try:
        run_id = start_job(job_key)
        st.session_state["_selected_job_run_id"] = run_id
        st.session_state["_job_error"] = ""
    except RuntimeError as exc:
        st.session_state["_job_error"] = str(exc)
    _rerun_command_fragment()


def _resolve_visible_run() -> tuple[JobRun | None, JobRun | None]:
    current_active = active_run()
    selected_id = st.session_state.get("_selected_job_run_id")
    if current_active is not None:
        st.session_state["_selected_job_run_id"] = current_active.run_id
        return current_active, current_active
    selected_run = poll_run(selected_id) if selected_id else None
    if selected_run is not None:
        return None, selected_run
    latest = latest_run()
    if latest is not None:
        st.session_state["_selected_job_run_id"] = latest.run_id
    return None, latest


def _render_scenario_card(job: JobDefinition, disabled: bool) -> None:
    with st.container(border=True):
        st.markdown(f"**{job.label}**")
        st.caption(job.desc)
        step_labels = " → ".join(step.label for step in job.steps)
        st.caption(step_labels)
        if st.button("启动链路", key=f"start_{job.key}", disabled=disabled, width="stretch"):
            _handle_start_job(job.key)


def _render_advanced_jobs(disabled: bool) -> None:
    show_advanced = st.toggle("显示高级单步", value=False, key="_show_advanced_jobs")
    if not show_advanced:
        return
    jobs = advanced_jobs()
    for offset in range(0, len(jobs), 4):
        cols = st.columns(min(4, len(jobs) - offset))
        for col, job in zip(cols, jobs[offset:offset + 4]):
            with col:
                if st.button(job.label, key=f"start_{job.key}", disabled=disabled, help=job.desc, width="stretch"):
                    _handle_start_job(job.key)


def _render_run_status(run: JobRun | None, active: JobRun | None) -> None:
    st.markdown("### 任务状态")
    if run is None:
        st.info("暂无任务记录。选择上方场景链路或高级单步开始执行。")
        return

    done, total = _step_progress(run)
    status_text = _STATUS_TEXT.get(run.status, run.status)
    cols = st.columns(5)
    cols[0].metric("当前任务", run.data.get("job_label", run.job_key))
    cols[1].metric("状态", status_text)
    cols[2].metric("步骤进度", f"{done}/{total}")
    cols[3].metric("运行时长", _format_duration(run))
    cols[4].metric("退出码", "—" if run.exit_code is None else str(run.exit_code))

    if total:
        st.progress(done / total, text=f"{status_text} · 开始 {_format_dt(run.data.get('started_at'))}")

    if run.status == RUNNING:
        current = run.data.get("current_step")
        current_label = next((step.get("label") for step in run.steps if step.get("key") == current), None)
        st.info(f"正在执行：{current_label or '准备中'}")
    elif run.status == SUCCEEDED:
        st.success(f"最近完成：{run.data.get('job_label', run.job_key)}")
    elif run.status == FAILED:
        st.error(f"最近任务失败：{run.data.get('error') or '请查看日志定位原因'}")

    steps = pd.DataFrame(run.steps)
    if not steps.empty:
        display = steps[["label", "status", "started_at", "ended_at", "exit_code"]].copy()
        display["status"] = display["status"].map(lambda s: _STATUS_TEXT.get(s, s))
        display["started_at"] = display["started_at"].map(_format_dt)
        display["ended_at"] = display["ended_at"].map(_format_dt)
        display = display.rename(columns={
            "label": "步骤",
            "status": "状态",
            "started_at": "开始",
            "ended_at": "结束",
            "exit_code": "退出码",
        })
        st.dataframe(display, hide_index=True, width="stretch")

    if active is None and run.status in {SUCCEEDED, FAILED}:
        st.caption("任务已结束，控制台保持展开；可直接查看日志或启动下一条链路。")


def _render_log_console(run: JobRun | None) -> None:
    st.markdown("### 任务日志")
    if run is None:
        st.caption("暂无日志。")
        return

    show_full = st.toggle("查看全文", value=False, key=f"full_log_{run.run_id}")
    log_text = tail_log(run.run_id, lines=0 if show_full else 200)
    summary = _log_summary(log_text)
    cols = st.columns(5)
    cols[0].metric("展示行数", summary["lines"])
    cols[1].metric("INFO", summary["info"])
    cols[2].metric("Warnings", summary["warnings"])
    cols[3].metric("Errors", summary["errors"])
    cols[4].metric("JSON片段", summary["json_blocks"])

    full_log = tail_log(run.run_id, lines=0)
    st.download_button(
        "下载完整日志",
        data=full_log,
        file_name=f"{run.run_id}.log",
        mime="text/plain",
        key=f"download_{run.run_id}",
        disabled=not bool(full_log),
    )
    if log_text:
        st.code(log_text, language="text", line_numbers=False)
    else:
        st.caption("日志文件已创建，等待任务输出。")


@st.fragment(run_every="2s")
def show_command_panel():
    """生产机风格任务工作台：场景入口、状态恢复和自动日志刷新。"""
    st.subheader("任务工作台")
    active, visible_run = _resolve_visible_run()
    disabled = active is not None

    with st.container(border=True):
        error = st.session_state.get("_job_error")
        if error:
            st.warning(error)

        st.markdown("### 场景链路")
        cols = st.columns(3)
        for col, job in zip(cols, scenario_jobs()):
            with col:
                _render_scenario_card(job, disabled=disabled)

        st.divider()
        _render_advanced_jobs(disabled=disabled)
        st.caption(f"状态自动刷新：2 秒 · 最近刷新 {datetime.now().strftime('%H:%M:%S')}")

        st.divider()
        _render_run_status(visible_run, active)
        _render_log_console(visible_run)


# ---- 绩效跟踪 ----

def show_performance_tracking():
    """绩效跟踪：净值曲线 + 指标卡片 + 对比基准"""
    st.subheader("绩效跟踪")

    nav_data = query_df("""
        SELECT strategy_name, trade_date, nav, investment_nav, daily_return,
               total_value, net_contribution, drawdown
        FROM portfolio_nav ORDER BY strategy_name, trade_date
    """)

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
        y = sub["investment_nav"] if "investment_nav" in sub.columns else sub["nav"]
        fig.add_trace(go.Scatter(x=sub["trade_date"], y=y, mode="lines", name=s))

    if benchmark_choice != "无":
        bench_code = bench_map[benchmark_choice]
        bench_df = query_df("""
            SELECT trade_date, close FROM index_daily WHERE index_code = ? ORDER BY trade_date
        """, [bench_code])
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
        nav_col = "investment_nav" if "investment_nav" in sub.columns else "nav"
        final_nav = sub[nav_col].iloc[-1]
        metrics_rows.append({
            "策略": s, "最终净值": f"{final_nav:.3f}", "累计收益": f"{(final_nav-1)*100:+.1f}%",
            "年化收益": f"{ann_ret*100:.1f}%", "年化波动": f"{ann_vol*100:.1f}%",
            "夏普": f"{sharpe:.2f}", "最大回撤": f"{max_dd*100:.1f}%",
        })

    if metrics_rows:
        st.dataframe(pd.DataFrame(metrics_rows), hide_index=True, width="stretch")

    reviews = query_df("""
        SELECT period_type, start_date, end_date, nav_return, benchmark_code,
               benchmark_return, excess_return, max_drawdown, net_flow
        FROM performance_reviews
        QUALIFY ROW_NUMBER() OVER (PARTITION BY period_type ORDER BY created_at DESC) = 1
        ORDER BY period_type
    """)
    if not reviews.empty:
        st.markdown("### 阶段评估")
        display = reviews.copy()
        for col in ["nav_return", "benchmark_return", "excess_return", "max_drawdown"]:
            display[col] = (display[col] * 100).round(2).astype(str) + "%"
        st.dataframe(display, hide_index=True, width="stretch")

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
    latest_pos = query_df("""
        SELECT strategy_name, trade_date, symbol, quantity, avg_cost, current_price,
               market_value, pnl, pnl_pct, weight
        FROM paper_positions
        WHERE (strategy_name, trade_date) IN (
            SELECT strategy_name, MAX(trade_date) FROM paper_positions GROUP BY strategy_name
        )
        AND quantity > 0
        ORDER BY strategy_name, weight DESC
    """)
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
