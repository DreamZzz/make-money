"""市场行情概览 — 指数走势、关键信号识别、涨跌排行"""
import sys
sys.path.insert(0, "/Users/zhaoqiang/Documents/Project/make-money")

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.dashboard.db import get_conn

PERIODS = {"1个月": 22, "3个月": 66, "6个月": 132, "1年": 252, "全部": 9999}




@st.cache_data(ttl=300)
def _load_index_data(index_code: str, days: int) -> pd.DataFrame:
    """加载指数日线数据"""
    df = get_conn().execute(f"""
        SELECT trade_date, open, high, low, close, volume, amount
        FROM index_daily
        WHERE index_code = ?
        ORDER BY trade_date
    """, [index_code]).fetchdf()
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if days < 9999:
        df = df.tail(days)
    df["MA5"] = df["close"].rolling(5).mean()
    df["MA20"] = df["close"].rolling(20).mean()
    df["MA60"] = df["close"].rolling(60).mean()
    return df


def _make_candlestick_chart(df: pd.DataFrame, title: str) -> go.Figure:
    """创建带均线和成交量的 K 线图"""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.04,
    )
    # K线
    fig.add_trace(go.Candlestick(
        x=df["trade_date"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="K线",
    ), row=1, col=1)
    # 均线
    for ma, color in [("MA5", "#e8c170"), ("MA20", "#f08eb5"), ("MA60", "#6ba1d4")]:
        fig.add_trace(go.Scatter(
            x=df["trade_date"], y=df[ma], mode="lines",
            name=ma, line=dict(color=color, width=1.2),
        ), row=1, col=1)
    # 成交量
    colors = ["#ef5350" if df["close"].iloc[i] < df["open"].iloc[i] else "#26a69a" for i in range(len(df))]
    fig.add_trace(go.Bar(
        x=df["trade_date"], y=df["volume"],
        name="成交量", marker_color=colors, opacity=0.5,
    ), row=2, col=1)
    fig.update_layout(
        title=title, height=520, xaxis_rangeslider_visible=False,
        xaxis2_tickformat="%m月%d日", margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.update_xaxes(tickformat="%m月%d日", row=1, col=1)
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    return fig


def show_index_chart():
    """指数走势图 — Tab 切换 + 时间范围"""
    st.subheader("基准指数走势")

    period_keys = list(PERIODS.keys())
    range_choice = st.radio("时间范围", period_keys, horizontal=True, index=2, key="idx_range")
    days = PERIODS[range_choice]

    INDEX_DEFS = [
        ("000300", "沪深300", "沪深300"),
        ("000905", "中证500", "中证500"),
        ("^HSI", "恒生指数", "恒生指数"),
        ("3032.HK", "恒生科技", "恒生科技"),
    ]

    tabs = st.tabs([name for _, _, name in INDEX_DEFS])

    for tab, (code, title, _) in zip(tabs, INDEX_DEFS):
        with tab:
            df = _load_index_data(code, days)
            if df.empty:
                st.info(f"暂无{title}数据，请运行 `python3 -m src.data_pipeline.main update` 拉取")
            else:
                st.plotly_chart(_make_candlestick_chart(df, title), width="stretch")
                latest = df.iloc[-1]
                chg = (latest["close"] - df.iloc[-2]["close"]) / df.iloc[-2]["close"] * 100 if len(df) > 1 else 0
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("收盘", f"{latest['close']:.2f}", f"{chg:+.2f}%")
                c2.metric("最高", f"{latest['high']:.2f}")
                c3.metric("最低", f"{latest['low']:.2f}")
                c4.metric("MA20", f"{latest['MA20']:.2f}")


# ---- 关键信号识别 ----

def _stock_external_links(symbol: str) -> dict:
    """生成个股外链"""
    pure = symbol.lstrip("0") or "0"
    if len(symbol) == 6:
        # A股
        market = "sh" if symbol.startswith(("6", "5")) else "sz"
        return {
            "东方财富": f"https://quote.eastmoney.com/{market}{symbol}.html",
            "雪球": f"https://xueqiu.com/S/SH{symbol}" if market == "sh" else f"https://xueqiu.com/S/SZ{symbol}",
        }
    else:
        # 港股
        return {
            "东方财富": f"https://quote.eastmoney.com/hk/{pure}.html",
            "雪球": f"https://xueqiu.com/S/HK{pure}",
        }


SIGNAL_META = {
    "volume_breakout": {
        "label": "📊 成交量异动",
        "interpretation": "放量说明市场关注度显著上升，大资金可能正在进场或出逃。成交量的突然放大通常先于价格的大幅波动，是趋势变化的先行指标。",
        "advice": "结合价格方向判断：放量上涨 → 多头确认，可跟随；放量下跌 → 空头发力，应减仓或观望。若放量滞涨（量增价平），需警惕出货可能。",
    },
    "ma_golden_cross": {
        "label": "📈 均线金叉",
        "interpretation": "短期均线（MA5）上穿中期均线（MA20），表示短期买入力量强于中期持有者，趋势由空转多的概率增大。在技术分析中，金叉被视为经典的买入信号。",
        "advice": "可视为短期入场信号，但需配合量能确认。若金叉同时伴随放量，可信度更高。若金叉出现在 MA60 下方，反弹性质的可能性较大，不宜重仓。",
    },
    "ma_death_cross": {
        "label": "📉 均线死叉",
        "interpretation": "短期均线（MA5）下穿中期均线（MA20），表示短期卖方力量占据上风，市场情绪转弱。死叉是趋势交易者常用的减仓或离场信号。",
        "advice": "建议减仓或暂时观望，观察 MA60 是否提供支撑。若死叉伴随放量下跌，应果断止损。死叉后若迅速收回 MA20 上方，则可能是假突破（空头陷阱）。",
    },
    "rsi_overbought": {
        "label": "🔴 RSI 超买",
        "interpretation": "RSI 高于 70 表示市场短期处于过热状态，买入力量消耗过大，技术性回调的概率增大。超买不一定立即下跌，但继续上涨的空间和动力都在减弱。",
        "advice": "不宜追高加仓，可考虑分批止盈。等待 RSI 回到 30~70 正常区间后再评估入场时机。在强牛市中，RSI 可能长期维持在超买区，此时应以趋势跟随为主。",
    },
    "rsi_oversold": {
        "label": "🟢 RSI 超卖",
        "interpretation": "RSI 低于 30 表示市场恐慌情绪较重，卖压短期释放充分，技术性反弹的概率增大。但超卖不代表底部已现，在下跌趋势中超卖后可能继续超卖。",
        "advice": "可考虑分批试探性建仓，但需严格止损。最佳策略是等待 RSI 从超卖区回升至 30 以上（底背离或W底确认），同时配合成交量回暖再加大仓位。",
    },
    "new_high": {
        "label": "🚀 突破60日新高",
        "interpretation": "价格突破过去 60 个交易日的最高点，表明多头完全主导当前行情，上方套牢盘压力较小，趋势延续的概率较大。",
        "advice": "持有者可继续持仓跟随趋势，止损位上移至买入成本或最近支撑位。新入场者需注意：突破新高后可能出现短暂回踩确认，可在回踩时寻找更好的入场点。",
    },
    "new_low": {
        "label": "⚠️ 跌破60日新低",
        "interpretation": "价格跌破过去 60 个交易日的最低点，空头力量占据绝对优势，市场信心不足。可能的原因包括基本面恶化、行业利空或系统性风险。",
        "advice": "建议立即止损或大幅减仓，不要试图抄底。等待出现明确的企稳信号（如底分型、放量反弹、MACD 底背离）后再考虑重新介入。",
    },
}

NO_SIGNAL_MSG = {
    "none": {
        "label": "✅ 无明显信号",
        "interpretation": "当前指数各项技术指标处于正常区间，没有检测到极端信号。市场处于相对平稳状态，适合按原有策略执行。",
        "advice": "维持现有仓位和策略，按计划执行调仓。可关注个股层面的信号变化，寻找结构性机会。",
    },
}


def _detect_signals(df: pd.DataFrame, lookback: int = 60) -> list[dict]:
    """从日线数据中识别关键交易信号，返回结构化信号列表"""
    if len(df) < 60:
        return []
    latest = df.iloc[-1]
    signals = []

    # 成交量异动
    avg_vol_20 = df["volume"].tail(21).head(20).mean()
    if avg_vol_20 > 0 and latest["volume"] > avg_vol_20 * 2:
        ratio = latest["volume"] / avg_vol_20
        signals.append({
            "key": "volume_breakout",
            "detail": f"当日成交量 {latest['volume']/1e8:.1f}亿，20日均量 {avg_vol_20/1e8:.1f}亿，放量 {ratio:.1f} 倍",
        })

    # 均线金叉/死叉
    ma5 = df["close"].rolling(5).mean()
    ma20 = df["close"].rolling(20).mean()
    if len(ma5) >= 3 and len(ma20) >= 3:
        if ma5.iloc[-1] > ma20.iloc[-1] and ma5.iloc[-2] <= ma20.iloc[-2]:
            signals.append({
                "key": "ma_golden_cross",
                "detail": f"MA5={ma5.iloc[-1]:.2f} ↑ / MA20={ma20.iloc[-1]:.2f}，收盘价 {latest['close']:.2f}",
            })
        elif ma5.iloc[-1] < ma20.iloc[-1] and ma5.iloc[-2] >= ma20.iloc[-2]:
            signals.append({
                "key": "ma_death_cross",
                "detail": f"MA5={ma5.iloc[-1]:.2f} ↓ / MA20={ma20.iloc[-1]:.2f}，收盘价 {latest['close']:.2f}",
            })

    # RSI 极值
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    if not pd.isna(rsi.iloc[-1]):
        if rsi.iloc[-1] > 70:
            signals.append({
                "key": "rsi_overbought",
                "detail": f"14日 RSI = {rsi.iloc[-1]:.1f}（超买阈值 70）",
            })
        elif rsi.iloc[-1] < 30:
            signals.append({
                "key": "rsi_oversold",
                "detail": f"14日 RSI = {rsi.iloc[-1]:.1f}（超卖阈值 30）",
            })

    # 新高/新低
    high_60 = df["high"].tail(61).head(60).max()
    low_60 = df["low"].tail(61).head(60).min()
    if latest["high"] >= high_60 * 0.999:
        signals.append({
            "key": "new_high",
            "detail": f"当日最高 {latest['high']:.2f} 突破60日最高 {high_60:.2f}",
        })
    if latest["low"] <= low_60 * 1.001:
        signals.append({
            "key": "new_low",
            "detail": f"当日最低 {latest['low']:.2f} 跌破60日最低 {low_60:.2f}",
        })

    return signals


def _render_signal_card(signal: dict):
    """渲染单个信号的详细卡片"""
    meta = SIGNAL_META.get(signal["key"])
    if not meta:
        return
    with st.expander(f"{meta['label']} — {signal['detail']}", expanded=len(signal.get("key","")) > 0):
        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.markdown("**📖 信号解读**")
            st.markdown(meta["interpretation"])
        with col_b:
            st.markdown("**💡 参考建议**")
            st.markdown(meta["advice"])


def show_market_signals():
    """展示当日关键信号（含详细解读和参考意见）"""
    st.subheader("当日关键信号")
    conn = get_conn()

    latest_date = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    if not latest_date:
        st.info("暂无数据")
        return

    st.caption(f"基于最新交易日 {latest_date} 的数据分析")

    SIGNAL_INDICES = [
        ("000300", "沪深300"),
        ("000905", "中证500"),
        ("^HSI", "恒生指数"),
        ("3032.HK", "恒生科技"),
    ]

    tabs = st.tabs([f"{name}" for _, name in SIGNAL_INDICES])

    for tab, (index_code, index_name) in zip(tabs, SIGNAL_INDICES):
        with tab:
            idx_df = conn.execute("""
                SELECT trade_date, open, high, low, close, volume
                FROM index_daily WHERE index_code = ? ORDER BY trade_date
            """, [index_code]).fetchdf()

            if idx_df.empty:
                st.info(f"暂无{index_name}数据")
                continue

            idx_df["trade_date"] = pd.to_datetime(idx_df["trade_date"])
            sigs = _detect_signals(idx_df)

            if not sigs:
                # 无异常信号
                none_meta = NO_SIGNAL_MSG["none"]
                st.success(f"{none_meta['label']}：{none_meta['interpretation']}")
                st.info(f"💡 {none_meta['advice']}")
            else:
                st.markdown(f"检测到 **{len(sigs)}** 个关键信号：")
                for sig in sigs:
                    _render_signal_card(sig)


def _market_name(symbol: str) -> str:
    """判断股票所属市场"""
    if len(symbol) == 6:
        return "沪市" if symbol.startswith(("6", "5")) else "深市"
    return "港股"


@st.cache_data(ttl=3600)
def _load_stock_names() -> dict[str, tuple]:
    """批量加载股票名称和行业"""
    df = get_conn().execute("""
        SELECT symbol, name, country, industry FROM stock_info
        WHERE name IS NOT NULL
    """).fetchdf()
    result = {}
    for _, row in df.iterrows():
        industry = row["industry"] if row["industry"] and not pd.isna(row["industry"]) else (
            "A股" if row["country"] == "CN" else "港股"
        )
        result[row["symbol"]] = (row["name"], row["country"], industry)
    return result


def show_top_movers():
    """涨跌幅排行（带名称、市场、行业、外链）"""
    st.subheader("涨跌幅排行")

    end = get_conn().execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    if not end:
        return

    df = get_conn().execute(f"""
        WITH latest AS (SELECT symbol, close FROM daily_price WHERE trade_date = ?),
        week_ago AS (
            SELECT symbol, close as close_w FROM daily_price
            WHERE trade_date <= ? AND trade_date >= ?::DATE - INTERVAL '7 days'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date ASC) = 1
        ),
        month_ago AS (
            SELECT symbol, close as close_m FROM daily_price
            WHERE trade_date <= ? AND trade_date >= ?::DATE - INTERVAL '30 days'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date ASC) = 1
        )
        SELECT l.symbol,
               ROUND((l.close - w.close_w) / w.close_w * 100, 2) as week_chg,
               ROUND((l.close - m.close_m) / m.close_m * 100, 2) as month_chg,
               ROUND(l.close, 2) as price
        FROM latest l
        LEFT JOIN week_ago w ON l.symbol = w.symbol
        LEFT JOIN month_ago m ON l.symbol = m.symbol
        WHERE w.close_w IS NOT NULL
    """, [end, end, end, end, end]).fetchdf()

    if df.empty:
        return

    # 批量获取名称
    names = _load_stock_names()

    def _make_links(sym):
        links = _stock_external_links(sym)
        return " ".join([f"[{name}]({url})" for name, url in links.items()])

    tab1, tab2, tab3 = st.tabs(["周涨幅 Top 10", "周跌幅 Bottom 10", "月涨幅 Top 10"])

    def _format_table(data, sort_col, ascending):
        data = data.sort_values(sort_col, ascending=ascending).head(10).copy()
        # 补充名称、市场、行业
        data["name"] = data["symbol"].apply(lambda s: names.get(s, ("", "", ""))[0])
        data["market"] = data["symbol"].apply(lambda s: names.get(s, ("", "", ""))[1] or _market_name(s))
        data["industry"] = data["symbol"].apply(lambda s: names.get(s, ("", "", ""))[2] or "-")
        data["外链"] = data["symbol"].apply(_make_links)
        other_col = "month_chg" if sort_col == "week_chg" else "week_chg"
        data[sort_col + "_fmt"] = data[sort_col].apply(lambda x: f"{x:+.2f}%")
        data[other_col + "_fmt"] = data[other_col].apply(lambda x: f"{x:+.2f}%")
        data["price"] = data["price"].round(2)
        return data[["symbol", "name", "market", "industry",
                     sort_col + "_fmt", other_col + "_fmt", "price", "外链"]]

    col_config = {
        "symbol": "代码",
        "name": "名称",
        "market": "市场",
        "industry": "行业",
        "week_chg_fmt": "周涨跌",
        "month_chg_fmt": "月涨跌",
        "price": st.column_config.NumberColumn("现价", format="%.2f"),
        "外链": st.column_config.LinkColumn("外链", width="medium"),
    }

    with tab1:
        st.dataframe(
            _format_table(df, "week_chg", False),
            column_config=col_config, hide_index=True, width="stretch",
        )
    with tab2:
        st.dataframe(
            _format_table(df, "week_chg", True),
            column_config=col_config, hide_index=True, width="stretch",
        )
    with tab3:
        st.dataframe(
            _format_table(df, "month_chg", False),
            column_config=col_config, hide_index=True, width="stretch",
        )


def show_data_status():
    """数据状态 — 按市场/指数详细展示"""
    st.subheader("数据构成")

    conn = get_conn()

    # ---- 查询数据 ----
    total = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price").fetchone()[0]
    rows = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    oldest = conn.execute("SELECT MIN(trade_date) FROM daily_price").fetchone()[0]
    latest = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]

    # A股（6位代码）
    cn_total = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol) = 6"
    ).fetchone()[0]
    cn_latest = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE length(symbol) = 6"
    ).fetchone()[0]
    cn_rows = conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE length(symbol) = 6"
    ).fetchone()[0]
    cn_today = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol) = 6 AND trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE length(symbol) = 6)"
    ).fetchone()[0]

    # 港股（1-5位代码）
    hk_total = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol) <= 5"
    ).fetchone()[0]
    hk_latest = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE length(symbol) <= 5"
    ).fetchone()[0]
    hk_rows = conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE length(symbol) <= 5"
    ).fetchone()[0]
    hk_today = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol) <= 5 AND trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE length(symbol) <= 5)"
    ).fetchone()[0]

    # 指数数据
    idx_info = conn.execute("""
        SELECT index_code, COUNT(*) as days, MAX(trade_date) as latest, MIN(trade_date) as oldest
        FROM index_daily GROUP BY index_code ORDER BY index_code
    """).fetchall()
    idx_map = {r[0]: {"days": r[1], "latest": r[2], "oldest": r[3]} for r in idx_info}

    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)

    def _freshness(dt, market_today):
        """数据新鲜度标签"""
        if dt is None:
            return "❌ 无数据", "#ef5350"
        days_behind = (today - dt).days if hasattr(dt, 'date') else (today - dt).days
        if days_behind <= 1:
            return f"✅ {dt} (最新)", "#26a69a"
        elif days_behind <= 3:
            return f"🟡 {dt} (落后{days_behind}天)", "#ff9800"
        else:
            return f"🔴 {dt} (落后{days_behind}天)", "#ef5350"

    # ---- 渲染 ----
    # 顶部概要卡片
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总覆盖", f"{total} 只", help="有日线数据的股票总数")
    c2.metric("日线记录", f"{rows/1e4:.1f} 万条")
    c3.metric("数据起点", str(oldest))
    c4.metric("A股覆盖", f"{cn_total} 只")
    c5.metric("港股覆盖", f"{hk_total} 只")

    st.divider()

    # A股 vs 港股详情
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### 🇨🇳 A股市场")
        st.markdown(f"""
        | 指标 | 值 |
        |------|-----|
        | 覆盖股票 | **{cn_total}** 只（沪深300 + 中证500 合并去重） |
        | 最新数据 | {_freshness(cn_latest, cn_today)[0]} |
        | 当日覆盖 | **{cn_today}** 只 |
        | 数据范围 | {oldest} ~ {cn_latest} |
        | 累计记录 | {cn_rows:,} 条 |
        | 沪深300 指数 | {idx_map.get('000300', {}).get('days', 0)} 天 · 最新 {idx_map.get('000300', {}).get('latest', 'N/A')} |
        | 中证500 指数 | {idx_map.get('000905', {}).get('days', 'N/A')} 天 · 最新 {idx_map.get('000905', {}).get('latest', 'N/A')} |
        """)

    with col_b:
        st.markdown("#### 🇭🇰 港股市场")
        st.markdown(f"""
        | 指标 | 值 |
        |------|-----|
        | 覆盖股票 | **{hk_total}** 只（恒生指数 + 恒生科技 合并） |
        | 最新数据 | {_freshness(hk_latest, hk_today)[0]} |
        | 当日覆盖 | **{hk_today}** 只 |
        | 数据范围 | {oldest} ~ {hk_latest} |
        | 累计记录 | {hk_rows:,} 条 |
        | 恒生指数 | {idx_map.get('^HSI', {}).get('days', 0)} 天 · 最新 {idx_map.get('^HSI', {}).get('latest', 'N/A')} |
        | 恒生科技 | {idx_map.get('3032.HK', {}).get('days', 'N/A')} 天 · 最新 {idx_map.get('3032.HK', {}).get('latest', 'N/A')} |
        """)


# ---- 页面渲染 ----
st.title("📈 市场行情概览")
show_data_status()
st.divider()
show_index_chart()
st.divider()
show_market_signals()
st.divider()
show_top_movers()
