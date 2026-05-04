"""
数据采集主入口 CLI。
用法：
    python -m src.data_pipeline.main init        # 首次全量拉取
    python -m src.data_pipeline.main update      # 每日增量更新
    python -m src.data_pipeline.main check       # 检查数据完整性
"""
import os
import sys
from datetime import date, timedelta

import click
import pandas as pd
from loguru import logger

from src.config import PROJECT_ROOT, load_config

# 绕过代理访问金融数据 API（如果代理拦截了 *.eastmoney.com / 东方财富）
_NO_PROXY_DOMAINS = "eastmoney.com,push2his.eastmoney.com,yahoo.com,github.com,wikipedia.org"


def _setup_environment():
    """初始化环境：绕过系统代理 + 修复 LibreSSL 兼容性"""
    # 1. 清除环境变量代理
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    # 2. 降低 urllib3 版本以兼容 macOS LibreSSL (Python 3.9)
    _fix_urllib3()

    # 3. Patch requests 的代理检测，防止读取 macOS 系统代理 (127.0.0.1:10080)
    import requests.utils
    requests.utils.getproxies = lambda: {}


def _fix_urllib3():
    """Python 3.9 的 LibreSSL 与 urllib3 v2 不兼容，强制降级到 v1。
       Python 3.12+ 使用 OpenSSL，不需要降级。"""
    if sys.version_info >= (3, 10):
        return  # Python 3.10+ 不需要降级
    try:
        from importlib.metadata import version
        v = version("urllib3")
        if v.startswith("2."):
            logger.info("urllib3 v2 与 LibreSSL 不兼容，正在降级到 v1...")
            import subprocess as sp
            sp.check_call(
                [sys.executable, "-m", "pip", "install", "--user", "urllib3<2"],
                stdout=sp.DEVNULL, stderr=sp.DEVNULL,
            )
            logger.info("urllib3 已降级，请重新运行 python3 -m src.data_pipeline.main init")
            sys.exit(0)
    except Exception:
        pass

from src.data_pipeline.fetchers import akshare_fetcher as ak
from src.data_pipeline.fetchers import yfinance_fetcher as yf
from src.data_pipeline.loader import (
    get_all_symbols,
    get_connection,
    get_last_trade_date,
    init_db,
    upsert_daily_price,
    upsert_index_daily,
    upsert_stock_info,
)

# 港股代码后缀映射
HK_INDEX_YFINANCE = {"HSI": "^HSI", "HSTECH": "3032.HK"}
CN_INDEX_AKSHARE = {"000300": "000300.SH", "000905": "000905.SH"}

# 恒生指数主要成分股（硬编码备用，当联网获取失败时使用）
# 恒生指数成分股（约82只，来源：恒生指数公司公开信息，需定期刷新）
_HSI_FALLBACK = [
    "00005", "00011", "00012", "00016", "00017", "00027", "00066", "00083",
    "00101", "00175", "00241", "00267", "00268", "00285", "00288", "00291",
    "00316", "00388", "00669", "00700", "00762", "00823", "00857", "00868",
    "00883", "00939", "00941", "00960", "00968", "00981", "00992", "01038",
    "01044", "01057", "01088", "01093", "01109", "01113", "01177", "01209",
    "01211", "01299", "01378", "01398", "01658", "01810", "01833", "01876",
    "01928", "01929", "01997", "02015", "02018", "02020", "02269", "02313",
    "02318", "02331", "02356", "02382", "02628", "02688", "02899", "03690",
    "03888", "03968", "03988", "06618", "06862", "09618", "09626", "09633",
    "09698", "09888", "09901", "09961", "09988", "09992", "09999",
    # 以下为2026年初新增/替换
    "00322", "00688", "01024", "02007",
]

# 恒生科技指数成分股（约30只）
_HSTECH_FALLBACK = [
    "00700", "09988", "03690", "01810", "09618", "09999", "09888", "09961",
    "02015", "01024", "00981", "02382", "06618", "09626", "00992", "01347",
    "01833", "02018", "03888", "09633", "02007", "00268", "09698",
    "09992", "01876", "09901",
]


def _load_config():
    return load_config()


