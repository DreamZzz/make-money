"""AkShare-backed index fund NAV and ETF price fetching."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from loguru import logger

from src.index_funds.config import FundWatchItem


def default_fetch_dates(history_years: int = 5) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=history_years * 365)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_fund_nav(item: FundWatchItem, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch and normalize one ETF/open-fund NAV series.

    Empty fund_code is valid in the default watchlist and means the user has not
    configured a real fund yet; in that case no remote call is attempted.
    """
    if not item.fund_code:
        return pd.DataFrame(columns=["fund_code", "trade_date", "nav", "close", "premium_discount"])

    import akshare as ak

    try:
        if item.fund_type == "ETF":
            raw = ak.fund_etf_hist_em(
                symbol=item.fund_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            return normalize_etf_nav(item.fund_code, raw)
        raw = ak.fund_open_fund_info_em(
            symbol=item.fund_code,
            indicator="单位净值走势",
            period="成立来",
        )
        return normalize_open_fund_nav(item.fund_code, raw, start_date, end_date)
    except Exception as exc:
        logger.warning(f"Index fund NAV fetch failed for {item.fund_code}: {exc}")
        return pd.DataFrame(columns=["fund_code", "trade_date", "nav", "close", "premium_discount"])


def normalize_etf_nav(fund_code: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["fund_code", "trade_date", "nav", "close", "premium_discount"])
    df = pd.DataFrame()
    df["trade_date"] = pd.to_datetime(_pick_col(raw, ["日期", "date", "trade_date"]).to_numpy())
    close = pd.to_numeric(_pick_col(raw, ["收盘", "close", "单位净值"]).to_numpy(), errors="coerce")
    df["fund_code"] = fund_code
    df["nav"] = close
    df["close"] = close
    premium = _maybe_col(raw, ["溢价率", "折价率", "premium_discount"])
    df["premium_discount"] = pd.to_numeric(premium.to_numpy(), errors="coerce") if premium is not None else None
    return _clean_nav(df)


def normalize_open_fund_nav(fund_code: str, raw: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["fund_code", "trade_date", "nav", "close", "premium_discount"])
    df = pd.DataFrame()
    df["trade_date"] = pd.to_datetime(_pick_col(raw, ["净值日期", "日期", "trade_date"]).to_numpy())
    nav = pd.to_numeric(_pick_col(raw, ["单位净值", "nav"]).to_numpy(), errors="coerce")
    df["fund_code"] = fund_code
    df["nav"] = nav
    df["close"] = nav
    df["premium_discount"] = None
    df = _clean_nav(df)
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    return df[(df["trade_date"] >= start) & (df["trade_date"] <= end)].reset_index(drop=True)


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    col = _maybe_col(df, candidates)
    if col is None:
        raise KeyError(f"missing columns: {candidates}")
    return col


def _maybe_col(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return df[normalized[key]]
    return None


def _clean_nav(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return (
        df.dropna(subset=["nav", "close"])
        .sort_values("trade_date")
        .drop_duplicates(["fund_code", "trade_date"], keep="last")
        .reset_index(drop=True)
    )
