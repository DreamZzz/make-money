"""F2: 基金扫描器 — 六维评分。

对 fund_info 里所有有 nav 历史的基金,每日打 0-100 综合分,并归类
to in_window / watch_high_value / avoid / insufficient_data。

口径(用户 5-30 决策):
- 加仓窗口期: 趋势优先 + 中位估值
  trend_score >= 70 AND price_pct ∈ [0.20, 0.60] AND macro_score >= 50
- 高价值关注: total_score >= 70 但当前不在窗口期(等回调)
- 规避: trend_score < 40 OR price_pct > 0.85
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from loguru import logger

# 六维权重(总分 0-100)
DEFAULT_WEIGHTS = {
    "trend": 0.30,        # 趋势优先,权重最高
    "valuation": 0.20,
    "momentum": 0.15,
    "risk": 0.15,
    "liquidity": 0.10,
    "macro": 0.10,
}

# 加仓窗口阈值
TREND_MIN_FOR_WINDOW = 70.0
PRICE_PCT_WINDOW_LOW = 0.20
PRICE_PCT_WINDOW_HIGH = 0.60
MACRO_MIN_FOR_WINDOW = 50.0

# 规避阈值
TREND_MAX_FOR_AVOID = 40.0
PRICE_PCT_AVOID = 0.85

# 高价值关注
TOTAL_MIN_FOR_WATCH = 70.0

# 算分需要的最小 nav 长度
MIN_NAV_DAYS = 60
SUFFICIENT_NAV_DAYS = 120

# 估值窗口(交易日)
VALUATION_WINDOW = 756  # ~3 年

# 超跌候选(oversold_candidate)阈值
# 设计:估值进入低位 + 深度回撤已发生 → 价值出现,等趋势确立
PRICE_PCT_OVERSOLD = 0.30
MAX_DRAWDOWN_OVERSOLD = -0.20


@dataclass
class FundScreeningResult:
    eval_date: date
    fund_code: str
    fund_name: str | None
    etf_subcategory: str | None
    tracking_index: str | None
    scale_yi: float | None
    valuation_score: float | None = None
    trend_score: float | None = None
    momentum_score: float | None = None
    risk_score: float | None = None
    liquidity_score: float | None = None
    macro_score: float | None = None
    total_score: float | None = None
    price_pct: float | None = None
    ma120_above: bool | None = None
    ma250_above: bool | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    excess_return_3m: float | None = None
    volatility_20d: float | None = None
    max_drawdown_120d: float | None = None
    sharpe_1y: float | None = None
    nav_history_days: int = 0
    signal_tag: str = "insufficient_data"
    thesis: str = ""
    risk_tags: list[str] = field(default_factory=list)
    macro_stage: str | None = None
    benchmark_code: str | None = None


def _load_macro(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT trade_date, stage, stage_score, heat_score, pe_pct_10y "
        "FROM market_state WHERE benchmark='000300' "
        "ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {"trade_date": row[0], "stage": row[1], "stage_score": row[2],
            "heat_score": row[3], "pe_pct_10y": row[4]}


def _scale_score(stage: str | None) -> float:
    """市场状态打分(0-100)。用户口径:强势上升=100, 危机=0。"""
    if not stage:
        return 50.0
    mapping = {
        "强势上升": 100,
        "震荡上行": 80,
        "震荡": 60,
        "震荡下行": 40,
        "下跌": 20,
        "危机": 0,
    }
    return float(mapping.get(stage, 50))


def _score_macro_fit(category: str | None, macro: dict[str, Any]) -> float:
    """宏观契合分: 不同类别在不同 stage 下打分不同。

    保守口径: stage 越好,所有权益类基金 macro 分都越高。
    商品/债券类在危机/下跌期更安全 → 反向加分。
    """
    base = _scale_score(macro.get("stage"))
    if category in {"broad", "sector", "qdii"}:
        return base
    if category in {"commodity", "bond"}:
        # 商品/债 在下跌+危机里反而是避险,分数反向加
        if macro.get("stage") in {"下跌", "危机"}:
            return min(100.0, base + 50)
        return max(0.0, base - 20)
    return base


def _percentile_rank(series: pd.Series, value: float) -> float:
    """value 在 series 中的 ≤ 比例(0-1)。"""
    n = len(series)
    if n == 0 or value is None or np.isnan(value):
        return float("nan")
    return float((series <= value).sum()) / n


def _ma(series: pd.Series, window: int) -> float | None:
    if len(series) < max(window // 2, 20):
        return None
    ma = series.rolling(window, min_periods=max(window // 2, 20)).mean().iloc[-1]
    return None if pd.isna(ma) else float(ma)


def _return_pct(series: pd.Series, n_days: int) -> float | None:
    if len(series) <= n_days:
        return None
    cur = float(series.iloc[-1])
    past = float(series.iloc[-n_days - 1])
    if past <= 0:
        return None
    return cur / past - 1.0


def _max_drawdown(series: pd.Series) -> float | None:
    if len(series) < 20:
        return None
    rolling_max = series.cummax()
    dd = (series / rolling_max - 1.0)
    return float(dd.min())


def _annualized_vol(series: pd.Series) -> float | None:
    if len(series) < 20:
        return None
    rets = series.pct_change().dropna()
    if len(rets) < 10:
        return None
    return float(rets.std() * np.sqrt(252))


def _sharpe(series: pd.Series, rf_annual: float = 0.025) -> float | None:
    if len(series) < 60:
        return None
    rets = series.pct_change().dropna()
    if len(rets) < 30:
        return None
    annual_ret = float(rets.mean() * 252)
    annual_vol = float(rets.std() * np.sqrt(252))
    if annual_vol < 1e-6:
        return None
    return (annual_ret - rf_annual) / annual_vol


def _score_valuation(price_pct: float | None) -> float | None:
    """0=最贵(price_pct=1), 100=最便宜(price_pct=0)。"""
    if price_pct is None or np.isnan(price_pct):
        return None
    return round((1.0 - price_pct) * 100, 2)


def _score_trend(close: float, ma120: float | None, ma250: float | None) -> float | None:
    if ma120 is None and ma250 is None:
        return None
    s = 50.0
    if ma120 is not None:
        s += 20.0 if close >= ma120 else -20.0
    if ma250 is not None:
        s += 20.0 if close >= ma250 else -20.0
    # 长慢线斜率(120 vs 250):多头排列 +10
    if ma120 is not None and ma250 is not None and ma120 > ma250:
        s += 10.0
    return max(0.0, min(100.0, s))


def _score_momentum(r1: float | None, r3: float | None, r6: float | None) -> float | None:
    parts = [v for v in (r1, r3, r6) if v is not None]
    if not parts:
        return None
    avg = sum(parts) / len(parts)
    # 把动量映射到 0-100: -15% → 0, 0 → 50, +15% → 100
    return max(0.0, min(100.0, 50.0 + avg * 333.33))


def _score_risk(vol: float | None, dd: float | None) -> float | None:
    if vol is None and dd is None:
        return None
    s = 50.0
    if vol is not None:
        # 年化波动 < 15% 满分,40% 0 分,线性
        vol_s = max(0.0, min(50.0, 50.0 - (vol - 0.15) * 200))
        s = vol_s
    if dd is not None:
        # 最大回撤 0 → +30, -50% → -30
        dd_bonus = max(-30.0, min(30.0, 30.0 + dd * 60))
        s += dd_bonus
    return max(0.0, min(100.0, s))


def _score_liquidity(scale_yi: float | None) -> float | None:
    if scale_yi is None:
        return None
    # 适中规模为佳: 50 亿 = 50, 200-500 亿 = 100, > 1000 亿 = 减分(规模拖累超额)
    if scale_yi <= 20:
        return 30.0
    if scale_yi <= 100:
        return 60.0 + (scale_yi - 20) / 80 * 30
    if scale_yi <= 500:
        return 90.0 + min(10.0, (scale_yi - 100) / 400 * 10)
    if scale_yi <= 1000:
        return 90.0
    return 70.0  # 超大规模略减


def _classify_signal(
    trend: float | None,
    valuation: float | None,
    price_pct: float | None,
    macro: float | None,
    total: float | None,
    max_drawdown: float | None = None,
) -> tuple[str, str]:
    """返回 (signal_tag, headline)。

    优先级:insufficient_data → too_expensive(avoid) → oversold_candidate →
    trend_broken(avoid) → in_window → watch_high_value → neutral
    """
    if total is None:
        return "insufficient_data", "数据不足无法评分"

    # 高估值无条件 avoid
    too_expensive = price_pct is not None and price_pct > PRICE_PCT_AVOID
    if too_expensive:
        return "avoid", f"规避: 估值 {price_pct:.0%} 过贵 (> {PRICE_PCT_AVOID:.0%})"

    # 超跌候选: 低估值 + 深度回撤已发生(优先于 trend_broken avoid)
    # 这类标的趋势还弱,但价值已现;不算"可加仓",而是"等趋势确立"的关注名单
    is_oversold = (
        price_pct is not None and price_pct < PRICE_PCT_OVERSOLD
        and max_drawdown is not None and max_drawdown < MAX_DRAWDOWN_OVERSOLD
    )
    if is_oversold:
        return "oversold_candidate", (
            f"超跌候选: 估值 {price_pct:.0%} 在低位 (< {PRICE_PCT_OVERSOLD:.0%}),"
            f"已回撤 {max_drawdown:.0%},等趋势(MA120/250)转好可考虑分批"
        )

    # 趋势破: avoid
    trend_broken = trend is not None and trend < TREND_MAX_FOR_AVOID
    if trend_broken:
        return "avoid", f"规避: 趋势 {trend:.0f} 跌穿 (< {TREND_MAX_FOR_AVOID:.0f})"

    # 加仓窗口
    in_window = (
        trend is not None and trend >= TREND_MIN_FOR_WINDOW
        and price_pct is not None and PRICE_PCT_WINDOW_LOW <= price_pct <= PRICE_PCT_WINDOW_HIGH
        and macro is not None and macro >= MACRO_MIN_FOR_WINDOW
    )
    if in_window:
        return "in_window", (
            f"趋势 {trend:.0f} 健康,估值中位 ({price_pct:.0%}),宏观契合 {macro:.0f} — 可加仓"
        )

    # 高价值关注
    if total >= TOTAL_MIN_FOR_WATCH:
        # 不在窗口,通常是估值偏贵 or 趋势未确认
        if price_pct is not None and price_pct > PRICE_PCT_WINDOW_HIGH:
            reason = f"综合 {total:.0f} 高,但估值 {price_pct:.0%} 偏贵,等回调"
        elif trend is not None and trend < TREND_MIN_FOR_WINDOW:
            reason = f"综合 {total:.0f} 高,但趋势 {trend:.0f} 未确认,观察"
        else:
            reason = f"综合 {total:.0f} 高,关注窗口"
        return "watch_high_value", reason

    return "neutral", f"综合 {total:.0f},暂无明确信号"


def _aggregate(scores: dict[str, float | None], weights: dict[str, float]) -> float | None:
    weighted = 0.0
    weight_sum = 0.0
    for dim, w in weights.items():
        s = scores.get(dim)
        if s is None:
            continue
        weighted += s * w
        weight_sum += w
    if weight_sum < 0.5:  # 至少 50% 维度有数据才打分
        return None
    return round(weighted / weight_sum, 2)


def evaluate_fund(
    conn: duckdb.DuckDBPyConnection,
    fund_code: str,
    *,
    eval_date: date,
    macro: dict[str, Any],
) -> FundScreeningResult:
    """单基金多维评分。"""
    info = conn.execute(
        "SELECT name, etf_subcategory, tracking_index, scale_yi "
        "FROM fund_info WHERE fund_code = ? LIMIT 1",
        [fund_code],
    ).fetchone()
    if not info:
        return FundScreeningResult(
            eval_date=eval_date, fund_code=fund_code, fund_name=None,
            etf_subcategory=None, tracking_index=None, scale_yi=None,
            signal_tag="insufficient_data", thesis="基金未注册",
        )
    fund_name, sub, tracking, scale_yi = info

    nav_df = conn.execute(
        "SELECT trade_date, nav FROM fund_nav WHERE fund_code = ? AND nav IS NOT NULL "
        "ORDER BY trade_date",
        [fund_code],
    ).fetchdf()
    nav_days = len(nav_df)

    result = FundScreeningResult(
        eval_date=eval_date, fund_code=fund_code, fund_name=fund_name,
        etf_subcategory=sub, tracking_index=tracking, scale_yi=scale_yi,
        nav_history_days=nav_days, macro_stage=macro.get("stage"),
        benchmark_code="000300",
    )
    if nav_days < MIN_NAV_DAYS:
        result.signal_tag = "insufficient_data"
        result.thesis = f"nav 仅 {nav_days} 天 (< {MIN_NAV_DAYS}),无法评分"
        return result

    nav_df = nav_df.sort_values("trade_date").reset_index(drop=True)
    nav_series = nav_df["nav"].astype(float)
    latest_nav = float(nav_series.iloc[-1])

    # 估值: nav 在历史窗口的分位
    val_window = nav_series.tail(VALUATION_WINDOW)
    price_pct = _percentile_rank(val_window, latest_nav)
    result.price_pct = price_pct
    result.valuation_score = _score_valuation(price_pct)

    # 趋势
    ma120 = _ma(nav_series, 120)
    ma250 = _ma(nav_series, 250)
    result.ma120_above = (latest_nav >= ma120) if ma120 is not None else None
    result.ma250_above = (latest_nav >= ma250) if ma250 is not None else None
    result.trend_score = _score_trend(latest_nav, ma120, ma250)

    # 动量
    r1 = _return_pct(nav_series, 20)
    r3 = _return_pct(nav_series, 60)
    r6 = _return_pct(nav_series, 120)
    result.return_1m = r1
    result.return_3m = r3
    result.return_6m = r6
    result.momentum_score = _score_momentum(r1, r3, r6)

    # 超额(vs 000300 近 60 日)
    if tracking and tracking != "000300":
        bench_df = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code='000300' "
            "AND trade_date >= ? ORDER BY trade_date",
            [nav_df["trade_date"].iloc[-min(60, len(nav_df))]],
        ).fetchdf()
        if len(bench_df) >= 30 and r3 is not None:
            bench_r3 = _return_pct(bench_df["close"].astype(float), min(60, len(bench_df) - 1))
            if bench_r3 is not None:
                result.excess_return_3m = r3 - bench_r3

    # 风险
    vol = _annualized_vol(nav_series.tail(120))
    dd = _max_drawdown(nav_series.tail(120))
    result.volatility_20d = vol
    result.max_drawdown_120d = dd
    result.risk_score = _score_risk(vol, dd)
    result.sharpe_1y = _sharpe(nav_series.tail(252))

    # 流动性
    result.liquidity_score = _score_liquidity(scale_yi)

    # 宏观
    result.macro_score = _score_macro_fit(sub, macro)

    scores = {
        "trend": result.trend_score,
        "valuation": result.valuation_score,
        "momentum": result.momentum_score,
        "risk": result.risk_score,
        "liquidity": result.liquidity_score,
        "macro": result.macro_score,
    }
    result.total_score = _aggregate(scores, DEFAULT_WEIGHTS)

    tag, headline = _classify_signal(
        result.trend_score, result.valuation_score, result.price_pct,
        result.macro_score, result.total_score,
        max_drawdown=result.max_drawdown_120d,
    )
    result.signal_tag = tag
    risk_tags: list[str] = []
    parts = [headline]
    if nav_days < SUFFICIENT_NAV_DAYS:
        risk_tags.append("short_history")
        parts.append(f"nav 仅 {nav_days} 天(< {SUFFICIENT_NAV_DAYS}),评分可信度低")
    if result.volatility_20d is not None and result.volatility_20d > 0.30:
        risk_tags.append("high_vol")
        parts.append(f"年化波动 {result.volatility_20d:.0%} 偏高")
    if result.max_drawdown_120d is not None and result.max_drawdown_120d < -0.20:
        risk_tags.append("deep_drawdown")
        parts.append(f"近 120 日最大回撤 {result.max_drawdown_120d:.0%}")
    result.thesis = "；".join(parts)
    result.risk_tags = risk_tags or ["normal"]
    return result


def scan_funds(
    conn: duckdb.DuckDBPyConnection,
    *,
    eval_date: date | None = None,
    fund_codes: list[str] | None = None,
    persist: bool = False,
) -> list[FundScreeningResult]:
    """扫描候选池中所有有 nav 的基金。"""
    macro = _load_macro(conn)
    if eval_date is None:
        eval_date = (
            macro.get("trade_date")
            or conn.execute("SELECT MAX(trade_date) FROM fund_nav").fetchone()[0]
            or date.today()
        )
    if fund_codes is None:
        rows = conn.execute(
            "SELECT fund_code FROM fund_info WHERE enabled = TRUE "
            "AND fund_code IN (SELECT DISTINCT fund_code FROM fund_nav)"
        ).fetchall()
        fund_codes = [r[0] for r in rows]
    out: list[FundScreeningResult] = []
    for code in fund_codes:
        try:
            out.append(evaluate_fund(conn, code, eval_date=eval_date, macro=macro))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"scan {code} failed: {exc}")
    if persist and out:
        _persist(conn, out)
    return out


def _persist(conn: duckdb.DuckDBPyConnection, results: list[FundScreeningResult]) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    eval_dates = df["eval_date"].dropna().unique().tolist()
    for d in eval_dates:
        conn.execute("DELETE FROM fund_screening_results WHERE eval_date = ?", [d])
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_screening AS SELECT * FROM df")
    conn.execute(
        """
        INSERT INTO fund_screening_results (
            eval_date, fund_code, fund_name, etf_subcategory, tracking_index, scale_yi,
            valuation_score, trend_score, momentum_score, risk_score, liquidity_score, macro_score, total_score,
            price_pct, ma120_above, ma250_above,
            return_1m, return_3m, return_6m, excess_return_3m,
            volatility_20d, max_drawdown_120d, sharpe_1y, nav_history_days,
            signal_tag, thesis, risk_tags, macro_stage, benchmark_code
        )
        SELECT
            eval_date, fund_code, fund_name, etf_subcategory, tracking_index, scale_yi,
            valuation_score, trend_score, momentum_score, risk_score, liquidity_score, macro_score, total_score,
            price_pct, ma120_above, ma250_above,
            return_1m, return_3m, return_6m, excess_return_3m,
            volatility_20d, max_drawdown_120d, sharpe_1y, nav_history_days,
            signal_tag, thesis, risk_tags, macro_stage, benchmark_code
        FROM _tmp_screening
        """
    )


def load_latest_screening(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_tags: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """读最新一日扫描结果,可按 signal_tag 过滤。"""
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='fund_screening_results'"
    ).fetchone():
        return []
    if signal_tags:
        placeholders = ", ".join("?" for _ in signal_tags)
        df = conn.execute(
            f"""
            SELECT * FROM fund_screening_results
            WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results)
              AND signal_tag IN ({placeholders})
            ORDER BY total_score DESC NULLS LAST
            LIMIT ?
            """,
            [*signal_tags, limit],
        ).fetchdf()
    else:
        df = conn.execute(
            """
            SELECT * FROM fund_screening_results
            WHERE eval_date = (SELECT MAX(eval_date) FROM fund_screening_results)
            ORDER BY total_score DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchdf()
    return df.to_dict(orient="records") if not df.empty else []
