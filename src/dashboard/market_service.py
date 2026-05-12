"""Market overview query and calculation service.

The Dashboard imports this module for read-only market analytics.  It does not
depend on Streamlit, so calculations are testable with an in-memory DuckDB.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import pandas as pd

from src.dashboard.db import query_df

MARKET_META = {
    "CN": {"label": "A股", "currency": "CNY"},
    "HK": {"label": "港股", "currency": "HKD"},
}

INDEX_DEFS = [
    {"index_code": "000300", "name": "沪深300", "market": "CN"},
    {"index_code": "000905", "name": "中证500", "market": "CN"},
    {"index_code": "^HSI", "name": "恒生指数", "market": "HK"},
    {"index_code": "3032.HK", "name": "恒生科技", "market": "HK"},
]

DISTRIBUTION_BUCKETS = [
    "下跌 >7%",
    "下跌 3-7%",
    "下跌 0-3%",
    "平盘",
    "上涨 0-3%",
    "上涨 3-7%",
    "上涨 >7%",
]

FIELD_LABELS = {
    "industry": "行业",
    "amount": "成交额",
    "turnover_rate": "换手率",
    "volume_ratio": "量比",
    "pe_ttm": "PE(TTM)",
    "pb": "PB",
    "market_cap": "总市值",
}


def _execute_df(conn, sql: str, params: Sequence | None = None) -> pd.DataFrame:
    if conn is not None:
        return conn.execute(sql, params or []).fetchdf()
    return query_df(sql, params)


def _table_exists(conn, table_name: str) -> bool:
    sql = """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
    """
    if conn is not None:
        return bool(conn.execute(sql, [table_name]).fetchone()[0])
    df = query_df(sql, [table_name])
    return bool(df.iloc[0, 0]) if not df.empty else False


def _market_case(alias: str = "dp") -> str:
    return f"""
        COALESCE(
            si.country,
            CASE
                WHEN regexp_matches({alias}.symbol, '^[0-9]{{6}}$') THEN 'CN'
                WHEN regexp_matches({alias}.symbol, '^[0-9]{{1,5}}$') THEN 'HK'
                ELSE 'OTHER'
            END
        )
    """


def _load_latest_from_snapshot(conn=None) -> pd.DataFrame:
    if not _table_exists(conn, "market_snapshot"):
        return pd.DataFrame()
    return _execute_df(conn, """
        SELECT
            ms.symbol,
            ms.market,
            ms.trade_date,
            ms.update_time,
            ms.last_price,
            ms.open,
            ms.high,
            ms.low,
            ms.prev_close,
            ms.pct_chg,
            ms.volume,
            ms.amount,
            ms.turnover_rate,
            ms.volume_ratio,
            ms.amplitude,
            ms.pe_ttm,
            ms.pb,
            COALESCE(ms.market_cap, si.market_cap) AS market_cap,
            ms.float_market_cap,
            ms.is_suspended,
            ms.source,
            si.name,
            si.industry,
            si.sector,
            COALESCE(si.currency, CASE WHEN ms.market = 'HK' THEN 'HKD' ELSE 'CNY' END) AS currency
        FROM market_snapshot ms
        LEFT JOIN stock_info si ON ms.symbol = si.symbol
        WHERE ms.market IN ('CN', 'HK')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ms.symbol ORDER BY ms.trade_date DESC) = 1
    """)


def _load_latest_from_daily(conn=None) -> pd.DataFrame:
    market_expr = _market_case("dp")
    return _execute_df(conn, f"""
        WITH priced AS (
            SELECT
                dp.symbol,
                {market_expr} AS market,
                dp.trade_date,
                CAST(dp.trade_date AS TIMESTAMP) + INTERVAL '15 hours' AS update_time,
                dp.close AS last_price,
                dp.open,
                dp.high,
                dp.low,
                COALESCE(
                    dp.pre_close,
                    LAG(dp.close) OVER (PARTITION BY dp.symbol ORDER BY dp.trade_date)
                ) AS prev_close,
                dp.volume,
                dp.amount,
                dp.turnover_rate,
                AVG(dp.volume) OVER (
                    PARTITION BY dp.symbol
                    ORDER BY dp.trade_date
                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                ) AS avg_volume_20,
                dp.pe_ttm,
                dp.pb,
                si.market_cap,
                NULL::DOUBLE AS float_market_cap,
                COALESCE(dp.is_suspended, FALSE) AS is_suspended,
                si.name,
                si.industry,
                si.sector,
                COALESCE(si.currency, CASE WHEN {market_expr} = 'HK' THEN 'HKD' ELSE 'CNY' END) AS currency
            FROM daily_price dp
            LEFT JOIN stock_info si ON dp.symbol = si.symbol
        ),
        latest AS (
            SELECT *
            FROM priced
            WHERE market IN ('CN', 'HK')
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        )
        SELECT
            symbol,
            market,
            trade_date,
            update_time,
            last_price,
            open,
            high,
            low,
            prev_close,
            CASE
                WHEN prev_close IS NOT NULL AND prev_close != 0
                THEN (last_price - prev_close) / prev_close * 100
            END AS pct_chg,
            volume,
            amount,
            turnover_rate,
            CASE
                WHEN avg_volume_20 IS NOT NULL AND avg_volume_20 != 0
                THEN volume / avg_volume_20
            END AS volume_ratio,
            CASE
                WHEN prev_close IS NOT NULL AND prev_close != 0
                THEN (high - low) / prev_close * 100
            END AS amplitude,
            pe_ttm,
            pb,
            market_cap,
            float_market_cap,
            is_suspended,
            'daily_price' AS source,
            name,
            industry,
            sector,
            currency
        FROM latest
    """)


def load_latest_quotes(conn=None) -> pd.DataFrame:
    """Load latest stock quote rows, preferring market_snapshot and falling back to daily_price."""
    snapshot = _load_latest_from_snapshot(conn)
    if not snapshot.empty:
        snapshot = _normalize_quotes(snapshot)
        daily_dates = _execute_df(conn, f"""
            SELECT { _market_case("dp") } AS market, MAX(dp.trade_date) AS latest_date
            FROM daily_price dp
            LEFT JOIN stock_info si ON dp.symbol = si.symbol
            WHERE { _market_case("dp") } IN ('CN', 'HK')
            GROUP BY market
        """)
        stale_markets: set[str] = set()
        if not daily_dates.empty:
            daily_dates["latest_date"] = pd.to_datetime(daily_dates["latest_date"]).dt.date
            snapshot_dates = snapshot.groupby("market")["trade_date"].max().to_dict()
            for _, row in daily_dates.iterrows():
                market = row["market"]
                if market not in snapshot_dates or snapshot_dates[market] < row["latest_date"]:
                    stale_markets.add(market)
        if not stale_markets:
            return snapshot
        daily = _normalize_quotes(_load_latest_from_daily(conn))
        fresh_snapshot = snapshot[~snapshot["market"].isin(stale_markets)]
        fresh_daily = daily[daily["market"].isin(stale_markets)]
        return pd.concat([fresh_snapshot, fresh_daily], ignore_index=True)
    return _normalize_quotes(_load_latest_from_daily(conn))


def _normalize_quotes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in [
        "last_price", "open", "high", "low", "prev_close", "pct_chg", "volume", "amount",
        "turnover_rate", "volume_ratio", "amplitude", "pe_ttm", "pb", "market_cap", "float_market_cap",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["market_label"] = df["market"].map(lambda m: MARKET_META.get(m, {}).get("label", m))
    df["currency"] = df["currency"].fillna(df["market"].map(lambda m: MARKET_META.get(m, {}).get("currency", "")))
    df["industry"] = df["industry"].where(df["industry"].astype(str).str.strip().ne(""), pd.NA)
    df["name"] = df["name"].fillna(df["symbol"])
    return df


def _bucket_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "平盘"
    value = float(value)
    if value < -7:
        return "下跌 >7%"
    if value < -3:
        return "下跌 3-7%"
    if value < 0:
        return "下跌 0-3%"
    if value == 0:
        return "平盘"
    if value <= 3:
        return "上涨 0-3%"
    if value <= 7:
        return "上涨 3-7%"
    return "上涨 >7%"


def _market_temperature(up_ratio: float, median_pct: float | None) -> str:
    if median_pct is None or pd.isna(median_pct):
        return "数据不足"
    if up_ratio >= 0.65 and median_pct > 1:
        return "偏热"
    if up_ratio <= 0.35 and median_pct < -1:
        return "偏冷"
    return "平衡"


def load_market_overview(conn=None) -> dict[str, Any]:
    quotes = load_latest_quotes(conn)
    if quotes.empty:
        return {
            "markets": {},
            "distribution": pd.DataFrame(columns=["market", "market_label", "bucket", "count"]),
            "latest_quotes": quotes,
        }

    market_rows = {}
    distribution_rows = []
    for market, group in quotes.groupby("market", dropna=False):
        valid_ret = group["pct_chg"].dropna()
        total = int(len(group))
        advancers = int((valid_ret > 0).sum())
        decliners = int((valid_ret < 0).sum())
        flats = int((valid_ret == 0).sum())
        up_ratio = advancers / total if total else 0
        median_pct = float(valid_ret.median()) if not valid_ret.empty else None
        currency = group["currency"].dropna().iloc[0] if group["currency"].notna().any() else ""
        market_rows[market] = {
            "market": market,
            "label": MARKET_META.get(market, {}).get("label", market),
            "currency": currency,
            "latest_date": max(group["trade_date"]),
            "total": total,
            "advancers": advancers,
            "decliners": decliners,
            "flats": flats,
            "up_ratio": up_ratio,
            "median_pct_chg": median_pct,
            "avg_pct_chg": float(valid_ret.mean()) if not valid_ret.empty else None,
            "total_amount": float(group["amount"].dropna().sum()) if "amount" in group else None,
            "temperature": _market_temperature(up_ratio, median_pct),
            "source": group["source"].dropna().mode().iloc[0] if group["source"].notna().any() else "daily_price",
        }
        bucket_counts = group["pct_chg"].map(_bucket_pct).value_counts().reindex(DISTRIBUTION_BUCKETS, fill_value=0)
        for bucket, count in bucket_counts.items():
            distribution_rows.append({
                "market": market,
                "market_label": MARKET_META.get(market, {}).get("label", market),
                "bucket": bucket,
                "count": int(count),
            })

    return {
        "markets": market_rows,
        "distribution": pd.DataFrame(distribution_rows),
        "latest_quotes": quotes,
    }


def load_field_coverage(conn=None) -> pd.DataFrame:
    quotes = load_latest_quotes(conn)
    rows = []
    if quotes.empty:
        return pd.DataFrame(columns=["market", "market_label", "field", "label", "available", "total", "coverage_pct", "status"])
    for market, group in quotes.groupby("market", dropna=False):
        total = len(group)
        for field, label in FIELD_LABELS.items():
            if field not in group.columns:
                available = 0
            elif field == "industry":
                available = int(group[field].notna().sum())
            else:
                available = int(pd.to_numeric(group[field], errors="coerce").notna().sum())
            pct = available / total if total else 0
            rows.append({
                "market": market,
                "market_label": MARKET_META.get(market, {}).get("label", market),
                "field": field,
                "label": label,
                "available": available,
                "total": total,
                "coverage_pct": pct,
                "status": "完整" if pct >= 0.8 else ("部分" if pct > 0 else "缺失"),
            })
    return pd.DataFrame(rows)


def load_market_breadth(conn=None) -> pd.DataFrame:
    market_expr = _market_case("dp")
    df = _execute_df(conn, f"""
        SELECT
            dp.symbol,
            {market_expr} AS market,
            dp.trade_date,
            dp.high,
            dp.low,
            dp.close,
            COALESCE(dp.pre_close, LAG(dp.close) OVER (PARTITION BY dp.symbol ORDER BY dp.trade_date)) AS prev_close,
            dp.volume
        FROM daily_price dp
        LEFT JOIN stock_info si ON dp.symbol = si.symbol
        WHERE {market_expr} IN ('CN', 'HK')
        ORDER BY dp.symbol, dp.trade_date
    """)
    if df.empty:
        return pd.DataFrame(columns=[
            "market", "market_label", "latest_date", "total", "above_ma20_pct", "above_ma60_pct",
            "above_ma120_pct", "new_high_20", "new_low_20", "new_high_60", "new_low_60",
            "volume_expand", "volume_contract", "median_pct_chg",
        ])
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["high", "low", "close", "prev_close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    grouped = df.groupby("symbol", group_keys=False)
    for window in [20, 60, 120]:
        df[f"ma{window}"] = grouped["close"].transform(lambda s: s.rolling(window, min_periods=window).mean())
        df[f"prior_high_{window}"] = grouped["high"].transform(
            lambda s: s.shift(1).rolling(window, min_periods=window).max()
        )
        df[f"prior_low_{window}"] = grouped["low"].transform(
            lambda s: s.shift(1).rolling(window, min_periods=window).min()
        )
    df["avg_volume_20"] = grouped["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=1).mean())
    df["volume_ratio"] = df["volume"] / df["avg_volume_20"].replace(0, pd.NA)
    df["pct_chg"] = (df["close"] - df["prev_close"]) / df["prev_close"].replace(0, pd.NA) * 100

    latest = df.sort_values(["symbol", "trade_date"]).groupby("symbol", as_index=False).tail(1)
    rows = []
    for market, group in latest.groupby("market", dropna=False):
        total = len(group)
        rows.append({
            "market": market,
            "market_label": MARKET_META.get(market, {}).get("label", market),
            "latest_date": max(group["trade_date"]),
            "total": int(total),
            "above_ma20_pct": float((group["close"] > group["ma20"]).mean()) if total else None,
            "above_ma60_pct": float((group["close"] > group["ma60"]).mean()) if total else None,
            "above_ma120_pct": float((group["close"] > group["ma120"]).mean()) if total else None,
            "new_high_20": int((group["high"] >= group["prior_high_20"]).sum()),
            "new_low_20": int((group["low"] <= group["prior_low_20"]).sum()),
            "new_high_60": int((group["high"] >= group["prior_high_60"]).sum()),
            "new_low_60": int((group["low"] <= group["prior_low_60"]).sum()),
            "volume_expand": int((group["volume_ratio"] >= 1.5).sum()),
            "volume_contract": int((group["volume_ratio"] <= 0.7).sum()),
            "median_pct_chg": float(group["pct_chg"].median()) if group["pct_chg"].notna().any() else None,
        })
    return pd.DataFrame(rows)


def load_index_benchmarks(conn=None, days: int = 132, start_date: date | None = None) -> dict[str, pd.DataFrame]:
    codes = [item["index_code"] for item in INDEX_DEFS]
    code_placeholders = ", ".join(["?"] * len(codes))
    params: list[Any] = list(codes)
    where = f"index_code IN ({code_placeholders})"
    if start_date is not None:
        where += " AND trade_date >= ?"
        params.append(start_date)
    df = _execute_df(conn, f"""
        SELECT index_code, trade_date, open, high, low, close, volume, amount
        FROM index_daily
        WHERE {where}
        ORDER BY index_code, trade_date
    """, params)
    if df.empty:
        return {
            "series": pd.DataFrame(columns=["index_code", "name", "trade_date", "close", "normalized", "drawdown"]),
            "summary": pd.DataFrame(columns=["index_code", "name", "latest_date", "latest_close", "period_return", "max_drawdown"]),
        }
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if days < 9999 and start_date is None:
        df = df.groupby("index_code", group_keys=False).tail(days)
    meta = {item["index_code"]: item for item in INDEX_DEFS}
    rows = []
    summary = []
    for code, group in df.groupby("index_code", sort=False):
        group = group.sort_values("trade_date").copy()
        if group.empty or group["close"].dropna().empty:
            continue
        for window in [5, 20, 60]:
            group[f"ma{window}"] = group["close"].rolling(window).mean()
        first = group["close"].dropna().iloc[0]
        group["normalized"] = group["close"] / first
        group["daily_return"] = group["close"].pct_change(fill_method=None)
        group["drawdown"] = group["normalized"] / group["normalized"].cummax() - 1
        name = meta.get(code, {}).get("name", code)
        group["name"] = name
        rows.append(group)
        summary.append({
            "index_code": code,
            "name": name,
            "market": meta.get(code, {}).get("market"),
            "latest_date": group["trade_date"].iloc[-1],
            "latest_close": float(group["close"].iloc[-1]),
            "period_return": float(group["close"].iloc[-1] / first - 1),
            "one_day_return": float(group["daily_return"].iloc[-1]) if pd.notna(group["daily_return"].iloc[-1]) else None,
            "max_drawdown": float(group["drawdown"].min()),
            "volume": float(group["volume"].iloc[-1]) if pd.notna(group["volume"].iloc[-1]) else None,
        })
    series = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return {"series": series, "summary": pd.DataFrame(summary)}


def _stock_external_links(symbol: str, market: str | None = None) -> str:
    pure = str(symbol).lstrip("0") or "0"
    if market == "CN" or len(str(symbol)) == 6:
        exchange = "sh" if str(symbol).startswith(("6", "5")) else "sz"
        xq = f"SH{symbol}" if exchange == "sh" else f"SZ{symbol}"
        return f"[东方财富](https://quote.eastmoney.com/{exchange}{symbol}.html) [雪球](https://xueqiu.com/S/{xq})"
    return f"[东方财富](https://quote.eastmoney.com/hk/{pure}.html) [雪球](https://xueqiu.com/S/HK{pure})"


def _format_mover_table(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    cols = [
        "symbol", "name", "market_label", "industry", "last_price", "pct_chg",
        "amount", "turnover_rate", "volume_ratio", "links",
    ]
    if df.empty:
        return pd.DataFrame(columns=cols)
    result = df.head(limit).copy()
    result["industry"] = result["industry"].fillna(result["market_label"])
    result["links"] = result.apply(lambda row: _stock_external_links(row["symbol"], row["market"]), axis=1)
    return result[cols]


def load_market_movers(conn=None, limit: int = 10) -> dict[str, pd.DataFrame]:
    quotes = load_latest_quotes(conn)
    if quotes.empty:
        empty = _format_mover_table(quotes, limit)
        return {"gainers": empty, "losers": empty, "turnover": empty, "volume_ratio": empty}
    tradable = quotes[quotes["pct_chg"].notna()].copy()
    return {
        "gainers": _format_mover_table(tradable.sort_values("pct_chg", ascending=False), limit),
        "losers": _format_mover_table(tradable.sort_values("pct_chg", ascending=True), limit),
        "turnover": _format_mover_table(quotes.sort_values("amount", ascending=False, na_position="last"), limit),
        "volume_ratio": _format_mover_table(quotes.sort_values("volume_ratio", ascending=False, na_position="last"), limit),
    }


def load_sector_style(conn=None) -> pd.DataFrame:
    quotes = load_latest_quotes(conn)
    if quotes.empty:
        return pd.DataFrame(columns=[
            "market", "market_label", "group", "count", "avg_pct_chg", "median_pct_chg", "advancers", "amount",
        ])
    df = quotes.copy()
    df["group"] = df["industry"].fillna(df["sector"]).fillna(df["market_label"])
    rows = []
    for (market, group_name), group in df.groupby(["market", "group"], dropna=False):
        valid_ret = group["pct_chg"].dropna()
        rows.append({
            "market": market,
            "market_label": MARKET_META.get(market, {}).get("label", market),
            "group": group_name,
            "count": int(len(group)),
            "avg_pct_chg": float(valid_ret.mean()) if not valid_ret.empty else None,
            "median_pct_chg": float(valid_ret.median()) if not valid_ret.empty else None,
            "advancers": int((valid_ret > 0).sum()),
            "amount": float(group["amount"].dropna().sum()) if group["amount"].notna().any() else None,
        })
    return pd.DataFrame(rows).sort_values(["market", "avg_pct_chg"], ascending=[True, False])


def load_data_quality_status(conn=None) -> pd.DataFrame:
    if not _table_exists(conn, "data_quality_status"):
        return pd.DataFrame(columns=["check_ts", "status", "metric", "value", "threshold", "detail"])
    return _execute_df(conn, """
        SELECT check_ts, status, metric, value, threshold, detail
        FROM data_quality_status
        ORDER BY check_ts DESC, metric
    """)
