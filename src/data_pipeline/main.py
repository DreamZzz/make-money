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

from src.config import DATA_QUALITY_RULES, PROJECT_ROOT, load_config

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
    build_market_snapshot_from_daily,
    get_all_symbols,
    get_connection,
    get_last_trade_date,
    init_db,
    record_data_source_health,
    repair_market_metadata,
    upsert_daily_price,
    upsert_index_daily,
    upsert_stock_info,
)

# 港股指数映射：优先使用 AkShare/Sina 的真实指数代码，yfinance 仅作兜底。
HK_INDEX_AKSHARE_SINA = {"HSI": "HSI", "HSTECH": "HSTECH"}
HK_INDEX_YFINANCE_FALLBACK = {"HSI": "^HSI", "HSTECH": "HSTECH.HK"}
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


def _chunks(items: list, size: int):
    size = max(int(size), 1)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _source_health_status(attempted: int, updated: int, source_error: int = 0, failed: int = 0, rate_limited: int = 0) -> str:
    if attempted <= 0:
        return "SKIPPED"
    if failed or (updated == 0 and (source_error or rate_limited)):
        return "FAILED"
    if source_error or rate_limited or updated < attempted:
        return "DEGRADED"
    return "OK"


def _record_update_source_health(conn, run_id: str, started_at, stats: dict) -> None:
    ended_at = pd.Timestamp.now()
    rows = [
        {
            "run_id": run_id,
            "source": "akshare",
            "market": "CN",
            "operation": "daily_update",
            "started_at": started_at,
            "ended_at": ended_at,
            "status": _source_health_status(
                stats.get("cn_akshare_attempted", 0),
                stats.get("cn_akshare_updated", 0),
                stats.get("cn_akshare_source_error", 0),
                0,
            ),
            "attempted": stats.get("cn_akshare_attempted", 0),
            "updated": stats.get("cn_akshare_updated", 0),
            "source_error": stats.get("cn_akshare_source_error", 0),
            "circuit_skip": stats.get("cn_akshare_circuit_skip", 0),
            "message": "AkShare A股主源；连续瞬时错误会熔断",
            "stats_json": stats,
        },
        {
            "run_id": run_id,
            "source": "yfinance",
            "market": "CN",
            "operation": "daily_update",
            "started_at": started_at,
            "ended_at": ended_at,
            "status": _source_health_status(
                stats.get("cn_yfinance_attempted", 0) + stats.get("cn_yfinance_batch_attempted", 0),
                stats.get("cn_yfinance_updated", 0) + stats.get("cn_yfinance_batch_updated", 0),
                stats.get("cn_yfinance_source_error", 0) + stats.get("cn_yfinance_batch_source_error", 0),
                0,
                stats.get("cn_yfinance_rate_limited", 0),
            ),
            "attempted": stats.get("cn_yfinance_attempted", 0) + stats.get("cn_yfinance_batch_attempted", 0),
            "updated": stats.get("cn_yfinance_updated", 0) + stats.get("cn_yfinance_batch_updated", 0),
            "no_data": stats.get("cn_yfinance_batch_empty", 0),
            "source_error": stats.get("cn_yfinance_source_error", 0) + stats.get("cn_yfinance_batch_source_error", 0),
            "rate_limited": stats.get("cn_yfinance_rate_limited", 0),
            "circuit_skip": stats.get("cn_yfinance_skipped_circuit", 0) + stats.get("cn_yfinance_batch_skipped_circuit", 0),
            "message": "A股备源；AkShare 熔断后批量接管剩余标的",
            "stats_json": stats,
        },
        {
            "run_id": run_id,
            "source": "yfinance",
            "market": "HK",
            "operation": "daily_update",
            "started_at": started_at,
            "ended_at": ended_at,
            "status": _source_health_status(
                stats.get("hk_attempted", 0),
                stats.get("hk_yfinance_updated", 0),
                stats.get("hk_source_error", 0),
                stats.get("hk_failed", 0),
                stats.get("hk_yfinance_rate_limited", 0),
            ),
            "attempted": stats.get("hk_attempted", 0),
            "updated": stats.get("hk_yfinance_updated", 0),
            "source_error": stats.get("hk_source_error", 0),
            "rate_limited": stats.get("hk_yfinance_rate_limited", 0),
            "failed": stats.get("hk_failed", 0),
            "message": "港股主源；失败后尝试 AkShare",
            "stats_json": stats,
        },
    ]
    record_data_source_health(conn, rows)