def _default_dates(config: dict):
    """从配置中获取默认日期范围（AkShare 格式: YYYYMMDD）"""
    history_years = config.get("data", {}).get("history_years", 5)
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=history_years * 365)).strftime("%Y%m%d")
    return start_date, end_date


def _default_dates_yf(config: dict):
    """从配置中获取默认日期范围（yfinance 格式: YYYY-MM-DD）"""
    history_years = config.get("data", {}).get("history_years", 5)
    end_date = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - timedelta(days=history_years * 365)).strftime("%Y-%m-%d")
    return start_date, end_date


def init_all(conn, config: dict):
    """首次全量初始化：基本信息 + 历史日线"""
    logger.info("=== 初始化：拉取股票基本信息 ===")

    # 1. A股基本信息
    cn_info = ak.fetch_cn_stock_info()
    if not cn_info.empty:
        upsert_stock_info(conn, cn_info[["symbol", "country", "name"]])

    # 2. 港股基本信息（非致命，忽略网络错误）
    hk_info = pd.DataFrame()
    try:
        hk_info = ak.fetch_hk_stock_info()
        if not hk_info.empty:
            available_cols = [c for c in ["symbol", "country", "name"] if c in hk_info.columns]
            if available_cols:
                upsert_stock_info(conn, hk_info[available_cols])
    except Exception as e:
        logger.warning(f"HK stock info skipped (network error): {e}")

    # 3. 获取指数成分股
    logger.info("=== 初始化：获取沪深300成分股 ===")
    hs300_symbols = ak.fetch_index_components("000300")

    logger.info("=== 初始化：获取中证500成分股 ===")
    zz500_symbols = ak.fetch_index_components("000905")
    # 合并 A 股股票池（去重）
    cn_symbols = list(dict.fromkeys(hs300_symbols + zz500_symbols))

    # 4. 恒生指数成分股（硬编码备用）
    logger.info("=== 初始化：获取恒生指数成分股 ===")
    hsi_symbols = []
    try:
        hsi_symbols = yf.fetch_hk_components_hsi()
    except Exception as e:
        logger.warning(f"HSI components fetch failed: {e}")
    if not hsi_symbols:
        hsi_symbols = _HSI_FALLBACK
        logger.info(f"Using fallback HSI list: {len(hsi_symbols)} stocks")

    # 恒生科技成分股（硬编码备用）
    hstech_symbols = _HSTECH_FALLBACK

    start_date, end_date = _default_dates(config)
    start_date_yf, end_date_yf = _default_dates_yf(config)

    # 5. 拉取 A股日线（yfinance 主通道，沪深300 + 中证500 合并股票池）
    logger.info(f"=== 初始化：拉取 A股日线 ({len(cn_symbols)} 只: 沪深300+中证500) ===")
    for i, sym in enumerate(cn_symbols):
        try:
            df = yf.fetch_cn_daily(sym, start_date=start_date_yf, end_date=end_date_yf)
            if df.empty:
                df = ak.fetch_cn_stock_daily(sym, start_date=start_date, end_date=end_date)
            if not df.empty:
                upsert_daily_price(conn, df)
        except Exception as e:
            logger.error(f"CN daily failed {sym}: {e}")
        if (i + 1) % 100 == 0:
            logger.info(f"  CN daily: {i+1}/{len(cn_symbols)}")

    # 6. 拉取 港股日线（HSI + HSTECH 合并）
    hk_symbols = list(dict.fromkeys(hsi_symbols + hstech_symbols))
    logger.info(f"=== 初始化：拉取 港股日线 ({len(hk_symbols)} 只: 恒指+恒生科技) ===")
    for i, sym in enumerate(hk_symbols):
        try:
            df = yf.fetch_hk_daily(sym, start_date=start_date_yf, end_date=end_date_yf)
            if df.empty:
                df = ak.fetch_hk_stock_daily(sym, start_date=start_date, end_date=end_date)
            if not df.empty:
                upsert_daily_price(conn, df)
        except Exception as e:
            logger.error(f"HK daily failed {sym}: {e}")
        if (i + 1) % 10 == 0:
            logger.info(f"  HK daily: {i+1}/{len(hk_symbols)}")

    # 7. 拉取指数日线
    logger.info("=== 初始化：拉取指数日线 ===")
    for code in CN_INDEX_AKSHARE:
        try:
            df = ak.fetch_cn_index_daily(code, start_date=start_date, end_date=end_date)
            if not df.empty:
                upsert_index_daily(conn, df)
        except Exception as e:
            logger.error(f"CN index daily failed {code}: {e}")

    for code, ycode in HK_INDEX_YFINANCE.items():
        try:
            df = yf.fetch_hk_index_daily(ycode, start_date=start_date_yf, end_date=end_date_yf)
            if not df.empty:
                upsert_index_daily(conn, df)
        except Exception as e:
            logger.error(f"HK index daily failed {ycode}: {e}")

    logger.info("=== 初始化完成 ===")


