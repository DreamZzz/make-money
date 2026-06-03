"""R2: 财报事件 analyzer。

把业绩预告 / 业绩快报 / 主财报原始行转成 EarningsAlert(含 sentiment / impact_score /
headline / risk_tags),落 earnings_alerts 表。

设计:
- sentiment 由 4 个加权信号决定: np_change 基准 + surprise 调整 + industry_rank 调整 + cf_quality 调整
- impact_score 在 [-1, +1],POSITIVE >0.3 / NEGATIVE <-0.3 / NEUTRAL 之间
- headline 为一句话中文判读,Dashboard 直接显示
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

import duckdb
import pandas as pd
from loguru import logger

# sentiment 阈值
SENTIMENT_POSITIVE_THRESHOLD = 0.3
SENTIMENT_NEGATIVE_THRESHOLD = -0.3


@dataclass
class EarningsAlert:
    alert_id: str
    symbol: str
    report_period: date
    event_type: str               # FORECAST / EXPRESS / ANNUAL / QUARTERLY
    event_date: date
    forecast_text: str | None = None
    np_change_min: float | None = None
    np_change_max: float | None = None
    np_change_mid: float | None = None
    revenue_yoy: float | None = None
    revenue_qoq: float | None = None
    np_yoy: float | None = None
    np_qoq: float | None = None
    surprise_pct: float | None = None
    industry_rank_pct: float | None = None
    cf_to_np_ratio: float | None = None
    sentiment: str = "NEUTRAL"
    impact_score: float = 0.0
    headline: str = ""
    risk_tags: list[str] = field(default_factory=list)
    post_event_return_1d: float | None = None
    post_event_return_5d: float | None = None


def compute_sentiment(
    np_change: float | None,
    surprise: float | None = None,
    industry_rank: float | None = None,
    cf_quality: float | None = None,
) -> tuple[str, float]:
    """加权 sentiment 计算。

    np_change 基础: <=-20%→-0.6, [-20%,-5%]→-0.3, [-5%,+5%]→0, [+5%,+20%]→+0.3, >+20%→+0.6
    surprise (实际 vs 预告中值差): ±0.3 加权
    industry_rank (同业内分位): >0.7→+0.2, <0.3→-0.2
    cf_quality (CF/NP): <0.5→-0.1, >1.0→+0.1
    最终 clip 到 [-1, +1]; sentiment 由 ±0.3 阈值划定。
    """
    if np_change is None:
        return "NEUTRAL", 0.0

    # 1. base 净利同比
    if np_change <= -20:
        base = -0.6
    elif np_change <= -5:
        base = -0.3
    elif np_change <= 5:
        base = 0.0
    elif np_change <= 20:
        base = 0.3
    else:
        base = 0.6

    # 2. surprise(超预期/逊预期 比例)
    adj_surprise = 0.0
    if surprise is not None:
        adj_surprise = max(-0.3, min(0.3, surprise / 100.0))

    # 3. industry_rank
    adj_industry = 0.0
    if industry_rank is not None:
        if industry_rank > 0.7:
            adj_industry = 0.2
        elif industry_rank < 0.3:
            adj_industry = -0.2

    # 4. cf_quality
    adj_cf = 0.0
    if cf_quality is not None:
        if cf_quality < 0.5:
            adj_cf = -0.1
        elif cf_quality > 1.0:
            adj_cf = 0.1

    score = max(-1.0, min(1.0, base + adj_surprise + adj_industry + adj_cf))
    if score > SENTIMENT_POSITIVE_THRESHOLD:
        sentiment = "POSITIVE"
    elif score < SENTIMENT_NEGATIVE_THRESHOLD:
        sentiment = "NEGATIVE"
    else:
        sentiment = "NEUTRAL"
    return sentiment, round(score, 3)


def _make_headline(alert: EarningsAlert) -> str:
    """根据数据生成一句话中文判读。"""
    parts: list[str] = []
    if alert.event_type == "FORECAST":
        ft = alert.forecast_text or "预告"
        if alert.np_change_mid is not None:
            parts.append(f"{ft}: 净利同比中值 {alert.np_change_mid:+.0f}%")
        else:
            parts.append(ft)
    elif alert.event_type == "EXPRESS":
        if alert.np_yoy is not None:
            parts.append(f"快报: 净利同比 {alert.np_yoy:+.1f}%")
        if alert.revenue_yoy is not None:
            parts.append(f"营收同比 {alert.revenue_yoy:+.1f}%")
    if alert.surprise_pct is not None and abs(alert.surprise_pct) > 5:
        parts.append(f"vs 预告 {alert.surprise_pct:+.0f}%")
    if alert.industry_rank_pct is not None:
        if alert.industry_rank_pct > 0.7:
            parts.append("行业领先")
        elif alert.industry_rank_pct < 0.3:
            parts.append("行业靠后")
    if alert.cf_to_np_ratio is not None and alert.cf_to_np_ratio < 0.5:
        parts.append("现金流偏弱")
    parts.append(f"[{alert.sentiment}]")
    return " · ".join(parts)


def _compute_risk_tags(alert: EarningsAlert) -> list[str]:
    tags: list[str] = []
    if alert.np_change_mid is not None and alert.np_change_mid < -30:
        tags.append("severe_np_decline")
    if alert.forecast_text and "亏" in alert.forecast_text:
        tags.append("loss_forecast")
    if alert.cf_to_np_ratio is not None and alert.cf_to_np_ratio < 0.3:
        tags.append("weak_cash_flow")
    if alert.industry_rank_pct is not None and alert.industry_rank_pct < 0.2:
        tags.append("industry_laggard")
    if alert.surprise_pct is not None and alert.surprise_pct < -20:
        tags.append("missed_forecast")
    return tags or ["normal"]


def analyze_forecast(raw_row: pd.Series, *,
                    industry_rank: float | None = None,
                    cf_quality: float | None = None) -> EarningsAlert:
    """业绩预告 → EarningsAlert。"""
    np_min = raw_row.get("np_change_min")
    np_max = raw_row.get("np_change_max")
    np_mid: float | None = None
    if pd.notna(np_min) and pd.notna(np_max):
        np_mid = float((np_min + np_max) / 2)
    elif pd.notna(np_min):
        np_mid = float(np_min)
    elif pd.notna(np_max):
        np_mid = float(np_max)

    alert = EarningsAlert(
        alert_id=f"EALT-{uuid.uuid4().hex[:10].upper()}",
        symbol=str(raw_row["symbol"]),
        report_period=raw_row["report_period"],
        event_type="FORECAST",
        event_date=raw_row["event_date"],
        forecast_text=str(raw_row.get("forecast_text") or ""),
        np_change_min=float(np_min) if pd.notna(np_min) else None,
        np_change_max=float(np_max) if pd.notna(np_max) else None,
        np_change_mid=np_mid,
        industry_rank_pct=industry_rank,
        cf_to_np_ratio=cf_quality,
    )
    sentiment, impact = compute_sentiment(
        alert.np_change_mid, surprise=None,
        industry_rank=industry_rank, cf_quality=cf_quality,
    )
    alert.sentiment = sentiment
    alert.impact_score = impact
    alert.risk_tags = _compute_risk_tags(alert)
    alert.headline = _make_headline(alert)
    return alert


def analyze_express(raw_row: pd.Series, *,
                    forecast_np_mid: float | None = None,
                    industry_rank: float | None = None,
                    cf_quality: float | None = None) -> EarningsAlert:
    """业绩快报 → EarningsAlert。

    forecast_np_mid: 同标的同期之前的预告中值,用于算 surprise。
    """
    np_yoy = raw_row.get("np_yoy")
    revenue_yoy = raw_row.get("revenue_yoy")

    surprise: float | None = None
    if forecast_np_mid is not None and pd.notna(np_yoy):
        surprise = float(np_yoy) - float(forecast_np_mid)

    alert = EarningsAlert(
        alert_id=f"EALT-{uuid.uuid4().hex[:10].upper()}",
        symbol=str(raw_row["symbol"]),
        report_period=raw_row["report_period"],
        event_type="EXPRESS",
        event_date=raw_row["event_date"],
        revenue_yoy=float(revenue_yoy) if pd.notna(revenue_yoy) else None,
        np_yoy=float(np_yoy) if pd.notna(np_yoy) else None,
        surprise_pct=surprise,
        industry_rank_pct=industry_rank,
        cf_to_np_ratio=cf_quality,
    )
    sentiment, impact = compute_sentiment(
        alert.np_yoy, surprise=surprise,
        industry_rank=industry_rank, cf_quality=cf_quality,
    )
    alert.sentiment = sentiment
    alert.impact_score = impact
    alert.risk_tags = _compute_risk_tags(alert)
    alert.headline = _make_headline(alert)
    return alert


def persist_alerts(conn: duckdb.DuckDBPyConnection, alerts: list[EarningsAlert]) -> int:
    """落 earnings_alerts 表(INSERT OR REPLACE)。"""
    if not alerts:
        return 0
    df = pd.DataFrame([asdict(a) for a in alerts])  # noqa: F841 - DuckDB 引用
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_ealts AS SELECT * FROM df")
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_alerts (
            alert_id, symbol, report_period, event_type, event_date,
            forecast_text, np_change_min, np_change_max, np_change_mid,
            revenue_yoy, revenue_qoq, np_yoy, np_qoq,
            surprise_pct, industry_rank_pct, cf_to_np_ratio,
            sentiment, impact_score, headline, risk_tags,
            post_event_return_1d, post_event_return_5d
        )
        SELECT alert_id, symbol, report_period, event_type, event_date,
               forecast_text, np_change_min, np_change_max, np_change_mid,
               revenue_yoy, revenue_qoq, np_yoy, np_qoq,
               surprise_pct, industry_rank_pct, cf_to_np_ratio,
               sentiment, impact_score, headline, risk_tags,
               post_event_return_1d, post_event_return_5d
        FROM _tmp_ealts
        """
    )
    return len(alerts)