def _run_cn_yfinance_batch_backup(
    conn,
    queue: list[dict],
    end_date_yf: str,
    batch_size: int,
    stats: dict,
) -> None:
    if not queue:
        return
    by_start: dict[str, list[str]] = {}
    for item in queue:
        by_start.setdefault(item["start_date"], []).append(item["symbol"])
    logger.info(f"Running CN yfinance batch backup for {len(queue)} symbols after AkShare circuit")
    for fetch_start, symbols in by_start.items():
        for chunk in _chunks(symbols, batch_size):
            stats["cn_yfinance_batch_attempted"] += len(chunk)
            try:
                batch = yf.fetch_cn_daily_batch(chunk, fetch_start, end_date_yf, log_empty=False)
            except Exception as exc:
                stats["cn_yfinance_batch_source_error"] += len(chunk)
                stats["cn_source_error"] += len(chunk)
                logger.warning(f"CN yfinance batch backup failed for {len(chunk)} symbols: {exc}")
                continue
            for sym in chunk:
                df = batch.get(sym, pd.DataFrame())
                status = yf.source_status(df)
                if not df.empty:
                    upsert_daily_price(conn, df)
                    stats["cn_updated"] += 1
                    stats["cn_yfinance_batch_updated"] += 1
                elif yf.is_rate_limited(df):
                    stats["cn_yfinance_rate_limited"] += 1
                    stats["cn_yfinance_batch_skipped_circuit"] += max(len(chunk) - chunk.index(sym) - 1, 0)
                    stats["cn_source_error"] += len(chunk) - chunk.index(sym)
                    logger.warning("yfinance CN batch rate limited; remaining current batch marked as source_error")
                    break
                elif status == yf.STATUS_SOURCE_ERROR:
                    stats["cn_yfinance_batch_source_error"] += 1
                    stats["cn_source_error"] += 1
                else:
                    stats["cn_yfinance_batch_empty"] += 1
                    stats["cn_no_data"] += 1


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

    for code, index_code in HK_INDEX_AKSHARE_SINA.items():
        try:
            df = ak.fetch_hk_index_daily_sina(index_code, start_date=start_date, end_date=end_date)
            if df.empty:
                df = yf.fetch_hk_index_daily(HK_INDEX_YFINANCE_FALLBACK[code], start_date=start_date_yf, end_date=end_date_yf)
                if not df.empty:
                    df["index_code"] = code
            if not df.empty:
                upsert_index_daily(conn, df)
        except Exception as e:
            logger.error(f"HK index daily failed {code}: {e}")

    logger.info("=== 初始化完成 ===")