def update_all(conn, config: dict):
    """每日增量更新：只更新已有日线数据的股票"""
    logger.info("=== 增量更新 ===")
    start_date, end_date = _default_dates(config)
    start_date_yf, end_date_yf = _default_dates_yf(config)

    # 只更新 daily_price 表中已有的股票
    cn_with_data = conn.execute(
        "SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')"
    ).fetchall()
    hk_with_data = conn.execute(
        "SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')"
    ).fetchall()
    cn_symbols = [r[0] for r in cn_with_data]
    hk_symbols = [r[0] for r in hk_with_data]

    # A股 增量（yfinance 主通道，和 init_all 保持一致）
    logger.info(f"Updating CN: {len(cn_symbols)} symbols with data")
    for sym in cn_symbols:
        last = get_last_trade_date(conn, sym)
        if last and (date.today() - last).days == 0:
            continue  # 今天已有数据，跳过
        fetch_start_yf = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else start_date_yf
        try:
            df = yf.fetch_cn_daily(sym, fetch_start_yf, end_date_yf)
            if not df.empty:
                upsert_daily_price(conn, df)
        except Exception:
            pass

    # 港股 增量（yfinance 主通道）
    logger.info(f"Updating HK: {len(hk_symbols)} symbols with data")
    for sym in hk_symbols:
        last = get_last_trade_date(conn, sym)
        if last and (date.today() - last).days == 0:
            continue
        fetch_start_yf = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else start_date_yf
        try:
            df = yf.fetch_hk_daily(sym, fetch_start_yf, end_date_yf)
            if not df.empty:
                upsert_daily_price(conn, df)
        except Exception:
            pass

    # 指数增量
    for code in CN_INDEX_AKSHARE:
        try:
            df = ak.fetch_cn_index_daily(code, start_date=start_date, end_date=end_date)
            if not df.empty:
                upsert_index_daily(conn, df)
        except Exception:
            pass
    for code, ycode in HK_INDEX_YFINANCE.items():
        try:
            df = yf.fetch_hk_index_daily(ycode, start_date=start_date_yf, end_date=end_date_yf)
            if not df.empty:
                upsert_index_daily(conn, df)
        except Exception:
            pass

    logger.info("=== 增量更新完成 ===")


