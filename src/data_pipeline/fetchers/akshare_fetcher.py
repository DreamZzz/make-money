"""
AkShare 数据采集器 — A股/港股行情、基本信息、财务数据。
AkShare 接口可能随版本变动，捕获异常时自动降级。
"""
import os
import random
import threading
import time

import akshare as ak
import pandas as pd
from loguru import logger

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_SOURCE_ERROR = "source_error"

_CN_DAILY_LOCK = threading.Lock()
_LAST_CN_DAILY_CALL = 0.0
_CN_DAILY_MIN_INTERVAL = float(os.environ.get("AKSHARE_CN_DAILY_MIN_INTERVAL", "0.8"))


def _with_status(df: pd.DataFrame, status: str, error: str = "") -> pd.DataFrame:
    df.attrs["source_status"] = status
    df.attrs["source_error"] = error
    return df


def source_status(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_status") or (STATUS_EMPTY if df.empty else STATUS_OK))


def source_error(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_error") or "")


def is_transient_source_error(df: pd.DataFrame) -> bool:
    return source_status(df) == STATUS_SOURCE_ERROR and _is_transient_network_error(Exception(source_error(df)))


def configure_cn_daily_rate_limit(min_interval_seconds: float) -> None:
    global _CN_DAILY_MIN_INTERVAL
    _CN_DAILY_MIN_INTERVAL = max(float(min_interval_seconds), 0.0)


def _is_transient_network_error(exc: Exception) -> bool:
    message = str(exc).lower()
    needles = [
        "max retries exceeded",
        "failed to establish a new connection",
        "nodename nor servname provided",
        "name or service not known",
        "connection aborted",
        "connection reset",
        "timed out",
        "timeout",
    ]
    return any(needle in message for needle in needles)


def _throttle_cn_daily_request() -> None:
    """Keep AkShare/Eastmoney requests below a conservative per-process pace."""
    global _LAST_CN_DAILY_CALL
    with _CN_DAILY_LOCK:
        now = time.monotonic()
        wait = _CN_DAILY_MIN_INTERVAL - (now - _LAST_CN_DAILY_CALL)
        if wait > 0:
            time.sleep(wait + random.uniform(0.05, 0.25))
        _LAST_CN_DAILY_CALL = time.monotonic()


def _transient_backoff(attempt: int) -> None:
    base = min(8.0, 1.5 * (2 ** attempt))
    time.sleep(base + random.uniform(0.2, 0.8))


def fetch_cn_stock_daily(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
    log_empty: bool = True,
) -> pd.DataFrame:
    """
    拉取A股日线数据。
    symbol: 纯数字代码，如 "600519"
    adjust: qfq=前复权 / hfq=后复权 / ""=不复权
    """
    last_error = ""
    for attempt in range(3):
        try:
            _throttle_cn_daily_request()
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date,
                                    end_date=end_date, adjust=adjust)
            if df.empty:
                if log_empty:
                    logger.warning(f"AkShare CN daily empty: {symbol}")
                return _with_status(pd.DataFrame(), STATUS_EMPTY)
            df = df.rename(columns={
                "日期": "trade_date", "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "成交额": "amount",
                "振幅": "amplitude", "涨跌幅": "pct_chg", "涨跌额": "change",
                "换手率": "turnover_rate",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["symbol"] = symbol
            return _with_status(df, STATUS_OK)
        except Exception as e:
            last_error = str(e)
            if attempt < 2 and _is_transient_network_error(e):
                _transient_backoff(attempt)
                continue
            log_fn = logger.warning if _is_transient_network_error(e) else logger.error
            log_fn(f"Fetch CN daily failed for {symbol}: {e}")
            return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, last_error)
    return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, last_error)


def fetch_hk_stock_daily(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
    log_empty: bool = True,
) -> pd.DataFrame:
    """
    拉取港股日线数据。
    symbol: 港股代码，如 "00700"
    """
    try:
        df = ak.stock_hk_hist(symbol=symbol, period="daily", start_date=start_date,
                              end_date=end_date, adjust=adjust)
        if df.empty:
            if log_empty:
                logger.warning(f"AkShare HK daily empty: {symbol}")
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        df = df.rename(columns={
            "日期": "trade_date", "开盘": "open", "最高": "high", "最低": "low",
            "收盘": "close", "成交量": "volume", "成交额": "amount",
            "涨跌幅": "pct_chg", "换手率": "turnover_rate",
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["symbol"] = symbol
        df["country"] = "HK"
        return _with_status(df, STATUS_OK)
    except Exception as e:
        logger.error(f"Fetch HK daily failed for {symbol}: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


def fetch_cn_stock_info() -> pd.DataFrame:
    """获取A股股票基本信息（代码+名称）"""
    try:
        df = ak.stock_info_a_code_name()
        df = df.rename(columns={"code": "symbol"})
        df["country"] = "CN"
        logger.info(f"Fetched {len(df)} CN stock info records")
        return df
    except Exception as e:
        logger.error(f"Fetch CN stock info failed: {e}")
        return pd.DataFrame()


def fetch_hk_stock_info() -> pd.DataFrame:
    """获取港股股票基本信息"""
    try:
        df = ak.stock_hk_spot_em()
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "代码": "symbol", "名称": "name", "最新价": "close",
            "涨跌幅": "pct_chg", "成交量": "volume", "成交额": "amount",
            "市盈率-动态": "pe_ttm",
        })
        df["country"] = "HK"
        return df[["symbol", "name", "country", "pe_ttm"]]
    except Exception as e:
        logger.error(f"Fetch HK stock info failed: {e}")
        return pd.DataFrame()


def fetch_cn_index_daily(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取A股指数日线，如 000300（沪深300）"""
    try:
        df = ak.stock_zh_index_daily(symbol=f"sh{index_code}" if index_code.startswith("000")
                                     else f"sz{index_code}")
        # AkShare 返回的是完整历史，手动截断
        df = df.rename(columns={"date": "trade_date", "open": "open", "high": "high",
                                "low": "low", "close": "close", "volume": "volume"})
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
        df["index_code"] = index_code
        return df
    except Exception as e:
        logger.error(f"Fetch CN index daily failed for {index_code}: {e}")
        return pd.DataFrame()


def fetch_hk_index_daily_sina(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取港股指数日线，如 HSI（恒生指数）、HSTECH（恒生科技指数）。"""
    try:
        df = ak.stock_hk_index_daily_sina(symbol=index_code)
        if df.empty:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        df = df.rename(columns={
            "date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)].copy()
        df["index_code"] = index_code
        return _with_status(df, STATUS_OK if not df.empty else STATUS_EMPTY)
    except Exception as e:
        logger.warning(f"Fetch HK index daily via Sina failed for {index_code}: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


def fetch_index_components(index_code: str) -> list[str]:
    """获取指数成分股列表"""
    try:
        df = ak.index_stock_cons(index_code)
        if df.empty:
            return []
        col = "品种代码" if "品种代码" in df.columns else df.columns[0]
        return df[col].astype(str).tolist()
    except Exception as e:
        logger.error(f"Fetch index components failed for {index_code}: {e}")
        return []


def fetch_index_member_snapshot(index_code: str) -> pd.DataFrame:
    """Fetch and normalize the dated CSIndex constituent snapshot."""
    from src.data_pipeline.index_membership import normalize_index_constituent_snapshot

    try:
        df = ak.index_stock_cons_csindex(index_code)
        if df.empty:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        out = normalize_index_constituent_snapshot(index_code, df, source="csindex_snapshot")
        return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)
    except Exception as e:
        logger.warning(f"Fetch CSIndex member snapshot failed for {index_code}: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


def fetch_cn_financials(symbol: str) -> pd.DataFrame:
    """获取A股财务数据（资产负债表+利润表主要指标）"""
    try:
        df = ak.stock_financial_abstract(symbol=symbol)
        if df.empty:
            return pd.DataFrame()
        df["symbol"] = symbol
        return df
    except Exception as e:
        logger.error(f"Fetch financials failed for {symbol}: {e}")
        return pd.DataFrame()


def fetch_cn_industry() -> pd.DataFrame:
    """获取A股行业分类"""
    try:
        df = ak.stock_board_industry_name_em()
        if df.empty:
            return pd.DataFrame()
        return df.rename(columns={"板块名称": "industry", "股票代码": "symbol", "股票名称": "name"})
    except Exception as e:
        logger.error(f"Fetch CN industry failed: {e}")
        return pd.DataFrame()
