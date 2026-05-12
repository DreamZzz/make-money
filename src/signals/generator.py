"""
信号生成器 — 汇总各策略信号，输出标准化信号表。
支持过滤、去重、置信度阈值控制。
"""
import os
import uuid
from datetime import datetime

import pandas as pd
from loguru import logger

from src.config import load_config
from src.signals.lifecycle import expire_stale_signals, retire_replaced_signals


def _get_config():
    return load_config()


def merge_signals(signal_dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """合并多个策略的信号 DataFrame"""
    valid = [df for df in signal_dfs if not df.empty]
    if not valid:
        return pd.DataFrame()
    return pd.concat(valid, ignore_index=True)


def apply_filters(df: pd.DataFrame, min_confidence: float = 0.6) -> pd.DataFrame:
    """
    过滤信号：
    - 置信度低于阈值的剔除
    - 同一股票同方向去重（保留最新、最高分的）
    """
    if df.empty:
        return df

    df = df[df["confidence"] >= min_confidence].copy()

    # 去重：同一股票 + 同一方向 → 保留最高分
    if "score" in df.columns:
        df = df.sort_values("score", ascending=False)
        df = df.drop_duplicates(subset=["symbol", "side"], keep="first")

    return df


def filter_executable_universe(df: pd.DataFrame, price_data: pd.DataFrame) -> pd.DataFrame:
    """过滤不在价格矩阵中、信号日缺少价格或价格无效的标的。"""
    if df.empty or price_data is None or price_data.empty or "symbol" not in df.columns:
        return df

    result = df[df["symbol"].isin(price_data.columns)].copy()
    if "trade_date" not in result.columns:
        latest_prices = price_data.iloc[-1]
        return result[result["symbol"].map(lambda s: pd.notna(latest_prices.get(s)) and latest_prices.get(s) > 0)]

    price_index = pd.to_datetime(price_data.index)
    price_lookup = price_data.copy()
    price_lookup.index = price_index

    def _has_price(row) -> bool:
        dt = pd.to_datetime(row["trade_date"])
        sym = row["symbol"]
        if dt not in price_lookup.index or sym not in price_lookup.columns:
            return False
        value = price_lookup.at[dt, sym]
        return pd.notna(value) and value > 0

    return result[result.apply(_has_price, axis=1)].reset_index(drop=True)


def generate_signal_id() -> str:
    return f"SIG-{uuid.uuid4().hex[:8].upper()}"


def save_to_db(df: pd.DataFrame) -> int:
    """保存信号到 DuckDB（批量 INSERT）"""
    from src.data_pipeline.loader import get_connection, init_db

    if df.empty:
        return 0

    insert_df = df.copy()
    insert_df["signal_id"] = [generate_signal_id() for _ in range(len(insert_df))]
    insert_df["model_version"] = insert_df.get("model_version", None).fillna("1.0") if "model_version" in insert_df.columns else "1.0"
    if "signal_ts" not in insert_df.columns:
        # 用信号对应的交易日而非当前时间，确保 T+1 成交逻辑能找到正确的交易日
        insert_df["signal_ts"] = insert_df["trade_date"] if "trade_date" in insert_df.columns else datetime.now()
    insert_df["status"] = "ACTIVE"

    cols = ["signal_id", "model_name", "model_version", "symbol", "signal_ts",
            "horizon", "score", "side", "confidence", "expected_holding_days",
            "max_position_pct", "thesis", "risk_tags", "status"]
    for col in cols:
        if col not in insert_df.columns:
            insert_df[col] = None
    insert_df = insert_df[cols]

    cols_str = ", ".join(cols)
    conn = get_connection()
    init_db(conn)
    expire_stale_signals(conn)
    retire_replaced_signals(conn, insert_df)
    conn.execute(f"INSERT INTO signals ({cols_str}) SELECT {cols_str} FROM insert_df")
    conn.close()
    return len(insert_df)


def save_to_csv(df: pd.DataFrame, output_path: str = None) -> str:
    """保存信号为 CSV"""
    config = _get_config()
    path = output_path or config.get("signals", {}).get("output_path", "output/signals")
    os.makedirs(path, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"signals_{timestamp}.csv"
    full_path = os.path.join(path, filename)

    df.to_csv(full_path, index=False)
    logger.info(f"Signals saved to {full_path} ({len(df)} rows)")
    return full_path


def generate_all(price_data: pd.DataFrame = None,
                 high_data: pd.DataFrame = None,
                 low_data: pd.DataFrame = None,
                 days_lookback: int = 120,
                 latest_only: bool = True) -> pd.DataFrame:
    """
    一站式生成所有策略信号并输出。

    Args:
        price_data:    收盘价宽表，index=trade_date, columns=symbol
        high_data:     最高价宽表，结构同上（用于 ATR 计算）
        low_data:      最低价宽表，结构同上（用于 ATR 计算）
        days_lookback: 从 DB 加载的历史天数（仅 price_data=None 时生效）；
                       设为足够覆盖最长指标周期（slow_ma=60）加缓冲，默认 120 天。
        latest_only:   True 时只输出最新交易日的信号（日常调用用）；
                       False 时输出所有日期信号（研究/回测用）。
        如不提供 price_data 则从 DuckDB 加载。
    """
    from src.research.strategies.trend_following import compute_signals as trend_signals
    from src.research.strategies.mean_reversion import generate_signals as mean_rev_signals
    from src.research.strategies.industry_rotation import generate_rotation_signals as ind_rot_signals

    if price_data is None:
        from src.data_pipeline.loader import get_connection
        conn = get_connection(read_only=True)
        df = conn.execute(f"""
            SELECT symbol, trade_date, close, high, low FROM daily_price
            WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
              AND trade_date >= CURRENT_DATE - INTERVAL '{days_lookback} days'
            ORDER BY trade_date
        """).fetchdf()
        conn.close()
        if df.empty:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        price_data = df.pivot(index="trade_date", columns="symbol", values="close")
        high_data  = df.pivot(index="trade_date", columns="symbol", values="high")
        low_data   = df.pivot(index="trade_date", columns="symbol", values="low")

    config = _get_config()
    min_conf = config.get("signals", {}).get("min_confidence", 0.6)

    # 生成各类信号
    trend_df    = trend_signals(price_data, highs=high_data, lows=low_data)
    mean_rev_df = mean_rev_signals(price_data)

    # 行业轮动：从 stock_info.industry 读取行业映射，有数据时自动启用
    ind_rot_df = pd.DataFrame()
    try:
        from src.data_pipeline.loader import get_connection as _gc
        _conn = _gc(read_only=True)
        _ind = _conn.execute(
            "SELECT symbol, industry FROM stock_info WHERE industry IS NOT NULL AND country='CN'"
        ).fetchdf()
        _conn.close()
        if not _ind.empty:
            industry_map = dict(zip(_ind["symbol"], _ind["industry"]))
            ind_rot_df = ind_rot_signals(price_data, industry_map)
            logger.info(f"行业轮动信号: {len(ind_rot_df)} 条")
    except Exception as e:
        logger.debug(f"行业轮动跳过 (industry_map 不可用): {e}")

    all_signals = merge_signals([trend_df, mean_rev_df, ind_rot_df])

    if latest_only and not all_signals.empty and "trade_date" in all_signals.columns:
        latest_date = all_signals["trade_date"].max()
        all_signals = all_signals[all_signals["trade_date"] == latest_date]
        logger.info(f"latest_only: 保留 {latest_date} 的信号 ({len(all_signals)} 条)")

    all_signals = filter_executable_universe(all_signals, price_data)

    filtered = apply_filters(all_signals, min_conf)

    if filtered.empty:
        logger.warning("No signals generated after filtering")
        return pd.DataFrame()

    count = save_to_db(filtered)
    save_to_csv(filtered)
    logger.info(f"Generated {count} signals")

    return filtered


if __name__ == "__main__":
    generate_all()
