"""Free-source fetchers and normalizers used for validation-period probes.

These adapters are intentionally conservative: they normalize third-party
responses and expose source status, but do not change the production trading
decision path by themselves.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from importlib.util import find_spec
from typing import Any

import pandas as pd
from loguru import logger

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_SOURCE_ERROR = "source_error"


def _with_status(df: pd.DataFrame, status: str, error: str = "") -> pd.DataFrame:
    df.attrs["source_status"] = status
    df.attrs["source_error"] = error
    return df


def source_status(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_status") or (STATUS_EMPTY if df.empty else STATUS_OK))


def source_error(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_error") or "")


def _normalize_symbol(symbol: str | int) -> str:
    return str(symbol).strip().zfill(6)


def tencent_symbol(symbol: str | int) -> str:
    """Return Tencent/gu.qq.com market-prefixed A-share symbol."""
    code = _normalize_symbol(symbol)
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def _to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _first_existing(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def normalize_tencent_daily(symbol: str | int, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize AkShare Tencent daily data into the local daily_price shape."""
    if df.empty:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)

    rename = {
        "date": "trade_date",
        "日期": "trade_date",
        "open": "open",
        "开盘": "open",
        "high": "high",
        "最高": "high",
        "low": "low",
        "最低": "low",
        "close": "close",
        "收盘": "close",
        "volume": "volume",
        "成交量": "volume",
        "amount": "amount",
        "成交额": "amount",
    }
    out = df.rename(columns=rename).copy()
    keep = [col for col in ["trade_date", "open", "high", "low", "close", "volume", "amount"] if col in out.columns]
    if "trade_date" not in keep or not {"open", "high", "low", "close"}.issubset(set(keep)):
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, "missing required Tencent daily columns")

    out = out[keep].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = _to_number(out[col])
    out["symbol"] = _normalize_symbol(symbol)
    out["country"] = "CN"
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_tencent_cn_daily(
    symbol: str | int,
    start_date: str | date,
    end_date: str | date,
    adjust: str = "",
    provider: Callable[..., pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Fetch A-share daily bars from Tencent Finance via AkShare."""
    try:
        if provider is None:
            import akshare as ak

            provider = ak.stock_zh_a_hist_tx
        start = pd.to_datetime(start_date).strftime("%Y%m%d")
        end = pd.to_datetime(end_date).strftime("%Y%m%d")
        raw = provider(symbol=tencent_symbol(symbol), start_date=start, end_date=end, adjust=adjust)
        return normalize_tencent_daily(symbol, raw)
    except Exception as exc:
        logger.warning(f"Fetch Tencent CN daily failed for {symbol}: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def normalize_tencent_quote_snapshot(text: str) -> pd.DataFrame:
    """Normalize Tencent quote text into valuation/market-cap fields.

    Tencent quote fields are not an official contract; for the validation
    period we only consume stable low-frequency fields observed in the public
    quote payload: code/name, PE, total market cap, and PB.
    """
    rows = []
    for match in re.finditer(r'v_[a-z]{2}\d{6}="([^"]*)"', text or ""):
        parts = match.group(1).split("~")
        if len(parts) < 47:
            continue
        rows.append({
            "symbol": str(parts[2]).zfill(6),
            "name": parts[1] or None,
            "pe_ttm": _safe_number(parts[39]),
            "market_cap": _safe_number(parts[45]),
            "pb": _safe_number(parts[46]),
            "country": "CN",
        })
    if not rows:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    return _with_status(pd.DataFrame(rows), STATUS_OK)


def fetch_tencent_quote_snapshot(
    symbols: list[str | int],
    requester: Callable[..., Any] | None = None,
    chunk_size: int = 80,
) -> pd.DataFrame:
    """Fetch Tencent quote valuation snapshot for A-share symbols."""
    normalized = [_normalize_symbol(symbol) for symbol in symbols]
    if not normalized:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    try:
        if requester is None:
            import requests

            requester = requests.get
        frames = []
        for idx in range(0, len(normalized), max(int(chunk_size), 1)):
            chunk = normalized[idx: idx + max(int(chunk_size), 1)]
            query = ",".join(tencent_symbol(symbol) for symbol in chunk)
            response = requester("https://qt.gtimg.cn/q=" + query, timeout=8)
            text = response.text if hasattr(response, "text") else str(response)
            frame = normalize_tencent_quote_snapshot(text)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        return _with_status(pd.concat(frames, ignore_index=True), STATUS_OK)
    except Exception as exc:
        logger.warning(f"Fetch Tencent quote snapshot failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def _safe_number(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def normalize_eastmoney_research_reports(symbol: str | int, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Eastmoney stock research reports into a stable research shape."""
    if df.empty:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)

    work = df.copy()
    columns = list(work.columns)
    eps_col = _first_existing(columns, [col for col in columns if "盈利预测-收益" in col])
    pe_col = _first_existing(columns, [col for col in columns if "盈利预测-市盈率" in col])
    rename = {
        "股票代码": "symbol",
        "股票简称": "name",
        "报告名称": "report_title",
        "东财评级": "rating",
        "评级": "rating",
        "机构": "institution",
        "行业": "industry",
        "日期": "report_date",
        "报告PDF链接": "source_url",
    }
    if eps_col:
        rename[eps_col] = "eps_forecast_year_1"
    if pe_col:
        rename[pe_col] = "pe_forecast_year_1"

    out = work.rename(columns=rename)
    for col in [
        "symbol",
        "name",
        "report_title",
        "rating",
        "institution",
        "industry",
        "report_date",
        "eps_forecast_year_1",
        "pe_forecast_year_1",
        "source_url",
    ]:
        if col not in out.columns:
            out[col] = None
    out = out[[
        "symbol",
        "name",
        "report_title",
        "rating",
        "institution",
        "industry",
        "report_date",
        "eps_forecast_year_1",
        "pe_forecast_year_1",
        "source_url",
    ]].copy()
    out["symbol"] = out["symbol"].fillna(_normalize_symbol(symbol)).astype(str).str.zfill(6)
    out["report_date"] = pd.to_datetime(out["report_date"], errors="coerce")
    out["eps_forecast_year_1"] = _to_number(out["eps_forecast_year_1"])
    out["pe_forecast_year_1"] = _to_number(out["pe_forecast_year_1"])
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_eastmoney_research_reports(
    symbol: str | int,
    provider: Callable[..., pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Fetch Eastmoney stock research reports via AkShare."""
    try:
        if provider is None:
            import akshare as ak

            provider = ak.stock_research_report_em
        raw = provider(symbol=_normalize_symbol(symbol))
        return normalize_eastmoney_research_reports(symbol, raw)
    except Exception as exc:
        logger.warning(f"Fetch Eastmoney research reports failed for {symbol}: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def normalize_ths_concept_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize TongHuaShun concept timeline / theme summary rows."""
    if df.empty:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    out = df.copy()
    rename = {
        "日期": "event_date",
        "概念": "theme",
        "概念名称": "theme",
        "题材": "theme",
        "成分股": "constituents",
        "原因": "reason",
        "驱动事件": "reason",
        "链接": "source_url",
    }
    out = out.rename(columns=rename)
    for col in ["event_date", "theme", "constituents", "reason", "source_url"]:
        if col not in out.columns:
            out[col] = None
    out = out[["event_date", "theme", "constituents", "reason", "source_url"]].copy()
    out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce")
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_ths_concept_summary(provider: Callable[..., pd.DataFrame] | None = None) -> pd.DataFrame:
    """Fetch TongHuaShun concept timeline via AkShare."""
    try:
        if provider is None:
            import akshare as ak

            provider = ak.stock_board_concept_summary_ths
        raw = provider()
        return normalize_ths_concept_summary(raw)
    except Exception as exc:
        logger.warning(f"Fetch THS concept summary failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def is_mootdx_available() -> bool:
    return find_spec("mootdx") is not None


def normalize_mootdx_daily(symbol: str | int, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize mootdx/TDX daily bars into the local daily_price shape."""
    if df.empty:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    out = df.reset_index() if df.index.name in {"date", "datetime"} or isinstance(df.index, pd.DatetimeIndex) else df.copy()
    rename = {
        "index": "trade_date",
        "date": "trade_date",
        "datetime": "trade_date",
        "time": "trade_date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
        "volume": "volume",
        "amount": "amount",
    }
    out = out.rename(columns=rename)
    keep = [col for col in ["trade_date", "open", "high", "low", "close", "volume", "amount"] if col in out.columns]
    if "trade_date" not in keep or not {"open", "high", "low", "close"}.issubset(set(keep)):
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, "missing required mootdx daily columns")
    out = out[keep].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = _to_number(out[col])
    out["symbol"] = _normalize_symbol(symbol)
    out["country"] = "CN"
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_mootdx_cn_daily(
    symbol: str | int,
    start_date: str | date,
    end_date: str | date,
    client: Any | None = None,
) -> pd.DataFrame:
    """Fetch A-share daily bars from mootdx when the optional dependency exists."""
    if client is None and not is_mootdx_available():
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, "mootdx is not installed")
    try:
        if client is None:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
        raw = client.bars(symbol=_normalize_symbol(symbol), frequency=9, offset=0, count=800)
        df = normalize_mootdx_daily(symbol, raw)
        if df.empty:
            return df
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)].copy()
        return _with_status(df, STATUS_OK if not df.empty else STATUS_EMPTY, source_error(df))
    except Exception as exc:
        logger.warning(f"Fetch mootdx CN daily failed for {symbol}: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))
