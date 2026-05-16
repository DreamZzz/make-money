"""本周操作建议 — 决策就绪格式的信号摘要与调仓计划"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import load_config
from src.dashboard.db import DuckDBError, db_error_widget, query_df
from src.dashboard.weekly_report_service import build_weekly_operation_summary
from src.portfolio.risk_profile import apply_risk_profile_to_portfolio_config


@st.cache_data(ttl=3600)
def _load_stock_name_map() -> dict[str, tuple[str, str]]:
    """返回 {symbol: (中文名, 市场)} 的字典，缓存 1 小时"""
    df = query_df("SELECT symbol, name, country FROM stock_info")
    result = {}
    for _, row in df.iterrows():
        market = "A股" if row["country"] == "CN" else "H股" if row["country"] == "HK" else (row["country"] or "—")
        result[str(row["symbol"])] = (row["name"] or str(row["symbol"]), market)
    return result


# ── 数据加载 ──────────────────────────────────────────────────────────────────

def _load_signals(days_back: int = 7) -> pd.DataFrame:
    """加载最近 N 天的信号；若无则回退到最新一批（按 signal_ts 日期部分）"""
    cutoff = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    df = query_df("""
        SELECT signal_id, model_name, symbol, side, score, confidence,
               horizon, max_position_pct, thesis, signal_ts,
               CAST(signal_ts AS DATE) AS trade_date
        FROM signals
        WHERE signal_ts >= ?
          AND COALESCE(status, 'ACTIVE') = 'ACTIVE'
        ORDER BY confidence DESC, score DESC
    """, [cutoff])

    if df.empty:
        df = query_df("""
            SELECT signal_id, model_name, symbol, side, score, confidence,
                   horizon, max_position_pct, thesis, signal_ts,
                   CAST(signal_ts AS DATE) AS trade_date
            FROM signals
            WHERE CAST(signal_ts AS DATE) = (
                SELECT MAX(CAST(signal_ts AS DATE)) FROM signals
            )
              AND COALESCE(status, 'ACTIVE') = 'ACTIVE'
            ORDER BY confidence DESC, score DESC
        """)

    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    return df


def _load_current_positions() -> pd.DataFrame:
    """从 paper_positions 读取最新纸交易持仓明细。"""
    return query_df("""
        SELECT p.strategy_name, p.trade_date, p.symbol, s.name, s.country,
               p.quantity, p.avg_cost, p.current_price, p.market_value,
               p.pnl, p.pnl_pct, p.weight
        FROM paper_positions p
        LEFT JOIN stock_info s ON p.symbol = s.symbol
        WHERE (p.strategy_name, p.trade_date) IN (
            SELECT strategy_name, MAX(trade_date)
            FROM paper_positions
            GROUP BY strategy_name
        )
        AND p.quantity > 0
        ORDER BY p.weight DESC, p.market_value DESC
    """)


def _positions_to_weights(positions: pd.DataFrame) -> dict[str, float]:
    if positions.empty:
        return {}
    grouped = positions.groupby("symbol", as_index=False)["weight"].sum()
    return dict(zip(grouped["symbol"], grouped["weight"].fillna(0)))


def _positions_to_quantities(positions: pd.DataFrame) -> dict[str, float]:
    if positions.empty:
        return {}
    grouped = positions.groupby("symbol", as_index=False)["quantity"].sum()
    return dict(zip(grouped["symbol"], grouped["quantity"].fillna(0)))


@st.cache_data(ttl=3600)
def _load_latest_prices(symbols: tuple[str, ...]) -> dict[str, float]:
    """读取每个标的最近一个有效收盘价，用于调仓预算和一手校验。"""
    if not symbols:
        return {}
    placeholders = ", ".join(["?"] * len(symbols))
    df = query_df(f"""
        SELECT symbol, close
        FROM (
            SELECT symbol, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM daily_price
            WHERE symbol IN ({placeholders})
              AND close IS NOT NULL
              AND close > 0
        )
        WHERE rn = 1
    """, list(symbols))
    if df.empty:
        return {}
    return dict(zip(df["symbol"], df["close"]))


def _find_conflicted_symbols(df: pd.DataFrame) -> set[str]:
    """同一批信号里同时出现 BUY 和 SELL/SHORT 的标的。"""
    if df.empty:
        return set()
    sides = df.assign(symbol=df["symbol"].astype(str)).groupby("symbol")["side"].agg(set)
    return {
        symbol for symbol, side_set in sides.items()
        if "BUY" in side_set and bool(side_set & {"SELL", "SHORT"})
    }


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


def _render_conflict_table(sub: pd.DataFrame, name_map: dict):
    st.markdown(f"#### 多策略冲突（{sub['symbol'].nunique()} 只）")
    if sub.empty:
        return

    display = sub[["symbol", "side", "confidence", "score", "max_position_pct", "model_name", "thesis"]].copy()
    display.insert(1, "名称", display["symbol"].map(lambda s: name_map.get(s, (s, "—"))[0]))
    display.insert(2, "市场", display["symbol"].map(lambda s: name_map.get(s, ("", "—"))[1]))
    display.columns = ["代码", "名称", "市场", "方向", "置信度", "分数", "建议仓位", "策略", "理由"]
    st.warning("以下标的同一期出现相反策略方向，已从自动执行买入/清仓中剔除，请人工确认。")
    st.dataframe(
        display.style.format({"置信度": "{:.0%}", "分数": "{:.3f}", "建议仓位": "{:.0%}"}),
        hide_index=True,
        use_container_width=True,
    )


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


def _render_current_holdings(positions: pd.DataFrame, name_map: dict, total_value: float):
    st.subheader("当前持仓资产")
    if positions.empty:
        st.info("暂无纸交易持仓。调仓计划将只基于现金和本期买入信号生成。")
        return

    pos = positions.copy()
    pos["display_name"] = pos.apply(lambda r: _display_stock_name(r, name_map), axis=1)
    pos["market"] = pos["country"].map({"CN": "A股", "HK": "H股"}).fillna(pos["country"].fillna("—"))

    holding_value = float(pos["market_value"].fillna(0).sum())
    holding_weight = holding_value / total_value if total_value > 0 else 0.0
    pnl = float(pos["pnl"].fillna(0).sum())
    pnl_pct = pnl / max(holding_value - pnl, 1) if holding_value > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("持仓数量", f"{len(pos)} 只")
    c2.metric("持仓市值", f"{holding_value:,.0f}")
    c3.metric("持仓占比", f"{holding_weight:.1%}")
    c4.metric("浮动盈亏", f"{pnl:,.0f}", f"{pnl_pct:.1%}")

    display = pos.rename(columns={
        "symbol": "代码",
        "display_name": "名称",
        "market": "市场",
        "strategy_name": "策略",
        "quantity": "数量",
        "avg_cost": "成本价",
        "current_price": "现价",
        "market_value": "市值",
        "pnl": "浮动盈亏",
        "pnl_pct": "盈亏率",
        "weight": "权重",
    })
    cols = ["代码", "名称", "市场", "策略", "数量", "成本价", "现价", "市值", "浮动盈亏", "盈亏率", "权重"]
    fmt = {
        "数量": "{:,.0f}",
        "成本价": "{:,.2f}",
        "现价": "{:,.2f}",
        "市值": "{:,.0f}",
        "浮动盈亏": "{:,.0f}",
        "盈亏率": "{:.1%}",
        "权重": "{:.1%}",
    }
    st.dataframe(display[cols].style.format(fmt), hide_index=True, use_container_width=True)


def _display_stock_name(row: pd.Series, name_map: dict) -> str:
    name = row.get("name")
    if pd.notna(name) and str(name).strip():
        return str(name)
    symbol = str(row.get("symbol", ""))
    return name_map.get(symbol, (symbol, "—"))[0]


def _build_rebalance_context(df: pd.DataFrame, positions: pd.DataFrame) -> dict:
    current_weights = _positions_to_weights(positions)
    current_quantities = _positions_to_quantities(positions)
    from src.portfolio.cashbook import get_account_summary

    account = get_account_summary()
    available_cash = float(account.get("cash") or 0)
    total_value = float(account.get("total_value") or 0)
    if total_value <= 0:
        total_value = available_cash

    if total_value <= 0:
        return {
            "plan": pd.DataFrame(),
            "available_cash": available_cash,
            "total_value": total_value,
            "portfolio_cfg": load_config().get("portfolio", {}),
            "risk_profile": None,
            "summary": build_weekly_operation_summary(pd.DataFrame()),
        }

    from src.portfolio.optimizer import build_executable_rebalance_plan

    app_config = load_config()
    cfg, risk_profile = apply_risk_profile_to_portfolio_config(app_config, total_value=total_value)
    symbols = tuple(sorted(set(df["symbol"].astype(str)) | set(current_weights)))
    latest_prices = _load_latest_prices(symbols)

    plan = build_executable_rebalance_plan(
        signals=df,
        current_weights=current_weights,
        latest_prices=latest_prices,
        available_cash=available_cash,
        total_value=total_value,
        current_quantities=current_quantities,
        max_single_position_pct=float(cfg.get("max_single_position_pct", 0.10)),
        overweight_single_position_pct=float(cfg.get("overweight_single_position_pct", 0.15)),
        overweight_min_confidence=float(cfg.get("overweight_min_confidence", 0.90)),
        overweight_min_rank_score=float(cfg.get("overweight_min_rank_score", 0.85)),
        max_gross_exposure_pct=float(cfg.get("max_gross_exposure_pct", 0.95)),
        cash_reserve_pct=float(cfg.get("cash_reserve_pct", 0.05)),
        min_buy_confidence=float(cfg.get("min_rebalance_buy_confidence", 0.75)),
        min_buy_rank_score=float(cfg.get("min_rebalance_buy_rank_score", 0.50)),
        estimated_fee_rate=float(cfg.get("estimated_trade_fee_rate", 0.0015)),
        max_buy_positions=int(cfg.get("max_stock_positions", 10)),
        existing_position_count=sum(1 for weight in current_weights.values() if float(weight or 0) > 0),
    )
    summary = build_weekly_operation_summary(
        plan,
        minutes_per_operation=int(cfg.get("estimated_minutes_per_operation", 3)),
    )
    return {
        "plan": plan,
        "available_cash": available_cash,
        "total_value": total_value,
        "portfolio_cfg": cfg,
        "risk_profile": risk_profile,
        "summary": summary,
    }


def _render_operation_summary_card(context: dict):
    summary = context.get("summary", {})
    profile = context.get("risk_profile")
    label = profile.label if profile else "未识别档位"
    max_positions = profile.max_stock_positions if profile else "—"

    st.subheader("本周操作量")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("需操作", f"{int(summary.get('operation_count', 0))} 次")
    c2.metric("需要现金", f"{float(summary.get('required_cash', 0.0)):,.0f}")
    c3.metric("预计耗时", f"{int(summary.get('estimated_minutes', 0))} 分钟")
    c4.metric("候选暂缓", f"{int(summary.get('candidate_count', 0))} 条")
    st.caption(
        f"当前风险档位：{label}；股票持仓上限：{max_positions} 只；"
        f"预计释放资金：{float(summary.get('released_cash', 0.0)):,.0f}。"
    )
    gap = float(summary.get("one_lot_funding_gap", 0.0) or 0.0)
    if gap > 0:
        st.info(f"候选买入中存在不足一手项目，按当前计划约还差 {gap:,.0f} 元才能凑齐最小交易单位。")


def _render_rebalance_plan(df: pd.DataFrame, name_map: dict, positions: pd.DataFrame, context: dict | None = None):
    st.subheader("调仓计划")
    context = context or _build_rebalance_context(df, positions)
    available_cash = float(context.get("available_cash") or 0)
    total_value = float(context.get("total_value") or 0)
    cfg = context.get("portfolio_cfg", {})
    profile = context.get("risk_profile")
    plan = context.get("plan", pd.DataFrame())

    st.caption(f"当前可用资金：{available_cash:,.0f}；账户总资产：{total_value:,.0f}")
    if total_value <= 0:
        st.info("账户暂无可用资产。请先在组合监控页记录初始入金。")
        return

    if plan.empty:
        st.success("当前没有可生成调仓草案的信号。")
        return

    executable_buys = plan[(plan["action"] == "买入") & (plan["executable"])]
    candidates = plan[(plan["side"] == "BUY") & (~plan["executable"])]
    reduce_plan = plan[plan["action"].isin(["减仓", "清仓"])]
    executable_reduces = reduce_plan[reduce_plan["executable"]]

    estimated_buy = float(executable_buys["order_value"].sum())
    buy_fee = float(executable_buys["estimated_fee"].sum())
    released_cash = float((-executable_reduces["order_value"] - executable_reduces["estimated_fee"]).sum())
    estimated_cash_after = available_cash + released_cash - estimated_buy - buy_fee

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("预计释放资金", f"{released_cash:,.0f}")
    c2.metric("预计买入金额", f"{estimated_buy:,.0f}")
    c3.metric("预计买入费用", f"{buy_fee:,.0f}")
    c4.metric("预计执行后现金", f"{estimated_cash_after:,.0f}")
    if estimated_buy <= 0 and not candidates.empty:
        st.warning("当前买入信号仅作为候选展示：受可用资金、现金保留、一手数量或单票仓位约束，暂不生成可执行买入。")
    elif not candidates.empty:
        st.info(f"已按账户规模筛选出 {len(executable_buys)} 条可执行买入，另有 {len(candidates)} 条候选暂缓。")

    display = plan.copy()
    display.insert(1, "name", display["symbol"].map(lambda s: name_map.get(s, (s, "—"))[0]))
    display.insert(2, "market", display["symbol"].map(lambda s: name_map.get(s, ("", "—"))[1]))
    display = display.rename(columns={
        "symbol": "代码",
        "name": "名称",
        "market": "市场",
        "current_weight": "当前权重",
        "target_weight": "计划后权重",
        "delta_weight": "本次变化",
        "action": "动作",
        "reason": "状态",
        "price": "参考价",
        "quantity": "预计数量",
        "order_value": "预计金额",
        "estimated_fee": "预计费用",
        "cash_after": "执行后现金",
        "confidence": "置信度",
        "min_lot_value": "一手金额",
        "funding_gap": "一手资金缺口",
    })

    table_cols = [
        "代码", "名称", "市场", "动作", "状态", "当前权重", "计划后权重",
        "本次变化", "参考价", "预计数量", "预计金额", "预计费用", "执行后现金",
        "一手金额", "一手资金缺口", "置信度",
    ]
    fmt = {
        "当前权重": "{:.1%}",
        "计划后权重": "{:.1%}",
        "本次变化": "{:+.1%}",
        "参考价": "{:,.2f}",
        "预计数量": "{:,.0f}",
        "预计金额": "{:,.0f}",
        "预计费用": "{:,.0f}",
        "执行后现金": "{:,.0f}",
        "一手金额": "{:,.0f}",
        "一手资金缺口": "{:,.0f}",
        "置信度": "{:.0%}",
    }

    if not executable_buys.empty:
        st.markdown("**可执行买入**")
        sub = display.loc[display["动作"] == "买入", table_cols]
        st.dataframe(sub.style.format(fmt), hide_index=True, use_container_width=True)

    if not reduce_plan.empty:
        st.markdown("**减仓 / 清仓提示**")
        sub = display.loc[display["动作"].isin(["减仓", "清仓"]), table_cols]
        st.dataframe(sub.style.format(fmt), hide_index=True, use_container_width=True)

    if not candidates.empty:
        st.markdown("**候选暂缓**")
        sub = display.loc[display["动作"] == "候选", table_cols]
        st.dataframe(sub.style.format(fmt), hide_index=True, use_container_width=True)

    st.caption(
        "调仓计划已按账户总资产、可用现金、现金保留、单票仓位上限和一手数量约束生成；"
        f"执行门槛为置信度 ≥ {float(cfg.get('min_rebalance_buy_confidence', 0.75)):.0%}，"
        f"排序分 ≥ {float(cfg.get('min_rebalance_buy_rank_score', 0.50)):.2f}；"
        f"高置信超配门槛为置信度 ≥ {float(cfg.get('overweight_min_confidence', 0.90)):.0%}，"
        f"排序分 ≥ {float(cfg.get('overweight_min_rank_score', 0.85)):.2f}，"
        f"单票上限可放宽至 {float(cfg.get('overweight_single_position_pct', 0.15)):.0%}；"
        f"当前档位最多持有 {int(cfg.get('max_stock_positions', 10))} 只股票"
        f"{f'（{profile.label}）' if profile else ''}。"
        "未进入可执行区的买入信号保留为候选，不计入预计买入金额。"
    )


# ── 页面主体 ──────────────────────────────────────────────────────────────────

st.title("📋 本周操作建议")

try:
    with st.spinner("加载信号数据…"):
        df_all = _load_signals(days_back=7)
        name_map = _load_stock_name_map()
        current_positions = _load_current_positions()
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
conflicted_symbols = _find_conflicted_symbols(df_view)
df_conflicts = df_view[df_view["symbol"].astype(str).isin(conflicted_symbols)]
df_actionable = df_view[~df_view["symbol"].astype(str).isin(conflicted_symbols)]
rebalance_context = _build_rebalance_context(df_view, current_positions)

_render_operation_summary_card(rebalance_context)
st.divider()
_render_summary_metrics(df_view)
st.divider()

from src.portfolio.cashbook import get_account_summary

account_for_holdings = get_account_summary()
_render_current_holdings(
    current_positions,
    name_map,
    float(account_for_holdings.get("total_value") or account_for_holdings.get("cash") or 0),
)
st.divider()

col_left, col_right = st.columns([3, 2])
with col_left:
    _render_signal_table(df_actionable[df_actionable["side"] == "BUY"].head(20), "买入建议", name_map)
    st.divider()
    _render_signal_table(
        df_actionable[df_actionable["side"].isin(["SELL", "SHORT"])].head(10),
        "减仓 / 清仓建议",
        name_map,
    )
    if not df_conflicts.empty:
        st.divider()
        _render_conflict_table(df_conflicts, name_map)
with col_right:
    _render_confidence_chart(df_actionable, name_map)

st.divider()
_render_rebalance_plan(df_view, name_map, current_positions, context=rebalance_context)
