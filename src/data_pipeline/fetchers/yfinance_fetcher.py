"""
yfinance 备用采集器 — 港股行情（含指数）。
当 AkShare 反爬限制或接口异常时自动切换。
"""
import logging

import pandas as pd
import yfinance as yf
from loguru import logger

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_SOURCE_ERROR = "source_error"


def _with_status(df: pd.DataFrame, status: str, error: str = "") -> pd.DataFrame:
    df.attrs["source_status"] = status
    df.attrs["source_error"] = error
    return df


def source_status(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_status") or (STATUS_EMPTY if df.empty else STATUS_OK))


def source_error(df: pd.DataFrame) -> str:
    return str(df.attrs.get("source_error") or "")


def is_rate_limited(df: pd.DataFrame) -> bool:
    return source_status(df) == STATUS_RATE_LIMITED


def _classify_exception(exc: Exception) -> str:
    message = str(exc).lower()
    if "too many requests" in message or "rate limited" in message or "429" in message:
        return STATUS_RATE_LIMITED
    return STATUS_SOURCE_ERROR


def _hk_symbol_to_yfinance(symbol: str) -> str:
    """港股代码转换为 yfinance 格式: 00700 → 0700.HK"""
    code = str(int(symbol)).zfill(4)
    return f"{code}.HK"


def _cn_symbol_to_yfinance(symbol: str) -> str:
    """A股代码转换为 yfinance 格式: 000001 → 000001.SZ, 600519 → 600519.SS"""
    if symbol.startswith(("6", "5")):
        return f"{symbol}.SS"  # 上海
    else:
        return f"{symbol}.SZ"  # 深圳


def _fix_end_date(end_date: str) -> str:
    """yfinance history end 是 exclusive，加一天变为 inclusive"""
    from datetime import timedelta
    dt = pd.to_datetime(end_date) + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def fetch_cn_daily(symbol: str, start_date: str, end_date: str, log_empty: bool = True) -> pd.DataFrame:
    """拉取A股日线（通过 yfinance）"""
    try:
        ticker_str = _cn_symbol_to_yfinance(symbol)
        ticker = yf.Ticker(ticker_str)
        df = ticker.history(start=start_date, end=_fix_end_date(end_date))
        if df.empty:
            if log_empty:
                logger.warning(f"yfinance CN daily empty: {ticker_str}")
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "trade_date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["trade_date"] = df["trade_date"].dt.tz_localize(None)
        df["symbol"] = symbol
        return _with_status(df, STATUS_OK)
    except Exception as e:
        logger.warning(f"yfinance fetch CN daily skipped for {symbol}: {e}")
        return _with_status(pd.DataFrame(), _classify_exception(e), str(e))


def fetch_hk_daily(symbol: str, start_date: str, end_date: str, log_empty: bool = True) -> pd.DataFrame:
    """拉取港股日线（通过 yfinance）"""
    try:
        ticker_str = _hk_symbol_to_yfinance(symbol)
        ticker = yf.Ticker(ticker_str)
        df = ticker.history(start=start_date, end=_fix_end_date(end_date))
        if df.empty:
            if log_empty:
                logger.warning(f"yfinance HK daily empty: {ticker_str}")
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "trade_date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["trade_date"] = df["trade_date"].dt.tz_localize(None)
        df["symbol"] = symbol
        df["country"] = "HK"
        return _with_status(df, STATUS_OK)
    except Exception as e:
        logger.warning(f"yfinance fetch HK daily skipped for {symbol}: {e}")
        return _with_status(pd.DataFrame(), _classify_exception(e), str(e))


def fetch_hk_index_daily(index_yfinance_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取港股指数日线，如 ^HSI（恒生指数）、^HSTECH（恒生科技）"""
    try:
        ticker = yf.Ticker(index_yfinance_code)
        df = ticker.history(start=start_date, end=_fix_end_date(end_date))
        if df.empty:
            logger.warning(f"yfinance index daily empty: {index_yfinance_code}")
            return _with_status(pd.DataFrame(), STATUS_EMPTY)
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "trade_date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["trade_date"] = df["trade_date"].dt.tz_localize(None)
        df["index_code"] = index_yfinance_code
        return _with_status(df, STATUS_OK)
    except Exception as e:
        logger.warning(f"yfinance fetch index daily skipped for {index_yfinance_code}: {e}")
        return _with_status(pd.DataFrame(), _classify_exception(e), str(e))


def fetch_hk_components_hsi() -> list[str]:
    """获取恒生指数成分股列表"""
    try:
        # yfinance 无法直接获取成分股，通过维基百科备用
        tables = pd.read_html("https://en.wikipedia.org/wiki/Hang_Seng_Index")
        for table in tables:
            if "Ticker" in table.columns or "Code" in table.columns:
                code_col = "Ticker" if "Ticker" in table.columns else "Code"
                codes = table[code_col].dropna().astype(str).tolist()
                return [c.strip() for c in codes if c.strip().isdigit()]
        logger.warning("Could not parse HSI components from Wikipedia")
        return []
    except Exception as e:
        logger.warning(f"Fetch HSI components skipped: {e}")
        return []
