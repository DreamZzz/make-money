"""R5: 财报模块每日运维入口。

供 daily_close (step 6b) 和 scheduler_watchdog (7:30 早盘) 调用。

模式:
- morning: 拉前一交易日盘后披露的业绩预告 + 业绩快报 + 刷新日历 + analyzer 落 earnings_alerts
- close:   主财报增量回填(200/天滚动)+ post_event_return 回填 + value_quality 信号生成
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import Any

from loguru import logger


def _disable_proxy() -> None:
    """与 fund_etf_provider / scheduler_watchdog 一致 — 拉外部数据时清 proxy。"""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "*"


def run_morning(conn, *, as_of: date | None = None) -> dict[str, Any]:
    """早盘 7:30: 拉昨日披露预告 + 快报 + 刷新日历。"""
    from src.data_pipeline.fetchers.akshare_fetcher import (
        fetch_cn_earnings_express,
        fetch_cn_earnings_forecast,
    )
    from src.financials.analyzer import analyze_express, analyze_forecast, persist_alerts
    from src.financials.calendar import build_earnings_calendar
    from src.financials.universe import load_earnings_universe

    _disable_proxy()
    as_of = as_of or date.today()
    universe_set = set(load_earnings_universe(conn))
    result: dict[str, Any] = {"status": "OK", "as_of": as_of.isoformat()}

    # 1. 刷新日历
    try:
        cal_n = build_earnings_calendar(conn, days_ahead=30, days_back=7, today=as_of)
        result["calendar_rows"] = cal_n
        logger.info(f"earnings calendar: {cal_n} rows")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"calendar build failed: {exc}")
        result["calendar_rows"] = 0
        result["calendar_error"] = str(exc)

    # 2. 业绩预告(报告期主要是当年中报/年报)
    forecast_alerts = []
    for period_label in _current_report_periods(as_of):
        try:
            df = fetch_cn_earnings_forecast(period_label)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"forecast {period_label} failed: {exc}")
            continue
        if df.empty:
            continue
        df = df[df["symbol"].astype(str).isin(universe_set)]
        for _, row in df.iterrows():
            try:
                forecast_alerts.append(analyze_forecast(row))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"analyze_forecast failed: {exc}")
    if forecast_alerts:
        persist_alerts(conn, forecast_alerts)
    result["forecast_alerts"] = len(forecast_alerts)
    logger.info(f"earnings forecasts: {len(forecast_alerts)} alerts persisted")

    # 3. 业绩快报
    express_alerts = []
    for period_label in _current_report_periods(as_of):
        try:
            df = fetch_cn_earnings_express(period_label)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"express {period_label} failed: {exc}")
            continue
        if df.empty:
            continue
        df = df[df["symbol"].astype(str).isin(universe_set)]
        # 拼上 forecast_np_mid 作 surprise base(同 symbol+同 report_period 取最新 forecast 中值)
        for _, row in df.iterrows():
            try:
                forecast_mid = _lookup_forecast_mid(conn, row["symbol"], row["report_period"])
                express_alerts.append(analyze_express(row, forecast_np_mid=forecast_mid))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"analyze_express failed: {exc}")
    if express_alerts:
        persist_alerts(conn, express_alerts)
    result["express_alerts"] = len(express_alerts)
    logger.info(f"earnings express: {len(express_alerts)} alerts persisted")

    return result


def run_close(conn, *, limit: int = 200, as_of: date | None = None) -> dict[str, Any]:
    """收盘 step 6b: 主财报增量 + post_event_return 回填 + value_quality 信号生成。"""
    from src.financials.analyzer import backfill_post_event_returns
    from src.financials.value_quality_runner import (
        generate_and_persist_value_quality_signals,
    )

    _disable_proxy()
    result: dict[str, Any] = {"status": "OK"}

    # 1. 主财报增量回填(R7 接入,目前留空 stub)
    # backfill 现有 financials_backfill 已有,但需要在 universe 范围内;简化为只更 universe 标的
    result["financials_backfilled"] = 0  # R7 阶段接入

    # 2. post_event_return 回填
    try:
        n = backfill_post_event_returns(conn, lookback_days=10)
        result["returns_backfilled"] = n
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"return backfill failed: {exc}")
        result["returns_backfilled"] = 0

    # 3. value_quality 信号
    try:
        n_vq = generate_and_persist_value_quality_signals(conn, top_n=20, min_score=0.6)
        result["value_quality_signals"] = n_vq
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"value_quality signal generation failed: {exc}")
        result["value_quality_signals"] = 0

    return result


def _current_report_periods(today: date) -> list[str]:
    """返回需要查询的报告期(YYYYMMDD 格式,akshare yjyg/yjbb 接口的 date 参数)。"""
    year = today.year
    periods = []
    # 上一年年报(Jan-May 还在披露)
    if today.month <= 5:
        periods.append(f"{year - 1}1231")
    # 当年一季报(Apr-Aug)
    if 3 <= today.month <= 8:
        periods.append(f"{year}0331")
    # 当年中报(Jul-Nov)
    if 6 <= today.month <= 11:
        periods.append(f"{year}0630")
    # 当年三季报(Oct-Dec)
    if 9 <= today.month <= 12:
        periods.append(f"{year}0930")
    return periods or [f"{year - 1}1231"]


def _lookup_forecast_mid(conn, symbol: str, report_period: date) -> float | None:
    """同 symbol + 同报告期的最新 forecast 中值,供快报算 surprise。"""
    row = conn.execute(
        "SELECT np_change_mid FROM earnings_alerts "
        "WHERE symbol = ? AND report_period = ? AND event_type = 'FORECAST' "
        "ORDER BY event_date DESC LIMIT 1",
        [symbol, report_period],
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["morning", "close"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)

    from src.data_pipeline.loader import get_connection, init_db
    conn = get_connection()
    try:
        init_db(conn)
        if args.mode == "morning":
            out = run_morning(conn)
        else:
            out = run_close(conn, limit=args.limit)
        print(out)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