def backfill_post_event_returns(conn: duckdb.DuckDBPyConnection, lookback_days: int = 5) -> int:
    """T+1 / T+5 收盘价回填到 earnings_alerts.post_event_return_*。"""
    rows = conn.execute(
        """
        SELECT alert_id, symbol, event_date
        FROM earnings_alerts
        WHERE event_date >= CURRENT_DATE - INTERVAL '60 days'
          AND (post_event_return_1d IS NULL OR post_event_return_5d IS NULL)
        """
    ).fetchall()
    if not rows:
        return 0
    updated = 0
    for alert_id, symbol, event_date in rows:
        try:
            r1 = _post_return(conn, symbol, event_date, 1)
            r5 = _post_return(conn, symbol, event_date, 5)
            conn.execute(
                "UPDATE earnings_alerts SET post_event_return_1d = ?, post_event_return_5d = ? "
                "WHERE alert_id = ?",
                [r1, r5, alert_id],
            )
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"return backfill {alert_id} failed: {exc}")
    return updated


def _post_return(conn, symbol: str, event_date: date, n: int) -> float | None:
    """event_date 后 n 个交易日的累计收盘价收益率。"""
    rows = conn.execute(
        """
        SELECT trade_date, close FROM daily_price
        WHERE symbol = ? AND trade_date > ?
        ORDER BY trade_date ASC LIMIT ?
        """,
        [symbol, event_date, n + 1],
    ).fetchall()
    if len(rows) < n + 1:
        return None
    base = float(rows[0][1])
    final = float(rows[n][1])
    if base <= 0:
        return None
    return round(final / base - 1, 4)