def check_data(conn, _config: dict, full: bool = False):
    """数据完整性检查。full=True 时进行成分股覆盖对比。"""
    from datetime import date as dt_date
    logger.info("=== 数据完整性检查 ===")

    # 基础统计
    cn_all = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchone()[0]
    hk_all = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')").fetchone()[0]
    latest = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    oldest = conn.execute("SELECT MIN(trade_date) FROM daily_price").fetchone()[0]

    logger.info(f"  A股日线: {cn_all} 只 | 港股日线: {hk_all} 只 | 合计: {cn_all+hk_all}")
    logger.info(f"  时间范围: {oldest} ~ {latest}")

    # 近10天覆盖
    recent = conn.execute("""
        SELECT trade_date, COUNT(DISTINCT symbol)
        FROM daily_price
        WHERE trade_date >= CURRENT_DATE - INTERVAL '10 days'
        GROUP BY trade_date ORDER BY trade_date DESC
    """).fetchall()
    for d, c in recent:
        logger.info(f"    {d}: {c} 只")

    # 指数
    idx = conn.execute("""
        SELECT index_code, COUNT(*) as days, MAX(trade_date) as latest
        FROM index_daily GROUP BY index_code ORDER BY index_code
    """).fetchall()
    logger.info(f"  指数: {len(idx)} 个")
    for code, days, lat in idx:
        behind = (dt_date.today() - lat).days if lat else 999
        flag = "✅" if behind <= 1 else ("🟡" if behind <= 3 else "🔴")
        logger.info(f"    {flag} {code}: {days}天, 最新 {lat}")

    # 全面检查：对比指数成分股
    if full:
        logger.info("=== 成分股覆盖对比 ===")
        try:
            import akshare as ak
            # A股
            hs300 = set(ak.index_stock_cons("000300")["品种代码"].astype(str))
            zz500 = set(ak.index_stock_cons("000905")["品种代码"].astype(str))
            target_cn = hs300 | zz500
            db_cn = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchall()}
            missing_cn = target_cn - db_cn
            extra_cn = db_cn - target_cn
            logger.info(f"  A股目标: {len(target_cn)} (HS300={len(hs300)} + ZZ500={len(zz500)})")
            logger.info(f"  A股实际: {len(db_cn)} | 覆盖: {len(db_cn & target_cn)} ({len(db_cn & target_cn)*100//len(target_cn)}%)")
            if missing_cn:
                logger.warning(f"  ❌ A股缺失 {len(missing_cn)} 只: {sorted(missing_cn)[:20]}...")
            if extra_cn:
                logger.info(f"  ℹ️  DB多余 {len(extra_cn)} 只(非当前成分股)")
            if not missing_cn:
                logger.info(f"  ✅ A股成分股全覆盖")

            # 港股 - 用硬编码备选列表对比
            hsi = set(_HSI_FALLBACK)
            hstech = set(_HSTECH_FALLBACK)
            target_hk = hsi | hstech
            db_hk = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')").fetchall()}
            missing_hk = target_hk - db_hk
            logger.info(f"  港股目标: {len(target_hk)} (HSI={len(hsi)} + HSTECH={len(hstech)})")
            logger.info(f"  港股实际: {len(db_hk)} | 覆盖: {len(db_hk & target_hk)} ({len(db_hk & target_hk)*100//len(target_hk) if target_hk else 0}%)")
            if missing_hk:
                logger.warning(f"  ❌ 港股缺失 {len(missing_hk)} 只: {sorted(missing_hk)}")
            else:
                logger.info(f"  ✅ 港股成分股全覆盖")
        except Exception as e:
            logger.warning(f"  成分股对比不可用 (AkShare 网络问题): {e}")

    # 数据新鲜度
    logger.info("=== 数据新鲜度 ===")
    today = dt_date.today()
    cn_latest = conn.execute("SELECT MAX(trade_date) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchone()[0]
    hk_latest = conn.execute("SELECT MAX(trade_date) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='HK')").fetchone()[0]
    for label, lat in [("A股", cn_latest), ("港股", hk_latest)]:
        if lat:
            behind = (today - lat).days
            flag = "✅ 正常" if behind <= 1 else ("🟡 落后" if behind <= 3 else "🔴 严重落后")
            logger.info(f"  {label}: {lat} ({flag}, {behind}天)")


@click.group()
def cli():
    pass


@cli.command()
def init():
    """首次全量数据下载"""
    _setup_environment()
    conn = get_connection()
    init_db(conn)
    config = _load_config()
    init_all(conn, config)
    # init 完成后自动做全面检查
    check_data(conn, config, full=True)
    conn.close()


@cli.command()
def update():
    """每日增量更新"""
    _setup_environment()
    conn = get_connection()
    config = _load_config()
    update_all(conn, config)
    conn.close()


@cli.command()
@click.option("--full", is_flag=True, help="全面检查：对比指数成分股覆盖")
def check(full):
    """数据完整性检查（--full 进行成分股覆盖对比）"""
    conn = get_connection(read_only=True)
    config = _load_config()
    check_data(conn, config, full=full)
    conn.close()


if __name__ == "__main__":
    cli()
