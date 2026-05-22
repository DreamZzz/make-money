"""Free-source fetchers and normalizers used for validation-period probes.

These adapters are intentionally conservative: they normalize third-party
responses and expose source status, but do not change the production trading
decision path by themselves.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import date
from importlib.util import find_spec
from io import StringIO
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


def _normalize_cn_symbol(value: Any) -> str:
    text = str(value).strip()
    match = re.search(r"\d{6}", text)
    if match:
        return match.group(0)
    return text.zfill(6)


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


def normalize_industry_members(df: pd.DataFrame, industry: str, source: str) -> pd.DataFrame:
    """Normalize industry-board constituents into a symbol-to-industry map."""
    if df.empty or not str(industry or "").strip():
        return _with_status(pd.DataFrame(), STATUS_EMPTY)

    symbol_values = _first_non_missing_values(df, ["symbol", "代码", "股票代码", "证券代码", "成分股代码"])
    name_values = _first_non_missing_values(df, ["name", "名称", "股票名称", "证券简称", "股票简称", "成分股名称"])
    if symbol_values is None:
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, "missing industry member symbol column")

    out = pd.DataFrame()
    out["symbol"] = symbol_values.map(_normalize_cn_symbol)
    out["name"] = name_values.where(name_values.notna(), None) if name_values is not None else None
    out["industry"] = str(industry).strip()
    out["country"] = "CN"
    out["source"] = source
    out = out[out["symbol"].astype(str).str.match(r"^\d{6}$", na=False)].copy()
    out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY)


def fetch_eastmoney_industry_members(
    provider_names: Callable[..., pd.DataFrame] | None = None,
    provider_cons: Callable[..., pd.DataFrame] | None = None,
    sleep_seconds: float = 0.2,
    max_industries: int | None = None,
) -> pd.DataFrame:
    """Fetch Eastmoney industry-board constituents as a bulk industry map."""
    try:
        if provider_names is None or provider_cons is None:
            import akshare as ak

            provider_names = provider_names or ak.stock_board_industry_name_em
            provider_cons = provider_cons or ak.stock_board_industry_cons_em
        raw_names = provider_names()
        industries = _extract_industry_names(raw_names)
        if max_industries is not None:
            industries = industries[: max(int(max_industries), 0)]
        if not industries:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)

        frames = []
        errors = []
        for idx, industry in enumerate(industries):
            try:
                try:
                    raw_members = provider_cons(symbol=industry)
                except TypeError:
                    raw_members = provider_cons(industry)
                frame = normalize_industry_members(
                    raw_members if raw_members is not None else pd.DataFrame(),
                    industry=industry,
                    source="eastmoney_industry_board",
                )
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"{industry}: {exc}")
                logger.warning(f"Fetch Eastmoney industry members failed for {industry}: {exc}")
            if sleep_seconds > 0 and idx < len(industries) - 1:
                time.sleep(sleep_seconds)

        if not frames:
            status = STATUS_SOURCE_ERROR if errors else STATUS_EMPTY
            return _with_status(pd.DataFrame(), status, "; ".join(errors[:5]))
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
        return _with_status(out, STATUS_OK, "; ".join(errors[:5]))
    except Exception as exc:
        logger.warning(f"Fetch Eastmoney industry board map failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def fetch_legulegu_sw_industry_members(
    provider_first_info: Callable[..., pd.DataFrame] | None = None,
    requester: Callable[..., Any] | None = None,
    sleep_seconds: float = 0.2,
    target_symbols: list[str | int] | None = None,
    max_industries: int | None = None,
) -> pd.DataFrame:
    """Fetch SW first-level industry constituents from Legulegu industry pages."""
    try:
        if provider_first_info is None:
            import akshare as ak

            provider_first_info = ak.sw_index_first_info
        if requester is None:
            import requests

            requester = requests.get

        industries = _extract_sw_industries(provider_first_info())
        if max_industries is not None:
            industries = industries[: max(int(max_industries), 0)]
        if not industries:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)

        target_set = set(_normalize_symbol(symbol) for symbol in (target_symbols or []))
        frames = []
        found: set[str] = set()
        errors = []
        for idx, item in enumerate(industries):
            code = item["code"]
            industry = item["industry"]
            try:
                html = _request_text(
                    requester,
                    f"https://legulegu.com/stockdata/index-composition?industryCode={code}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                table = _first_symbol_table(html)
                frame = normalize_industry_members(
                    table,
                    industry=industry,
                    source="legulegu_sw_first",
                )
                if not frame.empty:
                    frames.append(frame)
                    found.update(frame["symbol"].astype(str).tolist())
                    if target_set and target_set.issubset(found):
                        return _combine_industry_frames(frames, errors)
            except Exception as exc:
                errors.append(f"{industry}: {exc}")
                logger.warning(f"Fetch Legulegu SW industry members failed for {industry}: {exc}")
            if sleep_seconds > 0 and idx < len(industries) - 1:
                time.sleep(sleep_seconds)
        return _combine_industry_frames(frames, errors)
    except Exception as exc:
        logger.warning(f"Fetch Legulegu SW industry map failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def fetch_ths_industry_members(
    provider_names: Callable[..., pd.DataFrame] | None = None,
    requester: Callable[..., Any] | None = None,
    sleep_seconds: float = 0.2,
    max_industries: int | None = None,
    max_pages: int = 8,
    target_symbols: list[str | int] | None = None,
) -> pd.DataFrame:
    """Fetch TongHuaShun industry-board constituents by parsing board pages."""
    try:
        if requester is None:
            import requests

            requester = requests.get

        boards = _fetch_ths_industry_boards(requester) if provider_names is None else _extract_ths_boards(provider_names())
        if max_industries is not None:
            boards = boards[: max(int(max_industries), 0)]
        if not boards:
            return _with_status(pd.DataFrame(), STATUS_EMPTY)

        target_set = set(_normalize_symbol(symbol) for symbol in (target_symbols or []))
        frames = []
        found: set[str] = set()
        errors = []
        for board_idx, board in enumerate(boards):
            industry = board["industry"]
            code = board["code"]
            for page in range(1, max(int(max_pages), 1) + 1):
                try:
                    html = _request_text(
                        requester,
                        _ths_industry_detail_url(code, page),
                        headers={
                            "User-Agent": "Mozilla/5.0",
                            "Referer": "http://q.10jqka.com.cn/thshy/",
                        },
                        timeout=10,
                    )
                    table = _first_symbol_table(html)
                    if table.empty:
                        break
                    frame = normalize_industry_members(table, industry=industry, source="ths_industry_board")
                    if frame.empty:
                        break
                    frames.append(frame)
                    found.update(frame["symbol"].astype(str).tolist())
                    if target_set and target_set.issubset(found):
                        return _combine_industry_frames(frames, errors)
                    if len(frame) < 20:
                        break
                except Exception as exc:
                    errors.append(f"{industry}/{page}: {exc}")
                    logger.warning(f"Fetch THS industry members failed for {industry} page {page}: {exc}")
                    break
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            if sleep_seconds > 0 and board_idx < len(boards) - 1:
                time.sleep(sleep_seconds)
        return _combine_industry_frames(frames, errors)
    except Exception as exc:
        logger.warning(f"Fetch THS industry board map failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def fetch_cninfo_industry_members(
    symbols: list[str | int],
    provider: Callable[..., pd.DataFrame] | None = None,
    start_date: str = "20200101",
    end_date: str | None = None,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    """Fetch per-symbol industry classification from CNINFO as a low-volume fallback."""
    normalized = [_normalize_symbol(symbol) for symbol in symbols]
    normalized = list(dict.fromkeys(normalized))
    if not normalized:
        return _with_status(pd.DataFrame(), STATUS_EMPTY)
    try:
        if provider is None:
            import akshare as ak

            provider = ak.stock_industry_change_cninfo
        resolved_end = end_date or date.today().strftime("%Y%m%d")
        rows = []
        errors = []
        for idx, symbol in enumerate(normalized):
            try:
                raw = provider(symbol=symbol, start_date=start_date, end_date=resolved_end)
                row = _select_cninfo_industry_row(symbol, raw)
                if row:
                    rows.append(row)
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
                logger.warning(f"Fetch CNINFO industry classification failed for {symbol}: {exc}")
            if sleep_seconds > 0 and idx < len(normalized) - 1:
                time.sleep(sleep_seconds)
        if not rows:
            status = STATUS_SOURCE_ERROR if errors else STATUS_EMPTY
            return _with_status(pd.DataFrame(), status, "; ".join(errors[:5]))
        return _with_status(pd.DataFrame(rows), STATUS_OK, "; ".join(errors[:5]))
    except Exception as exc:
        logger.warning(f"Fetch CNINFO industry map failed: {exc}")
        return _with_status(pd.DataFrame(), STATUS_SOURCE_ERROR, str(exc))


def fetch_cn_industry_members(
    target_symbols: list[str | int] | None = None,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    """Fetch CN symbol-to-industry map from free batch sources."""
    target_set = set(_normalize_symbol(symbol) for symbol in (target_symbols or []))
    sw = fetch_legulegu_sw_industry_members(
        sleep_seconds=sleep_seconds,
        target_symbols=sorted(target_set) if target_set else None,
    )
    frames = [] if sw.empty else [sw]
    found = set(sw["symbol"].astype(str).tolist()) if not sw.empty and "symbol" in sw.columns else set()
    if target_set and target_set.issubset(found):
        return _combine_industry_frames(frames, [source_error(sw)])

    ths = fetch_ths_industry_members(
        sleep_seconds=sleep_seconds,
        target_symbols=sorted(target_set - found) if target_set else None,
    )
    if not ths.empty:
        frames.append(ths)
    if not ths.empty and "symbol" in ths.columns:
        found.update(ths["symbol"].astype(str).tolist())
    if target_set and target_set.issubset(found):
        return _combine_industry_frames(frames, [source_error(sw), source_error(ths)])

    cninfo = fetch_cninfo_industry_members(
        sorted(target_set - found),
        sleep_seconds=sleep_seconds,
    ) if target_set else pd.DataFrame()
    if not cninfo.empty:
        frames.append(cninfo)
    if not cninfo.empty and "symbol" in cninfo.columns:
        found.update(cninfo["symbol"].astype(str).tolist())
    if target_set and target_set.issubset(found):
        return _combine_industry_frames(frames, [source_error(sw), source_error(ths), source_error(cninfo)])

    eastmoney = fetch_eastmoney_industry_members(sleep_seconds=sleep_seconds)
    if not eastmoney.empty:
        frames.append(eastmoney)
    errors = [source_error(sw), source_error(ths), source_error(cninfo), source_error(eastmoney)]
    return _combine_industry_frames(frames, errors)


def _extract_industry_names(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    columns = list(df.columns)
    name_col = _first_existing(columns, ["板块名称", "行业名称", "名称", "name", "industry"])
    code_col = _first_existing(columns, ["板块代码", "行业代码", "代码", "code"])
    selected = name_col or code_col
    if selected is None:
        return []
    names = []
    seen = set()
    for value in df[selected].tolist():
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            names.append(text)
    return names


def _extract_ths_boards(df: pd.DataFrame) -> list[dict[str, str]]:
    if df is None or df.empty:
        return []
    columns = list(df.columns)
    name_col = _first_existing(columns, ["name", "板块", "行业", "名称"])
    code_col = _first_existing(columns, ["code", "代码", "板块代码"])
    if name_col is None or code_col is None:
        return []
    boards = []
    seen = set()
    for _, row in df.iterrows():
        industry = str(row.get(name_col) or "").strip()
        code = str(row.get(code_col) or "").strip()
        if industry and code and code not in seen:
            seen.add(code)
            boards.append({"industry": industry, "code": code})
    return boards


def _extract_sw_industries(df: pd.DataFrame) -> list[dict[str, str]]:
    if df is None or df.empty:
        return []
    columns = list(df.columns)
    code_col = _first_existing(columns, ["行业代码", "code", "代码"])
    industry_col = _first_existing(columns, ["行业名称", "industry", "名称"])
    if code_col is None or industry_col is None:
        return []
    rows = []
    seen = set()
    for _, row in df.iterrows():
        code = str(row.get(code_col) or "").strip()
        industry = str(row.get(industry_col) or "").strip()
        if code and industry and code not in seen:
            seen.add(code)
            rows.append({"code": code, "industry": industry})
    return rows


def _select_cninfo_industry_row(symbol: str, df: pd.DataFrame) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    work = df.copy()
    symbol_col = _first_existing(list(work.columns), ["证券代码", "symbol", "代码"])
    name_col = _first_existing(list(work.columns), ["新证券简称", "name", "证券简称", "名称"])
    standard_col = _first_existing(list(work.columns), ["分类标准", "standard"])
    date_col = _first_existing(list(work.columns), ["变更日期", "date"])
    industry_cols = ["行业次类", "行业大类", "行业门类", "行业中类", "industry"]
    existing_industry_cols = [col for col in industry_cols if col in work.columns]
    if not existing_industry_cols:
        return None
    if symbol_col is not None:
        work = work[work[symbol_col].astype(str).map(_normalize_cn_symbol).eq(symbol)]
    if work.empty:
        return None
    if date_col is not None:
        work["_sort_date"] = pd.to_datetime(work[date_col], errors="coerce")
    else:
        work["_sort_date"] = pd.NaT
    if standard_col is not None:
        standard = work[standard_col].astype(str)
        work["_standard_priority"] = 9
        work.loc[standard.str.contains("申银万国|申万", regex=True, na=False), "_standard_priority"] = 0
        work.loc[standard.str.contains("中证", regex=True, na=False), "_standard_priority"] = 1
        work.loc[standard.str.contains("巨潮", regex=True, na=False), "_standard_priority"] = 2
    else:
        work["_standard_priority"] = 9
    work = work.sort_values(["_standard_priority", "_sort_date"], ascending=[True, False])
    for _, row in work.iterrows():
        industry = _first_non_empty_scalar(row, existing_industry_cols)
        if industry:
            name = str(row.get(name_col) or "").strip() if name_col else ""
            return {
                "symbol": symbol,
                "name": name or None,
                "industry": industry,
                "country": "CN",
                "source": "cninfo_industry_change",
            }
    return None


def _first_non_empty_scalar(row: pd.Series, columns: list[str]) -> str | None:
    for column in columns:
        value = row.get(column)
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _fetch_ths_industry_boards(requester: Callable[..., Any]) -> list[dict[str, str]]:
    html = _request_text(
        requester,
        "http://q.10jqka.com.cn/thshy/",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://q.10jqka.com.cn/",
        },
        timeout=10,
    )
    boards = []
    seen = set()
    for match in re.finditer(r"thshy/detail/code/(\d+)/?[^>]*>([^<]+)</a>", html or ""):
        code = match.group(1).strip()
        industry = re.sub(r"\s+", "", match.group(2)).strip()
        if code and industry and code not in seen:
            seen.add(code)
            boards.append({"industry": industry, "code": code})
    return boards


def _ths_industry_detail_url(code: str, page: int) -> str:
    if int(page) <= 1:
        return f"http://q.10jqka.com.cn/thshy/detail/code/{code}/"
    return f"http://q.10jqka.com.cn/thshy/detail/code/{code}/page/{int(page)}/ajax/1/"


def _request_text(requester: Callable[..., Any], url: str, **kwargs: Any) -> str:
    response = requester(url, **kwargs)
    return response.text if hasattr(response, "text") else str(response)


def _first_symbol_table(html: str) -> pd.DataFrame:
    if not html:
        return pd.DataFrame()
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return pd.DataFrame()
    for table in tables:
        if _first_existing(list(table.columns), ["代码", "股票代码", "symbol"]):
            return table
    return pd.DataFrame()


def _combine_industry_frames(frames: list[pd.DataFrame], errors: list[str] | None = None) -> pd.DataFrame:
    errors = [error for error in (errors or []) if error]
    if not frames:
        status = STATUS_SOURCE_ERROR if errors else STATUS_EMPTY
        return _with_status(pd.DataFrame(), status, "; ".join(errors[:5]))
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return _with_status(out, STATUS_OK if not out.empty else STATUS_EMPTY, "; ".join(errors[:5]))


def _first_non_missing_values(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    existing = [column for column in candidates if column in df.columns]
    if not existing:
        return None
    values = df[existing[0]].copy()
    for column in existing[1:]:
        values = values.where(values.notna() & (values.astype(str).str.strip() != ""), df[column])
    return values


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
