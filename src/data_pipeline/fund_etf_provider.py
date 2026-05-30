"""F1: 头部 ETF 候选池数据拉取。

akshare `fund_etf_spot_em` 拉全场内 ETF 列表 + 总市值,按规模阈值过滤
头部品种,自动分类(broad/sector/qdii/commodity/bond/money),排除货币市场。
再 `fund_etf_hist_em` 批量回灌 nav 历史。

CLI:
    python -m src.data_pipeline.fund_etf_provider fetch --min-scale-yi 50
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import click
import pandas as pd
from loguru import logger


def _disable_proxy() -> None:
    """与 scheduler_watchdog 一致 — 拉外部数据时必清 proxy(企业代理对 eastmoney 阻断)。"""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "*"

DEFAULT_MIN_SCALE_YI = 50.0     # 亿;约对应 159 支
DEFAULT_NAV_LOOKBACK_DAYS = 365 * 3  # 3 年
NAV_REQUEST_INTERVAL_SEC = 0.8      # 单只 nav 间隔,降低 rate-limit 概率
NAV_MAX_RETRIES = 3
NAV_RETRY_BACKOFF_SEC = 5.0          # 失败重试基准


@dataclass
class EtfRow:
    fund_code: str
    name: str
    scale_yi: float
    iopv: float | None
    subcategory: str        # broad / sector / qdii / commodity / bond / money / other
    tracking_index: str     # 反推的跟踪指数 code (空时为 "")


# 行业 / 主题关键词 → 子类
SECTOR_KEYWORDS = {
    "broad": ["沪深300", "中证500", "中证1000", "上证50", "上证180", "深证100",
              "创业板", "科创50", "科创100", "中证A50", "中证A500", "国证2000"],
    "qdii": ["纳斯达克", "标普", "纳指", "道琼斯", "恒生", "港股", "海外", "国际",
             "日本", "德国", "法国", "印度", "越南", "QDII"],
    "commodity": ["黄金", "白银", "有色", "贵金属", "原油", "能源化工", "豆粕"],
    "bond": ["国债", "信用债", "可转债", "城投债", "短债"],
    "money": ["货币", "日利", "添益", "添利", "现金", "赤利"],
}

# 行业关键词单独维护(命中即归为 sector)
SECTOR_THEME_KEYWORDS = [
    "医药", "医疗", "生物", "创新药", "中药",
    "科技", "半导体", "芯片", "5G", "通信", "人工智能", "AI", "云计算",
    "消费", "白酒", "食品", "饮料",
    "金融", "银行", "证券", "保险",
    "新能源", "光伏", "锂电", "风电",
    "汽车", "智能驾驶",
    "军工", "国防",
    "地产", "建材",
    "煤炭", "钢铁", "化工", "石油",
    "环保",
    "传媒", "游戏",
    "农业",
]

# 已知关键宽基指数代码反推映射
INDEX_CODE_MAP = {
    "沪深300": "000300",
    "中证500": "000905",
    "中证1000": "000852",
    "上证50": "000016",
    "创业板": "399006",
    "科创50": "000688",
    "恒生科技": "HSTECH",
    "恒生": "HSI",
    "纳斯达克": "NDX",
    "标普500": "SPX",
}


def classify(name: str) -> tuple[str, str]:
    """根据基金名称归类 + 反推 tracking_index 代码。

    返回 (subcategory, tracking_index_code).
    """
    if not name:
        return "other", ""
    for key in SECTOR_KEYWORDS["money"]:
        if key in name:
            return "money", ""
    for key in SECTOR_KEYWORDS["bond"]:
        if key in name:
            return "bond", ""
    for key in SECTOR_KEYWORDS["commodity"]:
        if key in name:
            return "commodity", ""
    for key in SECTOR_KEYWORDS["qdii"]:
        if key in name:
            tracking = next((v for k, v in INDEX_CODE_MAP.items() if k in name), "")
            return "qdii", tracking
    for key in SECTOR_KEYWORDS["broad"]:
        if key in name:
            tracking = next((v for k, v in INDEX_CODE_MAP.items() if k in name), "")
            return "broad", tracking
    for theme in SECTOR_THEME_KEYWORDS:
        if theme in name:
            return "sector", ""
    return "other", ""


def fetch_etf_universe(min_scale_yi: float = DEFAULT_MIN_SCALE_YI) -> list[EtfRow]:
    """拉头部 ETF 列表(规模过滤 + 自动分类 + 排除货币)。"""
    _disable_proxy()
    import akshare as ak

    df = ak.fund_etf_spot_em()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce")
    df = df.dropna(subset=["总市值", "代码", "名称"])
    df = df[df["总市值"] > min_scale_yi * 1e8].copy()
    df["scale_yi"] = df["总市值"] / 1e8
    rows: list[EtfRow] = []
    for _, r in df.iterrows():
        name = str(r["名称"])
        sub, tracking = classify(name)
        if sub == "money":
            continue  # 排除货币市场
        iopv = pd.to_numeric(r.get("IOPV实时估值"), errors="coerce")
        rows.append(EtfRow(
            fund_code=str(r["代码"]),
            name=name,
            scale_yi=float(r["scale_yi"]),
            iopv=None if pd.isna(iopv) else float(iopv),
            subcategory=sub,
            tracking_index=tracking,
        ))
    rows.sort(key=lambda x: x.scale_yi, reverse=True)
    return rows


def fetch_etf_history(
    fund_code: str,
    *,
    start_date: date,
    end_date: date,
    max_retries: int = NAV_MAX_RETRIES,
) -> pd.DataFrame:
    """单只 ETF 历史日线 - 带重试 + 指数退避(对抗 eastmoney 反爬)。"""
    import time
    _disable_proxy()
    import akshare as ak

    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            df = ak.fund_etf_hist_em(symbol=fund_code, period="daily",
                                     start_date=s, end_date=e)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={"日期": "trade_date", "收盘": "nav"})
            df["fund_code"] = fund_code
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
            return df[["fund_code", "trade_date", "nav"]].dropna()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_retries - 1:
                wait = NAV_RETRY_BACKOFF_SEC * (2 ** attempt)
                logger.debug(f"{fund_code} hist attempt {attempt+1}/{max_retries} failed, retry in {wait}s")
                time.sleep(wait)
    logger.warning(f"{fund_code} hist failed after {max_retries} retries: {last_exc}")
    return pd.DataFrame()


def persist_universe(conn, etfs: list[EtfRow]) -> int:
    """把头部 ETF 落到 fund_info(只更新数据源为 etf_scan 的行,保留 manual 的 3 支配置)。"""
    if not etfs:
        return 0
    now = datetime.now()
    rows = [{
        "fund_code": e.fund_code,
        "name": e.name,
        "fund_type": "ETF",
        "tracking_index": e.tracking_index,
        "market": "CN",
        "currency": "CNY",
        "enabled": True,
        "scale_yi": e.scale_yi,
        "etf_subcategory": e.subcategory,
        "data_source": "etf_scan",
        "last_scanned_at": now,
    } for e in etfs]
    df = pd.DataFrame(rows)  # noqa: F841 - DuckDB 通过 SELECT * FROM df 引用本地变量
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_etf_universe AS SELECT * FROM df")
    # 不覆盖用户手填的 3 支(data_source='manual' / NULL),只 upsert etf_scan 来源
    existing_manual = set(r[0] for r in conn.execute(
        "SELECT fund_code FROM fund_info WHERE COALESCE(data_source, 'manual') = 'manual'"
    ).fetchall())
    conn.execute(
        """
        INSERT OR REPLACE INTO fund_info (
            fund_code, name, fund_type, tracking_index, market, currency, enabled,
            scale_yi, etf_subcategory, data_source, last_scanned_at
        )
        SELECT fund_code, name, fund_type, tracking_index, market, currency, enabled,
               scale_yi, etf_subcategory, data_source, last_scanned_at
        FROM _tmp_etf_universe
        WHERE fund_code NOT IN (SELECT UNNEST(?))
        """,
        [list(existing_manual)] if existing_manual else [[""]],
    )
    return len(rows)


def persist_nav_batch(conn, nav_df: pd.DataFrame) -> int:
    if nav_df.empty:
        return 0
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fund_nav AS SELECT * FROM nav_df")
    conn.execute(
        """
        INSERT OR REPLACE INTO fund_nav (fund_code, trade_date, nav)
        SELECT fund_code, trade_date, nav FROM _tmp_fund_nav
        """
    )
    return len(nav_df)


@click.group()
def cli():
    pass


@cli.command()
@click.option("--min-scale-yi", default=DEFAULT_MIN_SCALE_YI, help="规模阈值(亿)")
@click.option("--lookback-days", default=DEFAULT_NAV_LOOKBACK_DAYS, help="nav 回灌天数")
@click.option("--max-funds", default=0, help="限制最多拉多少支(0=不限,按规模降序)")
@click.option("--nav-only", is_flag=True, help="只更新 nav,不重新分类")
def fetch(min_scale_yi: float, lookback_days: int, max_funds: int, nav_only: bool) -> None:
    """F1 主入口:拉头部 ETF + nav。"""
    from src.data_pipeline.loader import get_connection, init_db

    end = date.today()
    start = end - timedelta(days=lookback_days)
    conn = get_connection()
    try:
        init_db(conn)
        if not nav_only:
            etfs = fetch_etf_universe(min_scale_yi=min_scale_yi)
            if max_funds > 0:
                etfs = etfs[:max_funds]
            click.echo(f"扫到 {len(etfs)} 支头部 ETF (规模 > {min_scale_yi} 亿)")
            counts: dict[str, int] = {}
            for e in etfs:
                counts[e.subcategory] = counts.get(e.subcategory, 0) + 1
            click.echo(f"分类分布: {counts}")
            n = persist_universe(conn, etfs)
            click.echo(f"落 fund_info {n} 支(保留 manual 配置)")
            codes = [e.fund_code for e in etfs]
        else:
            codes = [r[0] for r in conn.execute(
                "SELECT fund_code FROM fund_info WHERE data_source = 'etf_scan'"
            ).fetchall()]
        import time
        click.echo(f"开始批量拉取 nav: {len(codes)} 支,回灌 {lookback_days} 天 "
                   f"(单只间隔 {NAV_REQUEST_INTERVAL_SEC}s + 失败重试 {NAV_MAX_RETRIES} 次)")
        total = 0
        failed = []
        for i, code in enumerate(codes, 1):
            df = fetch_etf_history(code, start_date=start, end_date=end)
            if df.empty:
                failed.append(code)
            else:
                total += persist_nav_batch(conn, df)
            if i % 20 == 0:
                click.echo(f"  进度 {i}/{len(codes)}  累计落 nav {total} 行  失败 {len(failed)} 支")
            if i < len(codes):
                time.sleep(NAV_REQUEST_INTERVAL_SEC)
        click.echo(f"完成 nav 落库 {total} 行,失败 {len(failed)} 支")
        if failed:
            click.echo(f"失败示例: {failed[:5]}")
    finally:
        conn.close()


if __name__ == "__main__":
    cli()
