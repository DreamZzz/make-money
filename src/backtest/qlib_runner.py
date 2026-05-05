"""
Qlib Alpha158 + LightGBM 训练→预测→信号写入 DuckDB。

完整流程：
  1. convert_to_qlib.py 已将 DuckDB 数据导出为 Qlib 二进制格式
  2. 本模块初始化 Qlib、训练模型、预测测试集
  3. 将预测分数写入 signals 表，供 dashboard 消费
"""
import json
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from src.config import PROJECT_ROOT, load_config


# ─── 辅助 ──────────────────────────────────────────────────────────────────

def _qlib_data_ready(market: str = "cn") -> bool:
    """检查 Qlib 二进制数据目录是否已生成（以日历文件为标志）"""
    return (PROJECT_ROOT / "qlib_data" / f"{market}_data" / "calendars" / "day.txt").exists()


def _load_lgb_params() -> dict:
    cfg = load_config()
    return {
        "loss": "mse",
        "learning_rate": 0.05,
        "num_leaves": 256,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "num_boost_round": 500,
        "early_stopping_rounds": 50,
        "verbosity": -1,
    }


# ─── 核心：训练 + 预测 ──────────────────────────────────────────────────────

def train_alpha158(
    train_start: str = "2019-01-01",
    train_end: str = "2022-12-31",
    valid_start: str = "2023-01-01",
    valid_end: str = "2023-12-31",
    test_start: str = "2024-01-01",
    test_end: str | None = None,
    market: str = "csi300",
) -> pd.DataFrame:
    """
    训练 Alpha158 + LightGBM，返回测试集预测 DataFrame。

    Returns:
        DataFrame，columns=[datetime, instrument, score]；失败时返回空 DataFrame
    """
    if test_end is None:
        test_end = date.today().strftime("%Y-%m-%d")

    if not _qlib_data_ready("cn"):
        logger.warning(
            "Qlib CN 数据未就绪，请先运行：\n"
            "  python scripts/convert_to_qlib.py --market cn"
        )
        return pd.DataFrame()

    try:
        import qlib
        from qlib.contrib.data.handler import Alpha158
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.data.dataset import DatasetH

        qlib.init(
            provider_uri=str(PROJECT_ROOT / "qlib_data" / "cn_data"),
            region="cn",
        )
        logger.info("Qlib 初始化完成")

        handler = Alpha158(
            start_time=train_start,
            end_time=test_end,
            fit_start_time=train_start,
            fit_end_time=train_end,
            instruments=market,
        )

        dataset = DatasetH(
            handler=handler,
            segments={
                "train": (train_start, train_end),
                "valid": (valid_start, valid_end),
                "test":  (test_start, test_end),
            },
        )

        model = LGBModel(**_load_lgb_params())
        logger.info("开始训练 Alpha158 + LightGBM …")
        model.fit(dataset)
        logger.info("训练完成")

        pred = model.predict(dataset, segment="test")
        # pred 是 MultiIndex Series (datetime, instrument) → score
        df = pred.reset_index()
        df.columns = ["datetime", "instrument", "score"]
        df["datetime"] = pd.to_datetime(df["datetime"])

        logger.info(
            f"预测完成：{len(df):,} 条，"
            f"日期 {df['datetime'].min().date()} ~ {df['datetime'].max().date()}"
        )
        return df

    except ImportError:
        logger.error("Qlib 未安装，请运行：pip install pyqlib")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Alpha158 训练失败：{e}")
        return pd.DataFrame()


def compute_ic(pred: pd.DataFrame) -> dict:
    """
    计算预测的信息系数（IC）和 ICIR。

    pred 需包含 [datetime, instrument, score]；
    实际收益率从 DuckDB 计算（T+5 日对数收益）。
    """
    if pred.empty:
        return {}

    from src.data_pipeline.loader import get_connection
    conn = get_connection(read_only=True)

    symbols = pred["instrument"].unique().tolist()
    start = pred["datetime"].min().strftime("%Y-%m-%d")
    end   = pred["datetime"].max().strftime("%Y-%m-%d")

    ph = ",".join(["?"] * len(symbols))
    prices = conn.execute(f"""
        SELECT symbol, trade_date, close FROM daily_price
        WHERE symbol IN ({ph}) AND trade_date >= ? AND trade_date <= ?
        ORDER BY symbol, trade_date
    """, [*symbols, start, end]).fetchdf()
    conn.close()

    if prices.empty:
        return {}

    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.set_index(["trade_date", "symbol"])["close"].unstack()
    fwd5 = prices.pct_change(5).shift(-5)  # T+5 前瞻收益

    pred_pivot = pred.set_index(["datetime", "instrument"])["score"].unstack()

    # 按日期截面计算 Rank IC
    common_dates = pred_pivot.index.intersection(fwd5.index)
    ics = []
    for dt in common_dates:
        if dt not in pred_pivot.index or dt not in fwd5.index:
            continue
        p = pred_pivot.loc[dt].dropna()
        r = fwd5.loc[dt].dropna()
        common = p.index.intersection(r.index)
        if len(common) < 10:
            continue
        ic = p[common].rank().corr(r[common].rank())
        if pd.notna(ic):
            ics.append(ic)

    if not ics:
        return {}

    import numpy as np
    ic_mean  = float(np.mean(ics))
    ic_std   = float(np.std(ics))
    icir     = ic_mean / ic_std if ic_std > 0 else 0.0
    ic_pos   = sum(1 for x in ics if x > 0) / len(ics)

    logger.info(f"IC Mean={ic_mean:.4f}  ICIR={icir:.4f}  IC>0={ic_pos:.1%}  N={len(ics)}")
    return {"ic_mean": ic_mean, "ic_std": ic_std, "icir": icir, "ic_positive_rate": ic_pos}


# ─── 信号写入 ───────────────────────────────────────────────────────────────

def predictions_to_signals(pred: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """
    将 Qlib 预测 DataFrame 转换为系统标准信号格式。
    只取最新截面的 top_n 高分股作为 BUY 信号。
    """
    from src.research.strategies.alpha158_baseline import generate_signals
    return generate_signals(pred, top_n=top_n)


# ─── 顶层入口 ───────────────────────────────────────────────────────────────

def run_qlib_backtest(strategy: str = "alpha158") -> dict:
    """
    完整跑一遍 Qlib 训练→预测→IC 评估→信号写入。
    """
    if strategy != "alpha158":
        logger.warning(f"目前仅支持 alpha158，跳过 {strategy}")
        return {}

    cfg = load_config().get("qlib", {})
    pred = train_alpha158(
        train_start=cfg.get("train_start", "2019-01-01"),
        train_end=cfg.get("train_end", "2022-12-31"),
        valid_start=cfg.get("valid_start", "2023-01-01"),
        valid_end=cfg.get("valid_end", "2023-12-31"),
        test_start=cfg.get("test_start", "2024-01-01"),
    )

    if pred.empty:
        return {}

    metrics = compute_ic(pred)

    # 转换为信号并写入 DB
    signals = predictions_to_signals(pred)
    if not signals.empty:
        from src.signals.generator import save_to_db, save_to_csv
        n = save_to_db(signals)
        save_to_csv(signals)
        logger.info(f"Alpha158 信号已写入：{n} 条")
        metrics["signals_written"] = n

    return metrics


def run_all_strategies() -> dict[str, dict]:
    return {"alpha158": run_qlib_backtest("alpha158")}


if __name__ == "__main__":
    result = run_qlib_backtest()
    print(result)
