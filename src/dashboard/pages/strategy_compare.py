"""策略对比页面 — 多策略回测结果横向比较 + 最近选股信号预览 + 命令执行"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.db import get_conn


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

import threading
from io import StringIO

COMMANDS = {
    "update": {"label": "📡 更新行情数据", "desc": "增量拉取最新交易日数据"},
    "generate_signals": {"label": "🎯 生成调仓信号", "desc": "汇总所有策略信号输出到数据库"},
}


def _do_update(output: list):
    """同进程线程执行更新（共享 DuckDB 配置，零锁冲突）"""
    from loguru import logger as _loguru
    buf = StringIO()
    sid = _loguru.add(buf, format="{time} | {level} | {message}", level="INFO")
    try:
        from src.data_pipeline.main import update_all, _load_config, _default_dates, _default_dates_yf
        from src.data_pipeline.loader import get_connection, get_last_trade_date, upsert_daily_price, upsert_index_daily
        from src.data_pipeline.fetchers import yfinance_fetcher as yf
        from src.data_pipeline.fetchers import akshare_fetcher as ak
        from datetime import date, timedelta

        config = _load_config()
        conn = get_connection()
        try:
            _loguru.info("=== 增量更新 ===")
            start_yf, end_yf = _default_dates_yf(config)
            cn = conn.execute("SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchall()
            hk = conn.execute("SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')").fetchall()
            cn_syms, hk_syms = [r[0] for r in cn], [r[0] for r in hk]
            today = date.today()
            _loguru.info(f"Updating CN={len(cn_syms)} HK={len(hk_syms)}")

            for sym in cn_syms:
                last = get_last_trade_date(conn, sym)
                if last and (today - last).days < 1:
                    continue
                fs = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else (today - timedelta(days=365*5)).strftime("%Y-%m-%d")
                try:
                    df = yf.fetch_cn_daily(sym, fs, end_yf)
                    if not df.empty:
                        upsert_daily_price(conn, df)
                except Exception:
                    pass

            for sym in hk_syms:
                last = get_last_trade_date(conn, sym)
                if last and (today - last).days < 1:
                    continue
                fs = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else (today - timedelta(days=365*5)).strftime("%Y-%m-%d")
                try:
                    df = yf.fetch_hk_daily(sym, fs, end_yf)
                    if not df.empty:
                        upsert_daily_price(conn, df)
                except Exception:
                    pass

            for ycode in ["^HSI", "3032.HK"]:
                try:
                    df = yf.fetch_hk_index_daily(ycode, (today - timedelta(days=30)).strftime("%Y-%m-%d"), end_yf)
                    if not df.empty:
                        upsert_index_daily(conn, df)
                except Exception:
                    pass
            _loguru.info("=== 增量更新完成 ===")
        finally:
            conn.close()
    except Exception:
        import traceback
        buf.write(traceback.format_exc())
    finally:
        _loguru.remove(sid)
        output.append(buf.getvalue())


def _do_generate_signals(output: list):
    """同进程线程生成信号"""
    from loguru import logger as _loguru
    buf = StringIO()
    sid = _loguru.add(buf, format="{time} | {level} | {message}", level="INFO")
    try:
        from src.signals.generator import generate_all
        df = generate_all()
        if df is not None and not df.empty:
            _loguru.info(f"Generated {len(df)} signals")
        else:
            _loguru.info("No signals (need price data and strategy run first)")
    except Exception:
        import traceback
        buf.write(traceback.format_exc())
    finally:
        _loguru.remove(sid)
        output.append(buf.getvalue())


def _start_command(key: str):
    """启动同进程线程执行命令"""

    def _runner():
        output = []
        if key == "update":
            _do_update(output)
        elif key == "generate_signals":
            _do_generate_signals(output)
        st.session_state[f"{key}_output"] = output[0] if output else "(no output)"
        st.session_state[f"{key}_exit_code"] = 0 if output else 1
        st.session_state[f"{key}_running"] = False
        st.session_state["_cmd_running"] = False

    st.session_state["_cmd_running"] = True
    st.session_state[f"{key}_running"] = True
    st.session_state[f"{key}_output"] = "⏳ 线程启动中..."
    st.session_state[f"{key}_exit_code"] = None
    threading.Thread(target=_runner, daemon=True).start()


def show_command_panel():
    """命令执行面板"""
    st.subheader("🔧 命令执行")

    for key in COMMANDS:
        if f"{key}_running" not in st.session_state:
            st.session_state[f"{key}_running"] = False
        if f"{key}_output" not in st.session_state:
            st.session_state[f"{key}_output"] = ""
        if f"{key}_exit_code" not in st.session_state:
            st.session_state[f"{key}_exit_code"] = None
    if "_cmd_running" not in st.session_state:
        st.session_state["_cmd_running"] = False

    with st.expander("展开命令面板", expanded=st.session_state.get("_cmd_running", False)):
        st.caption("点击按钮执行（同进程线程，无锁冲突）。点击「刷新状态」查看输出。")

        cols = st.columns(len(COMMANDS))
        for i, (key, info) in enumerate(COMMANDS.items()):
            with cols[i]:
                disabled = st.session_state.get(f"{key}_running", False)
                exit_code = st.session_state.get(f"{key}_exit_code")

                if st.button(info["label"], key=f"btn_{key}", disabled=disabled,
                             help=info["desc"], width="stretch"):
                    _start_command(key)

                if disabled:
                    st.info("⏳ 执行中...")
                elif exit_code == 0:
                    st.success("✅ 成功")
                elif exit_code is not None:
                    st.error("❌ 失败 (退出码: {})".format(exit_code))
                else:
                    st.caption("⚪ 就绪")

        st.divider()
        from datetime import datetime

        refresh_col, status_col = st.columns([1, 3])
        with refresh_col:
            if st.button("🔄 刷新状态", key="btn_refresh_status", width="stretch"):
                pass  # button click causes natural rerun

        # 每条命令的执行状态摘要
        parts = []
        for key in COMMANDS:
            running = st.session_state.get(f"{key}_running", False)
            exit_code = st.session_state.get(f"{key}_exit_code")
            if running:
                parts.append(f"{COMMANDS[key]['label']}: 执行中")
            elif exit_code == 0:
                parts.append(f"{COMMANDS[key]['label']}: 成功")
            elif exit_code is not None:
                parts.append(f"{COMMANDS[key]['label']}: 失败")
        with status_col:
            st.caption(f"上次刷新: {datetime.now().strftime('%H:%M:%S')} | {' | '.join(parts) if parts else '空闲'}")

        st.markdown("**命令输出**")
        output_tabs = st.tabs([info["label"] for info in COMMANDS.values()])
        for tab, (key, info) in zip(output_tabs, COMMANDS.items()):
            with tab:
                out = st.session_state.get(f"{key}_output", "")
                exit_code = st.session_state.get(f"{key}_exit_code")
                running = st.session_state.get(f"{key}_running", False)
                if running:
                    st.warning(f"⏳ 执行中... | 刷新时间: {datetime.now().strftime('%H:%M:%S')}")
                elif exit_code == 0:
                    st.success("✅ 执行成功")
                elif exit_code is not None:
                    st.error(f"❌ 退出码: {exit_code}")
                if out:
                    st.code(out, language="text", line_numbers=False)


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
    show_backtest_results()
    st.divider()
    show_signal_preview()
    st.divider()
    show_factor_importance()

with tab_performance:
    show_performance_tracking()
