"""Qlib 分析页 — 实验状态、双轨评估、IC 分析、预测截面、模型发布。"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.backtest.qlib_runner import _publish_gate_thresholds
from src.config import PROJECT_ROOT
from src.dashboard.db import DuckDBError, db_error_widget, get_conn, query_df, query_one
from src.dashboard.qlib_report_service import (
    add_rolling_ic_columns,
    load_experiment_report,
    parse_json_dict,
    prepare_experiment_frame,
)
from src.dashboard.qlib_rule_pk_service import (
    evaluate_ab_tracking,
    load_rule_qlib_pk,
    record_ab_snapshot,
    resolve_champion_experiment,
)


def _qlib_installed() -> bool:
    return importlib.util.find_spec("qlib") is not None


def _qlib_data_ready() -> bool:
    root = PROJECT_ROOT / "qlib_data" / "cn_data"
    return (
        (root / "calendars" / "day.txt").exists()
        and (root / "instruments").exists()
        and (root / "features").exists()
    )


def _pct(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.2f}%"


def _num(value, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}"


def _rank(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.0f}"


def _format_report_table(df: pd.DataFrame):
    display = df.copy()
    for col in [
        "annual_return", "benchmark_return", "excess_return", "max_drawdown",
        "rank_ic_positive_rate", "avg_cash_drag", "max_actual_position_pct",
        "max_position_pct",
    ]:
        if col in display:
            display[col] = display[col].map(_pct)
    for col in ["sharpe_ratio", "ic_mean", "icir", "turnover", "avg_selected_count"]:
        if col in display:
            display[col] = display[col].map(lambda x: _num(x, 3))
    if "duration_seconds" in display:
        display["duration_seconds"] = display["duration_seconds"].map(
            lambda x: "—" if pd.isna(x) else f"{float(x) / 60:.1f} 分钟"
        )
    if "is_final_selected" in display or "is_candidate_selected" in display:
        def _badge(row):
            badges = []
            if bool(row.get("is_final_selected")):
                badges.append("实验最优")
            if bool(row.get("is_candidate_selected")):
                badges.append("候选最优")
            return " / ".join(badges)

        display["highlight_badge"] = display.apply(_badge, axis=1)
        display = display.drop(columns=["is_final_selected", "is_candidate_selected"], errors="ignore")

    display = display.rename(columns={
        "highlight_badge": "高亮",
        "winning_metrics": "胜出指标",
        "highlight_reason": "高亮原因",
        "experiment_id": "实验ID",
        "candidate_id": "候选ID",
        "candidate_variant": "参数变体",
        "mode": "模式",
        "status": "状态",
        "verdict": "结论",
        "test_start": "测试开始",
        "test_end": "测试结束",
        "annual_return": "年化收益",
        "benchmark_return": "主基准年化",
        "excess_return": "主基准超额",
        "sharpe_ratio": "夏普",
        "max_drawdown": "最大回撤",
        "ic_mean": "IC均值",
        "icir": "ICIR",
        "rank_ic_positive_rate": "RankIC正占比",
        "turnover": "换手",
        "primary_benchmark": "主基准",
        "constraint_profile": "执行约束",
        "account_capital": "账户资金",
        "avg_selected_count": "平均可持仓数",
        "avg_cash_drag": "现金拖累",
        "max_position_pct": "单票上限",
        "max_actual_position_pct": "最大单票占比",
        "duration_seconds": "耗时",
        "error_message": "错误",
    })
    return display.style.apply(_style_report_table_row, axis=1)


def _style_report_table_row(row: pd.Series) -> list[str]:
    styles = [""] * len(row)
    badge = str(row.get("高亮") or "")
    is_final = "实验最优" in badge
    is_candidate = "候选最优" in badge
    winners = str(row.get("胜出指标") or "")
    for idx, col in enumerate(row.index):
        if is_final:
            styles[idx] = "background-color: #dcfce7; color: #14532d; font-weight: 600;"
        elif is_candidate:
            styles[idx] = "background-color: #dbeafe; color: #1e3a8a; font-weight: 600;"
        if col == "胜出指标" and winners:
            styles[idx] = "background-color: #fef3c7; color: #78350f; font-weight: 600;"
        if col == "高亮" and is_final:
            styles[idx] = "background-color: #16a34a; color: white; font-weight: 700;"
        elif col == "高亮" and is_candidate:
            styles[idx] = "background-color: #2563eb; color: white; font-weight: 700;"
        if col in winners:
            styles[idx] = "background-color: #fef9c3; color: #713f12; font-weight: 700;"
    return styles


def _load_experiments() -> pd.DataFrame:
    df = query_df("""
        SELECT experiment_id, run_id, model_name, model_version, mode, status,
               train_start, train_end, valid_start, valid_end, test_start, test_end,
               data_start, data_end, data_symbols, qlib_installed, qlib_data_ready,
               qlib_version, lightgbm_version, config_snapshot, metrics_json, error_message,
               started_at, ended_at
        FROM qlib_experiments
        ORDER BY started_at DESC
    """)
    if df.empty:
        return df
    return prepare_experiment_frame(df)


def _load_report() -> dict:
    conn = get_conn()
    try:
        return load_experiment_report(conn)
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def _load_benchmark_nav() -> pd.DataFrame:
    index_df = query_df("""
        SELECT index_code, trade_date, close
        FROM index_daily
        WHERE index_code IN ('000300', '000905')
        ORDER BY trade_date, index_code
    """)
    all_df = query_df("""
        SELECT dp.trade_date, dp.symbol, dp.close
        FROM daily_price dp
        JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country = 'CN'
        ORDER BY dp.trade_date, dp.symbol
    """)
    series = {}
    if not index_df.empty:
        index_df["trade_date"] = pd.to_datetime(index_df["trade_date"])
        for code, name in [("000300", "沪深300"), ("000905", "中证500")]:
            sub = index_df[index_df["index_code"] == code].set_index("trade_date")["close"].dropna().sort_index()
            if not sub.empty:
                series[name] = sub / sub.iloc[0]
    if not all_df.empty:
        all_df["trade_date"] = pd.to_datetime(all_df["trade_date"])
        close = all_df.pivot(index="trade_date", columns="symbol", values="close").sort_index()
        all_ret = close.pct_change(fill_method=None).mean(axis=1).dropna()
        all_nav = (1 + all_ret).cumprod()
        if not all_nav.empty:
            series["全A等权代理"] = all_nav / all_nav.iloc[0]

    if {"沪深300", "中证500", "全A等权代理"}.issubset(series):
        common = series["沪深300"].index.intersection(series["中证500"].index).intersection(series["全A等权代理"].index)
        mixed_ret = pd.concat([
            series["沪深300"].loc[common].pct_change(fill_method=None),
            series["中证500"].loc[common].pct_change(fill_method=None),
            series["全A等权代理"].loc[common].pct_change(fill_method=None),
        ], axis=1).mean(axis=1).dropna()
        mixed_nav = (1 + mixed_ret).cumprod()
        if not mixed_nav.empty:
            series["混合基准"] = mixed_nav / mixed_nav.iloc[0]

    rows = []
    for name, nav in series.items():
        for dt, value in nav.dropna().items():
            rows.append({"trade_date": dt, "benchmark": name, "nav": float(value)})
    return pd.DataFrame(rows)


def show_benchmark_reference():
    st.markdown("### 基准参考")
    df = _load_benchmark_nav()
    if df.empty:
        st.info("暂无可展示的基准数据。")
        return

    min_dt = df["trade_date"].min().date()
    max_dt = df["trade_date"].max().date()
    selected_range = st.date_input(
        "参考区间（默认全量；可改成实验测试窗口）",
        value=(min_dt, max_dt),
        min_value=min_dt,
        max_value=max_dt,
        key="qlib_benchmark_range",
    )
    if isinstance(selected_range, tuple):
        start = selected_range[0]
        end = selected_range[1] if len(selected_range) > 1 else selected_range[0]
    else:
        start = selected_range
        end = selected_range

    sub = df[(df["trade_date"].dt.date >= start) & (df["trade_date"].dt.date <= end)].copy()
    if sub.empty:
        st.info("当前区间没有基准数据。")
        return
    sub["nav"] = sub.groupby("benchmark")["nav"].transform(lambda s: s / s.iloc[0])

    fig = go.Figure()
    colors = {
        "沪深300": "#2563eb",
        "中证500": "#16a34a",
        "全A等权代理": "#f97316",
        "混合基准": "#111827",
    }
    for name in ["沪深300", "中证500", "全A等权代理", "混合基准"]:
        part = sub[sub["benchmark"] == name]
        if part.empty:
            continue
        fig.add_trace(go.Scatter(
            x=part["trade_date"],
            y=part["nav"],
            mode="lines",
            name=name,
            line=dict(color=colors.get(name), width=3 if name == "混合基准" else 2),
        ))
    fig.update_layout(
        height=380,
        xaxis_title=None,
        yaxis_title="归一化净值",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")

    summary_rows = []
    for name, part in sub.groupby("benchmark"):
        nav = part.sort_values("trade_date")["nav"]
        rets = nav.pct_change(fill_method=None).dropna()
        peak = nav.expanding().max()
        drawdown = (nav - peak) / peak
        years = max(len(rets) / 252, 1 / 252)
        annual = nav.iloc[-1] ** (1 / years) - 1 if len(nav) > 1 else 0
        vol = rets.std() * (252 ** 0.5) if len(rets) > 1 else 0
        summary_rows.append({
            "基准": name,
            "累计收益": nav.iloc[-1] - 1,
            "年化收益": annual,
            "年化波动": vol,
            "最大回撤": drawdown.min(),
        })
    summary = pd.DataFrame(summary_rows)
    for col in ["累计收益", "年化收益", "年化波动", "最大回撤"]:
        summary[col] = summary[col].map(_pct)
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption(
        "全A等权代理使用当前本地 708 只 A股日收益等权计算；混合基准为沪深300、中证500、全A等权代理三者等权。"
        "这里是可调参考区间，实验表里的主基准年化/超额会按每个实验自己的测试窗口和交易频率重新对齐计算。"
    )


def show_runtime_status():
    st.subheader("运行状态")
    data_stats = query_one("""
        SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT symbol), COUNT(*)
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
    """)
    latest_exp = query_df("""
        SELECT experiment_id, mode, status, model_version, error_message, started_at, ended_at
        FROM qlib_experiments
        ORDER BY started_at DESC
        LIMIT 1
    """)
    prod = query_df("""
        SELECT model_version, experiment_id, status, published_at
        FROM qlib_model_registry
        WHERE model_name = 'alpha158' AND status = 'production'
        ORDER BY published_at DESC NULLS LAST, created_at DESC
        LIMIT 1
    """)
    latest_pred = query_one("""
        SELECT MAX(prediction_date) FROM qlib_predictions
    """)[0]

    cols = st.columns(5)
    cols[0].metric("Qlib 依赖", "可用" if _qlib_installed() else "缺失")
    cols[1].metric("Qlib 数据", "Ready" if _qlib_data_ready() else "未就绪")
    cols[2].metric("A股行情截止", str(data_stats[1]) if data_stats else "—")
    cols[3].metric("Production 模型", prod.iloc[0]["model_version"] if not prod.empty else "未发布")
    cols[4].metric("最近预测日", str(latest_pred) if latest_pred else "—")

    if not latest_exp.empty:
        row = latest_exp.iloc[0]
        status_fn = st.success if row["status"] == "SUCCEEDED" else st.error if row["status"] == "FAILED" else st.info
        status_fn(f"最近实验：{row['experiment_id']} / {row['mode']} / {row['status']}")
        if row.get("error_message"):
            st.code(row["error_message"], language="text")
    else:
        st.info("暂无 Qlib 实验记录。运行 `python -m src.backtest.qlib_runner run-experiment --mode fixed` 后可见。")

    st.caption(
        f"本地 A股日线覆盖：{data_stats[0]} ~ {data_stats[1]}，"
        f"{data_stats[2]} 只股票，{data_stats[3]:,} 行。"
    )


def show_experiment_compare():
    st.subheader("实验对比")
    show_benchmark_reference()
    st.divider()
    df = _load_experiments()
    if df.empty:
        st.info("暂无实验记录。固定切分和 walk-forward 运行完成后，这里会展示双轨结论。")
        return

    display = df[[
        "experiment_id", "mode", "status", "model_version", "test_start", "test_end",
        "annual_return", "benchmark_return", "excess_return", "sharpe_ratio", "max_drawdown",
        "ic_mean", "icir", "rank_ic_positive_rate", "turnover", "primary_benchmark", "error_message",
    ]].copy()
    for col in ["annual_return", "benchmark_return", "excess_return", "max_drawdown", "rank_ic_positive_rate"]:
        display[col] = display[col].map(_pct)
    for col in ["sharpe_ratio", "ic_mean", "icir", "turnover"]:
        display[col] = display[col].map(lambda x: _num(x, 3))
    st.dataframe(display, hide_index=True, width="stretch")

    ok = df[df["status"] == "SUCCEEDED"].copy()
    if ok.empty:
        st.warning("目前没有成功实验。优先检查 Qlib 依赖和 qlib_data/cn_data 是否 ready。")
        return

    fig = go.Figure()
    for metric in ["annual_return", "ic_mean", "sharpe_ratio", "max_drawdown"]:
        fig.add_trace(go.Bar(name=metric, x=ok["mode"] + " / " + ok["model_version"].fillna(""), y=ok[metric]))
    fig.update_layout(height=420, barmode="group", xaxis_title=None, yaxis_title="指标值")
    st.plotly_chart(fig, width="stretch")

    grid = query_df("""
        SELECT source_experiment_id, mode, top_n, holding_days, rebalance_freq,
               buffer_n, benchmark_name, constraint_profile, account_capital,
               avg_selected_count, avg_cash_drag, max_actual_position_pct,
               annual_return, sharpe_ratio,
               max_drawdown, turnover, benchmark_return, excess_return
        FROM qlib_grid_results
        ORDER BY
            CASE benchmark_name WHEN 'MIXED_EQUAL' THEN 0 WHEN '000300' THEN 1 WHEN '000905' THEN 2 ELSE 3 END,
            excess_return DESC NULLS LAST,
            sharpe_ratio DESC NULLS LAST
        LIMIT 300
    """)
    if not grid.empty:
        st.markdown("### Top-N / 持仓周期 / 调仓频率网格")
        bench = st.selectbox(
            "基准",
            ["MIXED_EQUAL", "000300", "000905", "ALL_EQ_PROXY"],
            index=0,
            key="qlib_grid_benchmark",
        )
        sub = grid[grid["benchmark_name"] == bench].copy()
        for col in ["annual_return", "max_drawdown", "benchmark_return", "excess_return", "avg_cash_drag", "max_actual_position_pct"]:
            sub[col] = sub[col].map(_pct)
        for col in ["sharpe_ratio", "turnover", "avg_selected_count"]:
            sub[col] = sub[col].map(lambda x: _num(x, 3))
        st.dataframe(sub.head(80), hide_index=True, width="stretch")


def show_experiment_report():
    st.subheader("实验对比报告")
    report = _load_report()
    experiments = report["experiments"]
    if experiments.empty:
        st.info("暂无 Qlib 实验记录。先运行 fixed、walk-forward 或候选批跑后，这里会生成结构化报告。")
        return

    status_options = sorted(experiments["status"].dropna().unique().tolist())
    mode_options = sorted(experiments["mode"].dropna().unique().tolist())
    c1, c2, c3 = st.columns([1, 1, 2])
    selected_status = c1.multiselect("状态", status_options, default=status_options, key="qlib_report_status")
    selected_modes = c2.multiselect("模式", mode_options, default=mode_options, key="qlib_report_mode")
    keyword = c3.text_input("实验 / 候选筛选", key="qlib_report_keyword")

    filtered = experiments[
        experiments["status"].isin(selected_status)
        & experiments["mode"].isin(selected_modes)
    ].copy()
    if keyword:
        needle = keyword.strip().lower()
        mask = (
            filtered["experiment_id"].astype(str).str.lower().str.contains(needle, na=False)
            | filtered["model_version"].astype(str).str.lower().str.contains(needle, na=False)
            | filtered["candidate_id"].astype(str).str.lower().str.contains(needle, na=False)
            | filtered["candidate_variant"].astype(str).str.lower().str.contains(needle, na=False)
        )
        filtered = filtered[mask]

    summary = report["summary"]
    best_exp = summary.get("best_experiment")
    best_grid = summary.get("best_grid")
    best_candidate = summary.get("best_candidate")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("实验总数", summary["experiment_count"])
    m2.metric("成功实验", summary["succeeded_count"])
    m3.metric("失败实验", summary["failed_count"])
    m4.metric("当前筛选", len(filtered))

    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown("#### 最优实验")
        if best_exp:
            st.metric("超额收益", _pct(best_exp.get("excess_return")))
            st.caption(f"{best_exp.get('experiment_id')} / {best_exp.get('mode')} / {best_exp.get('verdict')}")
        else:
            st.info("暂无成功实验。")
    with k2:
        st.markdown("#### 最优网格")
        if best_grid:
            label = f"Top{best_grid.get('top_n')} / T+{best_grid.get('holding_days')} / {best_grid.get('rebalance_freq')}"
            st.metric(label, _num(best_grid.get("selection_score"), 3))
            st.caption(f"超额 {_pct(best_grid.get('excess_return'))}，换手 {_num(best_grid.get('turnover'), 2)}")
        else:
            st.info("暂无网格评估。")
    with k3:
        st.markdown("#### 最优候选")
        if best_candidate:
            label = f"{best_candidate.get('candidate_id')} / {best_candidate.get('model_variant')}"
            st.metric(label, _num(best_candidate.get("score"), 3))
            st.caption(
                f"Top{best_candidate.get('best_top_n')} / T+{best_candidate.get('best_holding_days')} / "
                f"{best_candidate.get('best_rebalance_freq')}"
            )
        else:
            st.info("暂无候选批跑结果。")

    st.markdown("### 高亮标准")
    standards = pd.DataFrame(report.get("highlight_standards", []))
    if not standards.empty:
        standards = pd.concat([
            pd.DataFrame([{
                "metric": "final_selected",
                "label": "最终选出实验",
                "winner_rule": "综合排序",
                "highlight_standard": "仅在成功实验中选择：先看主基准超额收益，再看 ICIR，最后看夏普；同时结合结论门槛检查。",
            }]),
            standards,
        ], ignore_index=True)
        standards = standards.rename(columns={
            "label": "指标",
            "winner_rule": "胜出规则",
            "highlight_standard": "高亮标准",
        })[["指标", "胜出规则", "高亮标准"]]
        st.table(standards)
        st.caption("实验数据表中：绿色行表示最终选出的实验；黄色单元格表示该实验在对应指标上胜出。")

    ok = filtered[filtered["status"] == "SUCCEEDED"].copy()
    if not ok.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ok["excess_return"],
            y=ok["icir"],
            mode="markers+text",
            text=ok["mode"].astype(str),
            textposition="top center",
            marker=dict(
                size=ok["turnover"].fillna(0).clip(lower=1, upper=220) / 8 + 8,
                color=ok["max_drawdown"],
                colorscale="RdYlGn",
                reversescale=True,
                showscale=True,
                colorbar=dict(title="最大回撤"),
                line=dict(width=1, color="#111827"),
            ),
            customdata=ok[["experiment_id", "candidate_id", "annual_return", "sharpe_ratio", "turnover"]],
            hovertemplate=(
                "实验=%{customdata[0]}<br>"
                "候选=%{customdata[1]}<br>"
                "超额=%{x:.2%}<br>"
                "ICIR=%{y:.3f}<br>"
                "年化=%{customdata[2]:.2%}<br>"
                "夏普=%{customdata[3]:.3f}<br>"
                "换手=%{customdata[4]:.2f}<extra></extra>"
            ),
        ))
        fig.add_hline(y=0, line_dash="dot", line_color="#9ca3af")
        fig.add_vline(x=0, line_dash="dot", line_color="#9ca3af")
        fig.update_layout(
            height=420,
            xaxis_title="相对主基准超额收益",
            yaxis_title="ICIR",
            hovermode="closest",
        )
        st.plotly_chart(fig, width="stretch")

    table_cols = [
        "is_final_selected", "is_candidate_selected", "winning_metrics", "highlight_reason",
        "experiment_id", "candidate_id", "candidate_variant", "mode", "status", "verdict",
        "test_start", "test_end", "annual_return", "benchmark_return", "excess_return", "sharpe_ratio",
        "max_drawdown", "ic_mean", "icir", "rank_ic_positive_rate", "turnover",
        "primary_benchmark", "duration_seconds", "error_message",
    ]
    display = filtered[[col for col in table_cols if col in filtered.columns]].copy()
    st.dataframe(_format_report_table(display), hide_index=True, width="stretch")

    benchmarks = report["benchmarks"]
    if not benchmarks.empty and not filtered.empty:
        st.markdown("### 多基准超额矩阵")
        visible_ids = set(filtered["experiment_id"].astype(str))
        bench = benchmarks[benchmarks["experiment_id"].astype(str).isin(visible_ids)].copy()
        if not bench.empty:
            pivot = bench.pivot_table(
                index="experiment_id",
                columns="benchmark_name",
                values="excess_return",
                aggfunc="last",
            )
            fig = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=pivot.columns,
                y=pivot.index,
                colorscale="RdYlGn",
                zmid=0,
                hovertemplate="实验=%{y}<br>基准=%{x}<br>超额=%{z:.2%}<extra></extra>",
            ))
            fig.update_layout(height=max(260, min(620, 120 + 26 * len(pivot))), xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig, width="stretch")
            bench_display = bench.copy()
            for col in ["benchmark_return", "excess_return"]:
                bench_display[col] = bench_display[col].map(_pct)
            bench_display["info_ratio"] = bench_display["info_ratio"].map(lambda x: _num(x, 3))
            st.dataframe(bench_display, hide_index=True, width="stretch")

    grid_best = report["grid_best"]
    if not grid_best.empty and not filtered.empty:
        st.markdown("### 网格最优组合")
        visible_ids = set(filtered["experiment_id"].astype(str))
        grid_display = grid_best[grid_best["source_experiment_id"].astype(str).isin(visible_ids)].copy()
        if not grid_display.empty:
            grid_display = grid_display.sort_values(
                ["benchmark_name", "selection_score", "excess_return"],
                ascending=[True, False, False],
                na_position="last",
            )
            for col in ["annual_return", "max_drawdown", "benchmark_return", "excess_return"]:
                grid_display[col] = grid_display[col].map(_pct)
            for col in ["avg_cash_drag", "max_actual_position_pct"]:
                if col in grid_display:
                    grid_display[col] = grid_display[col].map(_pct)
            for col in ["sharpe_ratio", "turnover", "selection_score", "avg_selected_count"]:
                grid_display[col] = grid_display[col].map(lambda x: _num(x, 3))
            st.dataframe(grid_display[[
                "source_experiment_id", "benchmark_name", "top_n", "holding_days",
                "rebalance_freq", "buffer_n", "constraint_profile", "account_capital",
                "avg_selected_count", "avg_cash_drag", "max_actual_position_pct",
                "selection_score", "annual_return",
                "sharpe_ratio", "max_drawdown", "turnover", "benchmark_return", "excess_return",
            ]], hide_index=True, width="stretch")

    candidates = report["candidate_results"]
    if not candidates.empty:
        st.markdown("### 候选批跑结果")
        cand = candidates.copy()
        for col in ["annual_return", "max_drawdown", "benchmark_return", "excess_return", "avg_cash_drag", "max_actual_position_pct"]:
            cand[col] = cand[col].map(_pct)
        for col in ["sharpe_ratio", "turnover", "ic_mean", "icir", "rank_ic_mean", "rank_ic_positive_rate", "score", "avg_selected_count"]:
            cand[col] = cand[col].map(lambda x: _num(x, 3))
        st.dataframe(cand[[
            "batch_id", "candidate_id", "model_variant", "status", "experiment_id",
            "best_benchmark", "best_top_n", "best_holding_days", "best_rebalance_freq",
            "best_constraint_profile", "best_account_capital", "avg_selected_count",
            "avg_cash_drag", "max_actual_position_pct", "score",
            "annual_return", "sharpe_ratio", "max_drawdown", "turnover",
            "excess_return", "ic_mean", "icir", "error_message",
        ]].head(80), hide_index=True, width="stretch")

    glossary = pd.DataFrame(report.get("metric_glossary", []))
    if not glossary.empty:
        st.markdown("### 指标备注")
        glossary = glossary.rename(columns={
            "label": "指标",
            "meaning": "指标含义",
            "plain_explanation": "通俗解释",
            "watch_out": "阅读备注",
        })[["指标", "指标含义", "通俗解释", "阅读备注"]]
        st.table(glossary)


def show_ic_analysis():
    st.subheader("因子/IC 分析")
    experiments = _load_experiments()
    ok_ids = experiments.loc[experiments["status"] == "SUCCEEDED", "experiment_id"].tolist() if not experiments.empty else []
    if not ok_ids:
        st.info("暂无成功实验的 IC 明细。")
        return
    selected = st.selectbox("实验", ok_ids, index=0)
    daily = query_df("""
        SELECT metric_date, ic, rank_ic, top_return, bottom_return, spread_return,
               portfolio_return, benchmark_return, turnover
        FROM qlib_daily_metrics
        WHERE experiment_id = ?
        ORDER BY metric_date
    """, [selected])
    if daily.empty:
        st.info("该实验暂无日度 IC 明细。")
        return
    daily = add_rolling_ic_columns(daily)
    daily["cum_rank_ic"] = daily["rank_ic"].fillna(0).cumsum()

    latest = daily.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Rank IC 30日", _num(latest.get("rank_ic_ma30"), 4))
    c2.metric("Rank IC 60日", _num(latest.get("rank_ic_ma60"), 4))
    c3.metric("Rank IC 180日", _num(latest.get("rank_ic_ma180"), 4))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["metric_date"], y=daily["rank_ic"], mode="lines", name="Rank IC"))
    for window in (30, 60, 180):
        col = f"rank_ic_ma{window}"
        if col in daily.columns:
            fig.add_trace(go.Scatter(
                x=daily["metric_date"],
                y=daily[col],
                mode="lines",
                name=f"Rank IC {window}日均值",
            ))
    fig.add_trace(go.Scatter(x=daily["metric_date"], y=daily["cum_rank_ic"], mode="lines", name="累计 Rank IC", yaxis="y2"))
    fig.update_layout(
        height=360,
        xaxis_title=None,
        yaxis_title="Rank IC",
        yaxis2=dict(title="累计 Rank IC", overlaying="y", side="right"),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=daily["metric_date"], y=daily["top_return"] * 100, mode="lines", name="Top 20%"))
    fig2.add_trace(go.Scatter(x=daily["metric_date"], y=daily["bottom_return"] * 100, mode="lines", name="Bottom 20%"))
    fig2.add_trace(go.Scatter(x=daily["metric_date"], y=daily["spread_return"] * 100, mode="lines", name="Spread"))
    fig2.update_layout(height=320, yaxis_title="前瞻收益 (%)", xaxis_title=None, hovermode="x unified")
    st.plotly_chart(fig2, width="stretch")


def show_predictions():
    st.subheader("选股预测")
    latest = query_one("SELECT MAX(prediction_date) FROM qlib_predictions")[0]
    if latest is None:
        st.info("暂无 Qlib 预测截面。研究实验成功后会先落入 qlib_predictions；发布后才会写入 signals。")
        return
    df = query_df("""
        WITH latest_pos AS (
            SELECT symbol, SUM(quantity) AS quantity
            FROM paper_positions
            WHERE trade_date = (SELECT MAX(trade_date) FROM paper_positions)
            GROUP BY symbol
        ),
        latest_sig AS (
            SELECT symbol, status, executed
            FROM signals
            WHERE model_name = 'alpha158'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY signal_ts DESC) = 1
        )
        SELECT p.prediction_date, p.experiment_id, p.model_version,
               p.symbol, si.name, si.industry, p.score, p.rank,
               p.confidence, p.selected,
               COALESCE(lp.quantity, 0) AS held_qty,
               ls.status AS signal_status, ls.executed AS signal_executed
        FROM qlib_predictions p
        LEFT JOIN stock_info si ON p.symbol = si.symbol
        LEFT JOIN latest_pos lp ON p.symbol = lp.symbol
        LEFT JOIN latest_sig ls ON p.symbol = ls.symbol
        WHERE p.prediction_date = ?
        ORDER BY p.rank
        LIMIT 100
    """, [latest])
    if df.empty:
        st.info("暂无最新预测明细。")
        return
    display = df.copy()
    display["confidence"] = display["confidence"].map(lambda x: _num(x, 2))
    display["score"] = display["score"].map(lambda x: _num(x, 4))
    display = display.rename(columns={
        "prediction_date": "预测日",
        "experiment_id": "实验ID",
        "model_version": "模型版本",
        "symbol": "代码",
        "name": "名称",
        "industry": "行业",
        "score": "分数",
        "rank": "排名",
        "confidence": "置信度",
        "selected": "入选TopN",
        "held_qty": "持仓数量",
        "signal_status": "信号状态",
        "signal_executed": "信号已执行",
    })
    st.dataframe(display, hide_index=True, width="stretch")


def show_rule_qlib_pk():
    st.subheader("规则 vs Qlib PK")
    st.caption("只做研究对比，不写入 signals，也不会触发纸交易。")

    conn = get_conn()
    try:
        champion = resolve_champion_experiment(conn)
    finally:
        conn.close()
    if champion is None:
        st.info("暂无 Qlib 预测截面。先运行 Qlib 实验或日常预测后，这里才能做 PK。")
        return
    latest_pred = champion["prediction_date"]

    latest_signal = query_one("""
        SELECT MAX(CAST(signal_ts AS DATE))
        FROM signals
        WHERE model_name IN ('trend_following', 'mean_reversion', 'industry_rotation')
          AND CAST(signal_ts AS DATE) <= ?
    """, [latest_pred])[0]
    if latest_signal is None:
        st.info("暂无规则策略信号。运行 `python -m src.signals.generator` 后可与 Qlib 预测对比。")
        return

    experiments = query_df("""
        SELECT p.experiment_id,
               COALESCE(e.mode, p.mode) AS mode,
               COALESCE(e.model_version, p.model_version) AS model_version,
               MAX(p.created_at) AS created_at
        FROM qlib_predictions p
        LEFT JOIN qlib_experiments e ON p.experiment_id = e.experiment_id
        WHERE p.prediction_date = ?
        GROUP BY p.experiment_id, COALESCE(e.mode, p.mode), COALESCE(e.model_version, p.model_version)
        ORDER BY created_at DESC NULLS LAST, p.experiment_id DESC
    """, [latest_pred])

    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1, 2])
    signal_date = c1.date_input("规则信号日", value=latest_signal, key="qlib_pk_signal_date")
    prediction_date = c2.date_input("Qlib预测日", value=latest_pred, key="qlib_pk_prediction_date")
    top_n = c3.number_input(
        "Top-N",
        min_value=10,
        max_value=300,
        value=int(champion.get("top_n") or 50),
        step=10,
        key="qlib_pk_top_n",
    )
    experiment_options = experiments["experiment_id"].tolist() if not experiments.empty else []
    selected_exp = None
    if experiment_options:
        default_index = experiment_options.index(champion["experiment_id"]) if champion["experiment_id"] in experiment_options else 0
        selected_exp = c4.selectbox(
            "Qlib实验",
            experiment_options,
            index=default_index,
            format_func=lambda eid: _format_pk_experiment_label(eid, experiments),
            key="qlib_pk_experiment",
        )

    st.caption(
        f"默认对比对象：{champion['source']} / {champion['experiment_id']} / "
        f"Top{int(champion.get('top_n') or 50)}"
    )

    conn = get_conn()
    try:
        report = load_rule_qlib_pk(
            conn,
            signal_date=signal_date,
            prediction_date=prediction_date,
            experiment_id=selected_exp,
            top_n=int(top_n),
            horizons=(1, 5, 10),
        )
    finally:
        conn.close()

    if report["status"] == "NO_QLIB_PREDICTIONS":
        st.info("所选日期没有 Qlib 预测截面。")
        return
    if report["status"] == "NO_RULE_SIGNALS":
        st.info("所选日期没有规则策略信号。")
        return

    summary = report["summary"]
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("规则股票", summary["rule_symbols"])
    m2.metric("规则 BUY / SELL", f"{summary['rule_buy_symbols']} / {summary['rule_sell_symbols']}")
    m3.metric(f"落入 Top{int(top_n)}", summary["overlap_top_n"])
    m4.metric("共振买入", summary["consensus_buy"])
    m5.metric("分歧信号", summary["conflict_sell_high_rank"])
    m6.metric("规则内部冲突", summary["rule_internal_conflict"])

    r1, r2, r3 = st.columns(3)
    r1.metric("规则 BUY 平均Qlib排名", _rank(summary["avg_qlib_rank_rule_buy"]))
    r2.metric("规则 SELL 平均Qlib排名", _rank(summary["avg_qlib_rank_rule_sell"]))
    r3.metric("Qlib独立候选", summary["qlib_only_top_n"])

    details = report["details"].copy()
    history = report["history"].copy()

    if not history.empty:
        st.markdown("### 前瞻收益 PK")
        horizon = st.segmented_control(
            "持有窗口",
            options=[1, 5, 10],
            default=1,
            format_func=lambda h: f"T+{h}",
            key="qlib_pk_horizon",
        )
        hist_sub = history[history["horizon"] == int(horizon)].copy()
        if not hist_sub.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=hist_sub["group"],
                y=hist_sub["avg_directional_return"],
                name="方向性收益",
                marker_color="#2563eb",
                hovertemplate="%{x}<br>方向性收益=%{y:.2%}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=hist_sub["group"],
                y=hist_sub["directional_win_rate"],
                mode="markers+lines",
                name="方向胜率",
                yaxis="y2",
                marker=dict(size=9, color="#f97316"),
                hovertemplate="%{x}<br>方向胜率=%{y:.1%}<extra></extra>",
            ))
            fig.update_layout(
                height=360,
                yaxis=dict(title="平均方向性收益", tickformat=".1%"),
                yaxis2=dict(title="方向胜率", tickformat=".0%", overlaying="y", side="right", range=[0, 1]),
                xaxis_title=None,
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            )
            st.plotly_chart(fig, width="stretch")

            hist_display = hist_sub.copy()
            hist_display["avg_forward_return"] = hist_display["avg_forward_return"].map(_pct)
            hist_display["avg_directional_return"] = hist_display["avg_directional_return"].map(_pct)
            hist_display["win_rate"] = hist_display["win_rate"].map(_pct)
            hist_display["directional_win_rate"] = hist_display["directional_win_rate"].map(_pct)
            hist_display = hist_display.rename(columns={
                "group": "分组",
                "horizon": "窗口",
                "observations": "样本数",
                "avg_forward_return": "原始平均收益",
                "win_rate": "原始胜率",
                "avg_directional_return": "方向性收益",
                "directional_win_rate": "方向胜率",
            })
            st.dataframe(hist_display, hide_index=True, width="stretch")
    else:
        st.info("当前样本缺少后续价格，暂无法计算前瞻收益。")

    st.markdown("### 信号明细")
    class_options = [c for c in [
        "共振买入", "冲突：规则卖出/Qlib高分", "规则内部冲突", "Qlib独立候选", "规则买入/Qlib弱", "规则卖出"
    ] if c in set(details["classification"])]
    selected_classes = st.multiselect(
        "分类",
        class_options,
        default=class_options,
        key="qlib_pk_classes",
    )
    view = details[details["classification"].isin(selected_classes)].copy() if selected_classes else details.copy()
    for col in ["rule_confidence", "rule_score", "qlib_score", "qlib_confidence"]:
        if col in view:
            view[col] = view[col].map(lambda x: _num(x, 4 if col.endswith("score") else 3))
    for col in ["forward_return_1d", "forward_return_5d", "forward_return_10d"]:
        if col in view:
            view[col] = view[col].map(_pct)
    if "qlib_rank" in view:
        view["qlib_rank"] = view["qlib_rank"].map(_rank)
    display_cols = [
        "classification", "symbol", "name", "industry", "rule_side", "rule_models",
        "rule_confidence", "rule_score", "qlib_rank", "qlib_score", "qlib_confidence",
        "forward_return_1d", "forward_return_5d", "forward_return_10d", "thesis",
    ]
    display_cols = [col for col in display_cols if col in view.columns]
    view = view.rename(columns={
        "classification": "分类",
        "symbol": "代码",
        "name": "名称",
        "industry": "行业",
        "rule_side": "规则方向",
        "rule_models": "规则来源",
        "rule_confidence": "规则置信度",
        "rule_score": "规则分数",
        "qlib_rank": "Qlib排名",
        "qlib_score": "Qlib分数",
        "qlib_confidence": "Qlib置信度",
        "forward_return_1d": "T+1收益",
        "forward_return_5d": "T+5收益",
        "forward_return_10d": "T+10收益",
        "thesis": "规则理由",
    })
    st.dataframe(view[[{
        "classification": "分类",
        "symbol": "代码",
        "name": "名称",
        "industry": "行业",
        "rule_side": "规则方向",
        "rule_models": "规则来源",
        "rule_confidence": "规则置信度",
        "rule_score": "规则分数",
        "qlib_rank": "Qlib排名",
        "qlib_score": "Qlib分数",
        "qlib_confidence": "Qlib置信度",
        "forward_return_1d": "T+1收益",
        "forward_return_5d": "T+5收益",
        "forward_return_10d": "T+10收益",
        "thesis": "规则理由",
    }[col] for col in display_cols]], hide_index=True, width="stretch")

    glossary = pd.DataFrame(report["glossary"])
    if not glossary.empty:
        st.markdown("### 指标解释")
        glossary = glossary.rename(columns={
            "metric": "指标",
            "meaning": "含义",
            "plain_explanation": "通俗解释",
        })
        st.table(glossary[["指标", "含义", "通俗解释"]])

    st.divider()
    show_rule_qlib_ab_tracking()


def show_rule_qlib_ab_tracking():
    st.markdown("### A/B 影子跟踪")
    st.caption("A组=规则 BUY；B组=Production/Champion Qlib Top-N；C组=规则与 Qlib 共振。")
    c1, c2 = st.columns([1, 4])
    if c1.button("记录今日样本", key="qlib_ab_record"):
        conn = get_conn()
        try:
            result = record_ab_snapshot(conn)
        finally:
            conn.close()
        if result.get("status") == "RECORDED":
            st.success(
                f"已记录 A/B 样本：{result['run_id']}，"
                f"成员 {result['members']} 个，Top{result['top_n']}。"
            )
        else:
            st.info(f"未记录：{result.get('reason', result.get('status'))}")
    c2.info("建议每天收盘闭环自动记录一次；重复执行同一 prediction_date + experiment + Top-N 会幂等覆盖。")

    conn = get_conn()
    try:
        ab = evaluate_ab_tracking(conn, horizons=(1, 5, 10, 20))
    finally:
        conn.close()
    snapshots = ab["snapshots"]
    arm_summary = ab["arm_summary"]
    if snapshots.empty:
        st.info("暂无 A/B 影子跟踪样本。点击“记录今日样本”或运行日常收盘闭环后可见。")
        return

    s1, s2, s3 = st.columns(3)
    s1.metric("样本批次", len(snapshots))
    s2.metric("最近预测日", str(snapshots["prediction_date"].max()))
    s3.metric("当前模型", snapshots.iloc[-1]["model_version"] or "—")

    if not arm_summary.empty:
        display = arm_summary.copy()
        for col in display.columns:
            if col.startswith("avg_forward_return_") or col.startswith("portfolio_return_") or col.startswith("win_rate_"):
                display[col] = display[col].map(_pct)
        display = display.rename(columns={
            "arm": "组别",
            "snapshots": "批次",
            "members": "成员数",
            "observations_1d": "T+1样本",
            "avg_forward_return_1d": "T+1平均",
            "portfolio_return_1d": "T+1组合",
            "win_rate_1d": "T+1胜率",
            "observations_5d": "T+5样本",
            "avg_forward_return_5d": "T+5平均",
            "portfolio_return_5d": "T+5组合",
            "win_rate_5d": "T+5胜率",
            "observations_10d": "T+10样本",
            "avg_forward_return_10d": "T+10平均",
            "portfolio_return_10d": "T+10组合",
            "win_rate_10d": "T+10胜率",
            "observations_20d": "T+20样本",
            "avg_forward_return_20d": "T+20平均",
            "portfolio_return_20d": "T+20组合",
            "win_rate_20d": "T+20胜率",
        })
        st.dataframe(display, hide_index=True, width="stretch")

    recent = snapshots.tail(20).copy()
    st.markdown("#### 最近样本")
    st.dataframe(recent, hide_index=True, width="stretch")


def _format_pk_experiment_label(experiment_id: str, experiments: pd.DataFrame) -> str:
    row = experiments[experiments["experiment_id"] == experiment_id]
    if row.empty:
        return experiment_id
    item = row.iloc[0]
    mode = item.get("mode") or "—"
    version = item.get("model_version") or "—"
    return f"{experiment_id} / {mode} / {version}"


def show_model_registry():
    st.subheader("模型发布")
    models = query_df("""
        SELECT model_version, experiment_id, model_name, status, market,
               model_path, metrics_json, published_at, archived_at, created_at
        FROM qlib_model_registry
        ORDER BY
            CASE status WHEN 'production' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
            created_at DESC
    """)
    if models.empty:
        st.info("暂无候选模型。成功实验会自动注册为 candidate；使用 CLI 手动发布为 production。")
        st.code("python -m src.backtest.qlib_runner publish --experiment-id QLIB-...", language="bash")
        return
    metrics = models["metrics_json"].map(parse_json_dict)
    models["ic_mean"] = metrics.map(lambda m: m.get("ic_mean"))
    models["icir"] = metrics.map(lambda m: m.get("icir"))
    models["max_drawdown"] = metrics.map(lambda m: m.get("max_drawdown"))
    models["excess_return"] = metrics.map(lambda m: m.get("excess_return"))
    display = models[[
        "model_version", "experiment_id", "status", "published_at", "archived_at",
        "ic_mean", "icir", "max_drawdown", "excess_return", "model_path",
    ]].copy()
    display["max_drawdown"] = display["max_drawdown"].map(_pct)
    display["excess_return"] = display["excess_return"].map(_pct)
    display["ic_mean"] = display["ic_mean"].map(lambda x: _num(x, 4))
    display["icir"] = display["icir"].map(lambda x: _num(x, 3))
    st.dataframe(display, hide_index=True, width="stretch")
    gate = _publish_gate_thresholds()
    st.caption(
        "发布门槛："
        f"IC Mean > {gate['min_ic_mean']:.3f}、"
        f"ICIR >= {gate['min_icir']:.2f}、"
        f"最大回撤不低于 {gate['max_drawdown_floor']:.0%}、"
        f"主基准超额 > {gate['min_excess_return']:.1%}。"
    )
    st.code("python -m src.backtest.qlib_runner publish --experiment-id <实验ID>", language="bash")


st.title("Qlib 分析")
tab_status, tab_report, tab_compare, tab_ic, tab_pred, tab_pk, tab_model = st.tabs([
    "运行状态", "实验报告", "实验对比", "因子/IC", "选股预测", "规则PK", "模型发布",
])

with tab_status:
    try:
        show_runtime_status()
    except DuckDBError as e:
        db_error_widget(e)
with tab_report:
    try:
        show_experiment_report()
    except DuckDBError as e:
        db_error_widget(e)
with tab_compare:
    try:
        show_experiment_compare()
    except DuckDBError as e:
        db_error_widget(e)
with tab_ic:
    try:
        show_ic_analysis()
    except DuckDBError as e:
        db_error_widget(e)
with tab_pred:
    try:
        show_predictions()
    except DuckDBError as e:
        db_error_widget(e)
with tab_pk:
    try:
        show_rule_qlib_pk()
    except DuckDBError as e:
        db_error_widget(e)
with tab_model:
    try:
        show_model_registry()
    except DuckDBError as e:
        db_error_widget(e)
