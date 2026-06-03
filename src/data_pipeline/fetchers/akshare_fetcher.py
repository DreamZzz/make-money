"""
AkShare 数据采集器 — A股/港股行情、基本信息、财务数据。
AkShare 接口可能随版本变动，捕获异常时自动降级。
"""
import os
import random
import re
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


def fetch_cn_stock_spot() -> pd.DataFrame:
    """Fetch A-share spot valuation fields for small-scope metadata repair."""
    try:
        df = ak.stock_zh_a_spot_em()
        return normalize_cn_stock_spot(df)
    except Exception as e:
        logger.warning(f"Fetch CN stock spot failed: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


def normalize_cn_stock_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    out = df.rename(columns={
        "代码": "symbol",
        "名称": "name",
        "市盈率-动态": "pe_ttm",
        "市盈率": "pe_ttm",
        "市净率": "pb",
        "总市值": "market_cap",
    }).copy()
    keep = [col for col in ["symbol", "name", "pe_ttm", "pb", "market_cap"] if col in out.columns]
    out = out[keep].copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    for col in ["pe_ttm", "pb"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "market_cap" in out.columns:
        out["market_cap"] = out["market_cap"].map(_market_cap_to_yi)
    out["country"] = "CN"
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_cn_stock_individual_info(symbol: str) -> pd.DataFrame:
    """Fetch per-symbol A-share metadata, mainly industry and market cap."""
    try:
        raw = ak.stock_individual_info_em(symbol=symbol)
        return normalize_cn_stock_individual_info(symbol, raw)
    except Exception as e:
        logger.warning(f"Fetch CN individual info failed for {symbol}: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


def normalize_cn_stock_individual_info(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df.columns) < 2:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    work = df.copy()
    if {"item", "value"}.issubset(work.columns):
        item_col, value_col = "item", "value"
    elif {"项目", "值"}.issubset(work.columns):
        item_col, value_col = "项目", "值"
    else:
        item_col, value_col = work.columns[:2]
    mapping = {
        str(row[item_col]).strip(): row[value_col]
        for _, row in work.iterrows()
        if pd.notna(row.get(item_col))
    }
    industry = (
        mapping.get("行业")
        or mapping.get("所属行业")
        or mapping.get("行业分类")
        or mapping.get("板块")
    )
    market_cap = mapping.get("总市值") or mapping.get("总市值(元)") or mapping.get("总市值（元）")
    name = mapping.get("股票简称") or mapping.get("简称") or mapping.get("名称")
    out = pd.DataFrame([{
        "symbol": str(symbol).zfill(6),
        "country": "CN",
        "name": None if pd.isna(name) else name,
        "industry": None if pd.isna(industry) else industry,
        "market_cap": _market_cap_to_yi(market_cap),
    }])
    return _with_status(out, STATUS_OK)


def _market_cap_to_yi(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return None
    multiplier = 1.0
    if text.endswith("万亿"):
        multiplier = 10_000.0
        text = text[:-2]
    elif text.endswith("亿"):
        multiplier = 1.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 1 / 10_000
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if multiplier != 1.0:
        return number * multiplier
    if abs(number) > 1_000_000:
        return number / 100_000_000
    return number


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
        raw = ak.stock_financial_abstract(symbol=symbol)
        return normalize_cn_financial_abstract(symbol, raw)
    except Exception as e:
        logger.error(f"Fetch financials failed for {symbol}: {e}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(e))


FINANCIAL_COLUMNS = [
    "symbol",
    "report_date",
    "revenue",
    "net_profit",
    "total_assets",
    "total_equity",
    "operating_cf",
    "roe",
    "roa",
    "gross_margin",
    "net_margin",
    "debt_ratio",
    "eps",
    "bvps",
]

_FINANCIAL_LABELS = {
    "revenue": ("营业总收入", "营业收入"),
    "net_profit": ("归母净利润", "归属于母公司所有者的净利润", "净利润"),
    "total_assets": ("资产总计", "总资产"),
    "total_equity": ("股东权益合计(净资产)", "所有者权益合计", "股东权益合计"),
    "operating_cf": ("经营现金流量净额", "经营活动产生的现金流量净额"),
    "roe": ("净资产收益率(ROE)", "净资产收益率_平均", "摊薄净资产收益率"),
    "roa": ("总资产报酬率(ROA)", "总资产报酬率", "总资产净利率_平均"),
    "gross_margin": ("毛利率", "销售毛利率"),
    "net_margin": ("销售净利率", "净利率"),
    "debt_ratio": ("资产负债率",),
    "eps": ("基本每股收益",),
    "bvps": ("每股净资产", "每股净资产_最新股数"),
}

_MONEY_FIELDS = {"revenue", "net_profit", "total_assets", "total_equity", "operating_cf"}


def normalize_cn_financial_abstract(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize AkShare stock_financial_abstract output into the financials schema."""
    if df.empty:
        return _with_status(pd.DataFrame(columns=FINANCIAL_COLUMNS), STATUS_EMPTY)
    indicator_col = _pick_financial_indicator_column(df)
    if indicator_col is None:
        return _with_status(pd.DataFrame(columns=FINANCIAL_COLUMNS), STATUS_EMPTY)

    work = df.copy()
    work[indicator_col] = work[indicator_col].astype(str).str.strip()
    report_cols = _financial_report_columns(work)
    if not report_cols:
        return _with_status(pd.DataFrame(columns=FINANCIAL_COLUMNS), STATUS_EMPTY)

    rows = []
    normalized_symbol = _normalize_cn_symbol(symbol)
    for report_col in report_cols:
        row = {
            "symbol": normalized_symbol,
            "report_date": pd.to_datetime(str(report_col), format="%Y%m%d").date(),
        }
        for field, labels in _FINANCIAL_LABELS.items():
            value = _financial_value_for_period(work, indicator_col, labels, report_col)
            row[field] = value / 100_000_000 if field in _MONEY_FIELDS and value is not None else value
        if row.get("total_assets") is None:
            row["total_assets"] = _derive_total_assets(row.get("total_equity"), row.get("debt_ratio"))
        rows.append(row)

    out = pd.DataFrame(rows, columns=FINANCIAL_COLUMNS)
    out = out.dropna(how="all", subset=[col for col in FINANCIAL_COLUMNS if col not in {"symbol", "report_date"}])
    out = out.sort_values("report_date", ascending=False).reset_index(drop=True)
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def _pick_financial_indicator_column(df: pd.DataFrame) -> str | None:
    for column in ("指标", "item", "项目", "metric"):
        if column in df.columns:
            return column
    return None


def _financial_report_columns(df: pd.DataFrame) -> list[str]:
    columns = []
    for column in df.columns:
        text = str(column)
        if re.fullmatch(r"\d{8}", text):
            try:
                pd.to_datetime(text, format="%Y%m%d")
            except ValueError:
                continue
            columns.append(column)
    return sorted(columns, key=lambda value: str(value), reverse=True)


def _financial_value_for_period(
    df: pd.DataFrame,
    indicator_col: str,
    labels: tuple[str, ...],
    report_col: str,
) -> float | None:
    indicators = df[indicator_col].astype(str).str.strip()
    for label in labels:
        matches = df.loc[indicators == label, report_col]
        for value in matches:
            parsed = _financial_number(value)
            if parsed is not None:
                return parsed
    return None


def _financial_number(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _derive_total_assets(total_equity: float | None, debt_ratio: float | None) -> float | None:
    if total_equity is None or debt_ratio is None:
        return None
    equity_ratio = 1 - debt_ratio / 100
    if equity_ratio <= 0:
        return None
    return total_equity / equity_ratio


def _normalize_cn_symbol(symbol: str) -> str:
    text = str(symbol).strip()
    if "." in text:
        left, right = text.split(".", 1)
        text = right if len(right) == 6 else left
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


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


# ============================================
# R1: 财报事件 fetcher (业绩预告 + 业绩快报 + 披露日历)
# ============================================

def fetch_cn_earnings_forecast(date_yyyymmdd: str) -> pd.DataFrame:
    """ak.stock_yjyg_em(date) → A 股业绩预告(报告期 date,如 20250630)。

    返回规整后的 DataFrame: symbol/report_period/forecast_text/np_change_min/
    np_change_max/event_date/source/...
    """
    import akshare as ak
    try:
        raw = ak.stock_yjyg_em(date=date_yyyymmdd)
    except Exception as e:
        logger.warning(f"fetch_cn_earnings_forecast({date_yyyymmdd}) failed: {e}")
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame()
    # 列名兼容多种(akshare 时常调整)
    sym_col = next((c for c in raw.columns if c in {"股票代码", "代码", "symbol"}), None)
    text_col = next((c for c in raw.columns if c in {"预测类型", "业绩变动类型", "预告类型", "forecast_text"}), None)
    npmin_col = next((c for c in raw.columns if c in {"预测指标净利润同比增长下限(%)", "净利润同比增长下限(%)", "预测净利润同比增长下限"}), None)
    npmax_col = next((c for c in raw.columns if c in {"预测指标净利润同比增长上限(%)", "净利润同比增长上限(%)", "预测净利润同比增长上限"}), None)
    ndate_col = next((c for c in raw.columns if c in {"公告日期", "公告日"}), None)
    if not sym_col:
        return pd.DataFrame()
    out["symbol"] = raw[sym_col].astype(str)
    out["report_period"] = pd.to_datetime(date_yyyymmdd, format="%Y%m%d").date()
    if text_col:
        out["forecast_text"] = raw[text_col].astype(str)
    out["np_change_min"] = pd.to_numeric(raw[npmin_col], errors="coerce") if npmin_col else None
    out["np_change_max"] = pd.to_numeric(raw[npmax_col], errors="coerce") if npmax_col else None
    if ndate_col:
        out["event_date"] = pd.to_datetime(raw[ndate_col], errors="coerce").dt.date
    else:
        out["event_date"] = pd.Timestamp.now().date()
    out["source"] = "akshare_yjyg"
    return out.dropna(subset=["symbol", "event_date"])


def fetch_cn_earnings_express(date_yyyymmdd: str) -> pd.DataFrame:
    """ak.stock_yjbb_em(date) → A 股业绩快报。"""
    import akshare as ak
    try:
        raw = ak.stock_yjbb_em(date=date_yyyymmdd)
    except Exception as e:
        logger.warning(f"fetch_cn_earnings_express({date_yyyymmdd}) failed: {e}")
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame()
    sym_col = next((c for c in raw.columns if c in {"股票代码", "代码", "symbol"}), None)
    rev_col = next((c for c in raw.columns if c in {"营业收入-营业收入", "营业总收入", "营业收入"}), None)
    np_col = next((c for c in raw.columns if c in {"净利润-净利润", "归母净利润", "净利润"}), None)
    rev_yoy_col = next((c for c in raw.columns if c in {"营业收入-同比增长", "营业总收入同比增长", "营业收入同比增长(%)"}), None)
    np_yoy_col = next((c for c in raw.columns if c in {"净利润-同比增长", "归母净利润同比增长", "净利润同比增长(%)"}), None)
    ndate_col = next((c for c in raw.columns if c in {"公告日期", "公告日", "最新公告日期"}), None)
    if not sym_col:
        return pd.DataFrame()
    out["symbol"] = raw[sym_col].astype(str)
    out["report_period"] = pd.to_datetime(date_yyyymmdd, format="%Y%m%d").date()
    out["revenue"] = pd.to_numeric(raw[rev_col], errors="coerce") if rev_col else None
    out["net_profit"] = pd.to_numeric(raw[np_col], errors="coerce") if np_col else None
    out["revenue_yoy"] = pd.to_numeric(raw[rev_yoy_col], errors="coerce") if rev_yoy_col else None
    out["np_yoy"] = pd.to_numeric(raw[np_yoy_col], errors="coerce") if np_yoy_col else None
    if ndate_col:
        out["event_date"] = pd.to_datetime(raw[ndate_col], errors="coerce").dt.date
    else:
        out["event_date"] = pd.Timestamp.now().date()
    out["source"] = "akshare_yjbb"
    return out.dropna(subset=["symbol", "event_date"])


def fetch_cn_earnings_disclosure_calendar(period: str, market: str = "沪深京") -> pd.DataFrame:
    """ak.stock_report_disclosure(market, period) → 财报披露日历。

    period 是 "2025三季报"/"2025年报"/"2026一季报" 等中文报告期。
    """
    import akshare as ak
    try:
        raw = ak.stock_report_disclosure(market=market, period=period)
    except Exception as e:
        logger.warning(f"fetch_cn_earnings_disclosure_calendar({period}) failed: {e}")
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = pd.DataFrame()
    sym_col = next((c for c in raw.columns if c in {"代码", "股票代码", "symbol"}), None)
    plan_col = next((c for c in raw.columns if c in {"最新披露日期", "预约披露日期", "披露日期", "实际披露日期"}), None)
    if not sym_col or not plan_col:
        return pd.DataFrame()
    out["symbol"] = raw[sym_col].astype(str)
    out["disclosure_date"] = pd.to_datetime(raw[plan_col], errors="coerce").dt.date
    out["disclosure_type"] = _disclosure_type_from_period(period)
    out["report_period"] = _report_period_from_period(period)
    out["source"] = "akshare_disclosure"
    return out.dropna(subset=["symbol", "disclosure_date"])


def _disclosure_type_from_period(period: str) -> str:
    if "一季" in period:
        return "quarterly"
    if "三季" in period:
        return "quarterly"
    if "中" in period or "半年" in period:
        return "semi_annual"
    if "年报" in period:
        return "annual"
    return "other"


def _report_period_from_period(period: str):
    import re
    m = re.match(r"(\d{4})", period)
    if not m:
        return None
    year = int(m.group(1))
    if "一季" in period:
        return pd.Timestamp(year=year, month=3, day=31).date()
    if "中" in period or "半年" in period:
        return pd.Timestamp(year=year, month=6, day=30).date()
    if "三季" in period:
        return pd.Timestamp(year=year, month=9, day=30).date()
    if "年报" in period:
        return pd.Timestamp(year=year, month=12, day=31).date()
    return None