def update_all(conn, config: dict):
    """每日增量更新：只更新已有日线数据的股票"""
    logger.info("=== 增量更新 ===")
    run_started_at = pd.Timestamp.now()
    run_id = f"UPDATE-{run_started_at.strftime('%Y%m%d%H%M%S')}"
    start_date, end_date = _default_dates(config)
    start_date_yf, end_date_yf = _default_dates_yf(config)
    repair_market_metadata(conn)
    stats = {
        "run_id": run_id,
        "cn_attempted": 0,
        "cn_updated": 0,
        "cn_no_data": 0,
        "cn_source_error": 0,
        "cn_akshare_attempted": 0,
        "cn_akshare_updated": 0,
        "cn_akshare_source_error": 0,
        "cn_akshare_circuit_skip": 0,
        "cn_yfinance_attempted": 0,
        "cn_yfinance_updated": 0,
        "cn_yfinance_source_error": 0,
        "cn_yfinance_rate_limited": 0,
        "cn_yfinance_skipped_circuit": 0,
        "cn_yfinance_batch_attempted": 0,
        "cn_yfinance_batch_updated": 0,
        "cn_yfinance_batch_empty": 0,
        "cn_yfinance_batch_source_error": 0,
        "cn_yfinance_batch_skipped_limit": 0,
        "cn_yfinance_batch_skipped_circuit": 0,
        "cn_failed": 0,
        "hk_attempted": 0,
        "hk_updated": 0,
        "hk_yfinance_updated": 0,
        "hk_akshare_updated": 0,
        "hk_no_data": 0,
        "hk_source_error": 0,
        "hk_yfinance_rate_limited": 0,
        "hk_failed": 0,
        "index_updated": 0,
        "index_failed": 0,
    }

    # 只更新 daily_price 表中已有的股票
    cn_with_data = conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
        ORDER BY symbol
        """
    ).fetchall()
    hk_with_data = conn.execute(
        """
        SELECT DISTINCT dp.symbol
        FROM daily_price dp
        LEFT JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country = 'HK' OR regexp_matches(dp.symbol, '^[0-9]{1,5}$')
        ORDER BY dp.symbol
        """
    ).fetchall()
    cn_symbols = [r[0] for r in cn_with_data]
    hk_symbols = [r[0] for r in hk_with_data]

    # A股 增量：AkShare 主通道；yfinance 只做兜底，遇到限流后本轮熔断。
    logger.info(f"Updating CN: {len(cn_symbols)} symbols with data")
    yfinance_cn_circuit_open = False
    akshare_cn_circuit_open = False
    akshare_consecutive_errors = 0
    data_cfg = config.get("data", {})
    akshare_circuit_threshold = int(data_cfg.get("akshare_cn_error_circuit_threshold", 12))
    cn_backup_after_akshare_circuit = bool(data_cfg.get("cn_backup_after_akshare_circuit", True))
    cn_backup_batch_size = int(data_cfg.get("cn_backup_batch_size", 80))
    cn_backup_max_symbols = int(data_cfg.get("cn_backup_max_symbols_after_circuit", 800))
    cn_backup_queue: list[dict] = []
    ak.configure_cn_daily_rate_limit(float(data_cfg.get("akshare_cn_min_interval_seconds", 0.8)))
    for sym in cn_symbols:
        last = get_last_trade_date(conn, sym)
        if last and (date.today() - last).days == 0:
            continue  # 今天已有数据，跳过
        stats["cn_attempted"] += 1
        fetch_start_yf = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else start_date_yf
        fetch_start_ak = (last + timedelta(days=1)).strftime("%Y%m%d") if last else start_date
        try:
            akshare_skipped_current = False
            source_used = None
            if akshare_cn_circuit_open:
                df = pd.DataFrame()
                ak_status = ak.STATUS_SOURCE_ERROR
                akshare_skipped_current = True
                stats["cn_akshare_circuit_skip"] += 1
                if cn_backup_after_akshare_circuit and not yfinance_cn_circuit_open:
                    if len(cn_backup_queue) < cn_backup_max_symbols:
                        cn_backup_queue.append({"symbol": sym, "start_date": fetch_start_yf})
                    else:
                        stats["cn_yfinance_batch_skipped_limit"] += 1
                        stats["cn_source_error"] += 1
                else:
                    stats["cn_source_error"] += 1
                continue
            else:
                stats["cn_akshare_attempted"] += 1
                df = ak.fetch_cn_stock_daily(sym, start_date=fetch_start_ak, end_date=end_date, log_empty=False)
                ak_status = ak.source_status(df)
                if not df.empty:
                    source_used = "akshare"
                if ak_status == ak.STATUS_SOURCE_ERROR:
                    akshare_consecutive_errors += 1
                    if ak.is_transient_source_error(df) and akshare_consecutive_errors >= akshare_circuit_threshold:
                        akshare_cn_circuit_open = True
                        logger.warning(
                            "AkShare CN transient errors reached "
                            f"{akshare_consecutive_errors}; circuit opened for the rest of this update run"
                        )
                else:
                    akshare_consecutive_errors = 0

            yf_status = None
            if df.empty and not yfinance_cn_circuit_open and not akshare_skipped_current:
                stats["cn_yfinance_attempted"] += 1
                df_yf = yf.fetch_cn_daily(sym, fetch_start_yf, end_date_yf, log_empty=False)
                yf_status = yf.source_status(df_yf)
                if yf.is_rate_limited(df_yf):
                    yfinance_cn_circuit_open = True
                    stats["cn_yfinance_rate_limited"] += 1
                    logger.warning("yfinance CN rate limited; circuit opened for the rest of this update run")
                elif yf_status == yf.STATUS_SOURCE_ERROR:
                    stats["cn_yfinance_source_error"] += 1
                if not df_yf.empty:
                    df = df_yf
                    source_used = "yfinance"
            elif df.empty and yfinance_cn_circuit_open:
                stats["cn_yfinance_skipped_circuit"] += 1

            if not df.empty:
                upsert_daily_price(conn, df)
                stats["cn_updated"] += 1
                if source_used == "akshare":
                    stats["cn_akshare_updated"] += 1
                elif source_used == "yfinance":
                    stats["cn_yfinance_updated"] += 1
            elif ak_status == ak.STATUS_SOURCE_ERROR and (yf_status is None or yf_status in {yf.STATUS_SOURCE_ERROR, yf.STATUS_RATE_LIMITED}):
                stats["cn_source_error"] += 1
                if not akshare_skipped_current:
                    stats["cn_akshare_source_error"] += 1
            else:
                stats["cn_no_data"] += 1
        except Exception as e:
            stats["cn_failed"] += 1
            logger.warning(f"CN daily update failed after fallback {sym}: {e}")

    _run_cn_yfinance_batch_backup(conn, cn_backup_queue, end_date_yf, cn_backup_batch_size, stats)

    # 港股 增量（yfinance 主通道）
    logger.info(f"Updating HK: {len(hk_symbols)} symbols with data")
    for sym in hk_symbols:
        last = get_last_trade_date(conn, sym)
        if last and (date.today() - last).days == 0:
            continue
        stats["hk_attempted"] += 1
        fetch_start_yf = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else start_date_yf
        fetch_start_ak = (last + timedelta(days=1)).strftime("%Y%m%d") if last else start_date
        try:
            df = yf.fetch_hk_daily(sym, fetch_start_yf, end_date_yf, log_empty=False)
            yf_status = yf.source_status(df)
            if not df.empty:
                upsert_daily_price(conn, df)
                stats["hk_updated"] += 1
                stats["hk_yfinance_updated"] += 1
            else:
                if yf.is_rate_limited(df):
                    stats["hk_yfinance_rate_limited"] += 1
                df = ak.fetch_hk_stock_daily(sym, start_date=fetch_start_ak, end_date=end_date, log_empty=False)
                ak_status = ak.source_status(df)
                if not df.empty:
                    upsert_daily_price(conn, df)
                    stats["hk_updated"] += 1
                    stats["hk_akshare_updated"] += 1
                elif yf_status in {yf.STATUS_SOURCE_ERROR, yf.STATUS_RATE_LIMITED} and ak_status == ak.STATUS_SOURCE_ERROR:
                    stats["hk_source_error"] += 1
                else:
                    stats["hk_no_data"] += 1
        except Exception as e:
            stats["hk_failed"] += 1
            logger.warning(f"HK daily update failed after fallback {sym}: {e}")

    # 指数增量
    for code in CN_INDEX_AKSHARE:
        try:
            df = ak.fetch_cn_index_daily(code, start_date=start_date, end_date=end_date)
            if not df.empty:
                upsert_index_daily(conn, df)
                stats["index_updated"] += 1
        except Exception as e:
            stats["index_failed"] += 1
            logger.warning(f"CN index update failed {code}: {e}")
    for code, index_code in HK_INDEX_AKSHARE_SINA.items():
        try:
            df = ak.fetch_hk_index_daily_sina(index_code, start_date=start_date, end_date=end_date)
            if df.empty:
                df = yf.fetch_hk_index_daily(HK_INDEX_YFINANCE_FALLBACK[code], start_date=start_date_yf, end_date=end_date_yf)
                if not df.empty:
                    df["index_code"] = code
            if not df.empty:
                upsert_index_daily(conn, df)
                stats["index_updated"] += 1
        except Exception as e:
            stats["index_failed"] += 1
            logger.warning(f"HK index update failed {code}: {e}")

    logger.info(
        "增量更新汇总: "
        f"CN attempted={stats['cn_attempted']} updated={stats['cn_updated']} "
        f"no_data={stats['cn_no_data']} source_error={stats['cn_source_error']} failed={stats['cn_failed']} "
        f"(ak_error={stats['cn_akshare_source_error']}, ak_circuit_skip={stats['cn_akshare_circuit_skip']}, "
        f"yf_error={stats['cn_yfinance_source_error']}, "
        f"yf_rate_limited={stats['cn_yfinance_rate_limited']}, yf_circuit_skip={stats['cn_yfinance_skipped_circuit']}, "
        f"yf_batch_attempted={stats['cn_yfinance_batch_attempted']}, yf_batch_updated={stats['cn_yfinance_batch_updated']}) | "
        f"HK attempted={stats['hk_attempted']} updated={stats['hk_updated']} "
        f"no_data={stats['hk_no_data']} source_error={stats['hk_source_error']} failed={stats['hk_failed']} "
        f"(yf_rate_limited={stats['hk_yfinance_rate_limited']}) | "
        f"index updated={stats['index_updated']} failed={stats['index_failed']}"
    )
    _record_update_source_health(conn, run_id, run_started_at, stats)
    logger.info("=== 增量更新完成 ===")
    return stats


def _write_quality_status(conn, rows: list[dict]) -> None:
    """Persist the latest structured data-quality check."""
    if not rows:
        return
    df_quality = pd.DataFrame(rows)
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_quality AS SELECT * FROM df_quality")
    conn.execute("DELETE FROM data_quality_status")
    conn.execute("""
        INSERT INTO data_quality_status (check_ts, status, metric, value, threshold, detail)
        SELECT check_ts, status, metric, value, threshold, detail FROM _tmp_quality
    """)


def check_data(conn, _config: dict, full: bool = False):
    """数据完整性检查。full=True 时进行成分股覆盖对比。"""
    from datetime import date as dt_date
    logger.info("=== 数据完整性检查 ===")
    repair_market_metadata(conn)

    # 基础统计
    cn_all = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')").fetchone()[0]
    hk_all = conn.execute("""
        SELECT COUNT(DISTINCT dp.symbol)
        FROM daily_price dp
        LEFT JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country='HK' OR regexp_matches(dp.symbol, '^[0-9]{1,5}$')
    """).fetchone()[0]
    latest = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    oldest = conn.execute("SELECT MIN(trade_date) FROM daily_price").fetchone()[0]

    logger.info(f"  A股日线: {cn_all} 只 | 港股日线: {hk_all} 只 | 合计: {cn_all+hk_all}")
    logger.info(f"  时间范围: {oldest} ~ {latest}")

    check_ts = pd.Timestamp.now()
    quality_rows = []

    def add_quality(metric: str, value, threshold, passed: bool, detail: str):
        quality_rows.append({
            "check_ts": check_ts,
            "status": "PASS" if passed else "WARN",
            "metric": metric,
            "value": float(value) if value is not None else None,
            "threshold": float(threshold) if threshold is not None else None,
            "detail": detail,
        })

    rules = DATA_QUALITY_RULES
    add_quality(
        "cn_stock_coverage",
        cn_all,
        rules["cn_min_stocks"],
        cn_all >= rules["cn_min_stocks"],
        f"A股覆盖 {cn_all} 只，阈值 {rules['cn_min_stocks']} 只",
    )
    add_quality(
        "hk_stock_coverage",
        hk_all,
        rules["hk_min_stocks"],
        hk_all >= rules["hk_min_stocks"],
        f"港股覆盖 {hk_all} 只，阈值 {rules['hk_min_stocks']} 只",
    )
    if oldest and latest:
        history_years = (latest - oldest).days / 365.0
        add_quality(
            "history_years",
            history_years,
            rules["min_history_years"],
            history_years >= rules["min_history_years"],
            f"历史区间 {oldest} ~ {latest}，约 {history_years:.1f} 年",
        )

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
    hk_latest = conn.execute("""
        SELECT MAX(dp.trade_date)
        FROM daily_price dp
        LEFT JOIN stock_info si ON dp.symbol = si.symbol
        WHERE si.country='HK' OR regexp_matches(dp.symbol, '^[0-9]{1,5}$')
    """).fetchone()[0]
    for label, lat in [("A股", cn_latest), ("港股", hk_latest)]:
        if lat:
            behind = (today - lat).days
            flag = "✅ 正常" if behind <= 1 else ("🟡 落后" if behind <= 3 else "🔴 严重落后")
            logger.info(f"  {label}: {lat} ({flag}, {behind}天)")
            add_quality(
                f"{'cn' if label == 'A股' else 'hk'}_data_age_days",
                behind,
                DATA_QUALITY_RULES["max_data_age_days"],
                behind <= DATA_QUALITY_RULES["max_data_age_days"],
                f"{label}最新日期 {lat}，落后 {behind} 天",
            )

    try:
        _write_quality_status(conn, quality_rows)
        failed = [r for r in quality_rows if r["status"] != "PASS"]
        if failed:
            logger.warning(f"数据质量检查完成：{len(failed)} 项告警 / {len(quality_rows)} 项")
        else:
            logger.info(f"数据质量检查通过：{len(quality_rows)} 项")
    except Exception as e:
        logger.warning(f"数据质量状态写入失败: {e}")

    return {
        "status": "WARN" if any(r["status"] != "PASS" for r in quality_rows) else "PASS",
        "checks": quality_rows,
    }


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
    init_db(conn)
    config = _load_config()
    stats = update_all(conn, config)
    conn.close()
    failure_count = (
        stats.get("cn_failed", 0)
        + stats.get("hk_failed", 0)
        + stats.get("cn_source_error", 0)
        + stats.get("hk_source_error", 0)
        + stats.get("index_failed", 0)
    )
    max_failures = config.get("data", {}).get("max_update_failures", 0)
    if failure_count > max_failures:
        raise click.ClickException(
            f"增量更新存在 {failure_count} 个失败项，超过阈值 {max_failures}。"
        )


@cli.command("update-snapshot")
@click.option("--markets", default="CN,HK", help="逗号分隔市场列表，例如 CN,HK")
def update_snapshot(markets):
    """从最新日线派生日终行情快照。"""
    conn = get_connection()
    init_db(conn)
    market_list = [item.strip().upper() for item in markets.split(",") if item.strip()]
    rows = build_market_snapshot_from_daily(conn, markets=market_list)
    conn.close()
    logger.info(f"市场行情快照更新完成: markets={','.join(market_list)} rows={rows}")


@cli.command()
@click.option("--full", is_flag=True, help="全面检查：对比指数成分股覆盖")
def check(full):
    """数据完整性检查（--full 进行成分股覆盖对比）"""
    conn = get_connection()
    init_db(conn)
    config = _load_config()
    check_data(conn, config, full=full)
    conn.close()


if __name__ == "__main__":
    cli()
