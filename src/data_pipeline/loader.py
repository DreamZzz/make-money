"""
DuckDB 数据入库模块。
支持全量导入和增量更新，自动建表。
"""
import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
from loguru import logger

from src.config import PROJECT_ROOT, load_config


def get_config() -> dict:
    return load_config()


def get_db_path() -> str:
    config = get_config()
    db_path = Path(config["data"]["duckdb_path"])
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return str(db_path)


def init_db(conn: duckdb.DuckDBPyConnection) -> None:
    """执行 schema.sql 建表，并为已存在的旧库补充缺失字段"""
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path) as f:
        sql = f.read()
    conn.execute(sql)
    # 为旧库补充 signals 表的纸交易字段（新库由 schema.sql 覆盖，此处幂等）
    for col_ddl in [
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS pre_close DOUBLE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS adj_close DOUBLE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS adj_factor DOUBLE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS pe_ttm DOUBLE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS pb DOUBLE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS is_st BOOLEAN DEFAULT FALSE",
        "ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS update_time TIMESTAMP",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS pct_chg DOUBLE",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS volume_ratio DOUBLE",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS amplitude DOUBLE",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS float_market_cap DOUBLE",
        "ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'daily_price'",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS execution_price DOUBLE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS execution_date DATE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'ACTIVE'",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS status_reason VARCHAR",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS expires_at DATE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS superseded_by VARCHAR",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE portfolio_nav ADD COLUMN IF NOT EXISTS external_flow DOUBLE DEFAULT 0",
        "ALTER TABLE portfolio_nav ADD COLUMN IF NOT EXISTS net_contribution DOUBLE DEFAULT 0",
        "ALTER TABLE portfolio_nav ADD COLUMN IF NOT EXISTS investment_nav DOUBLE DEFAULT 1",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS order_value DOUBLE",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS fee DOUBLE",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS cash_before DOUBLE",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS cash_after DOUBLE",
        "ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS status_reason VARCHAR",
        "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS engine VARCHAR DEFAULT 'unknown'",
        "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS decision_scope VARCHAR DEFAULT 'decision'",
        "ALTER TABLE qlib_experiments ADD COLUMN IF NOT EXISTS mode VARCHAR",
        "ALTER TABLE qlib_experiments ADD COLUMN IF NOT EXISTS metrics_json TEXT",
        "ALTER TABLE qlib_experiments ADD COLUMN IF NOT EXISTS error_message VARCHAR",
        "ALTER TABLE qlib_predictions ADD COLUMN IF NOT EXISTS confidence DOUBLE",
        "ALTER TABLE qlib_predictions ADD COLUMN IF NOT EXISTS selected BOOLEAN DEFAULT FALSE",
        "ALTER TABLE qlib_daily_metrics ADD COLUMN IF NOT EXISTS portfolio_return DOUBLE",
        "ALTER TABLE qlib_daily_metrics ADD COLUMN IF NOT EXISTS benchmark_return DOUBLE",
        "ALTER TABLE qlib_model_registry ADD COLUMN IF NOT EXISTS published_at TIMESTAMP",
        "ALTER TABLE qlib_model_registry ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS buffer_n INTEGER",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS benchmark_name VARCHAR",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS constraint_profile VARCHAR DEFAULT 'theoretical_equal_weight'",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS account_capital DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS max_position_pct DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS cash_reserve_pct DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS lot_size INTEGER",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS avg_selected_count DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS min_selected_count INTEGER",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS avg_cash_drag DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS max_actual_position_pct DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS avg_weight_deviation DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS skipped_price_cap_avg DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS skipped_valuation_avg DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS skipped_budget_avg DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS max_pe_ttm DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS max_pb DOUBLE",
        "ALTER TABLE qlib_grid_results ADD COLUMN IF NOT EXISTS metrics_json TEXT",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS params_json TEXT",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS grid_json TEXT",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS best_constraint_profile VARCHAR",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS best_account_capital DOUBLE",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS avg_selected_count DOUBLE",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS avg_cash_drag DOUBLE",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS max_actual_position_pct DOUBLE",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS rank_ic_positive_rate DOUBLE",
        "ALTER TABLE qlib_candidate_results ADD COLUMN IF NOT EXISTS error_message VARCHAR",
        "ALTER TABLE data_source_health ADD COLUMN IF NOT EXISTS stats_json TEXT",
        "ALTER TABLE index_member_history ADD COLUMN IF NOT EXISTS end_date DATE",
        "ALTER TABLE index_member_history ADD COLUMN IF NOT EXISTS source VARCHAR",
        "ALTER TABLE index_member_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE index_member_history ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]:
        try:
            conn.execute(col_ddl)
        except Exception:
            pass
    try:
        conn.execute("""
            UPDATE signals s
            SET status = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM paper_orders po WHERE po.signal_id = s.signal_id
                    ) THEN 'FILLED'
                    ELSE 'SUPERSEDED'
                END,
                status_reason = COALESCE(
                    status_reason,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM paper_orders po WHERE po.signal_id = s.signal_id
                        ) THEN '历史成交信号'
                        ELSE '历史已处理信号归档'
                    END
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE s.executed = TRUE
              AND COALESCE(s.status, 'ACTIVE') = 'ACTIVE'
        """)
    except Exception:
        pass
    logger.info("Database tables initialized")


def upsert_stock_info(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入股票基本信息（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["symbol", "country", "name"]
    existing = [c for c in columns if c in df.columns]
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_stock_info AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO stock_info ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_stock_info
    """)
    return len(df)


def repair_market_metadata(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """补齐历史行情里已有、但 stock_info 缺失的市场元数据。"""
    hk_df = conn.execute("""
        SELECT DISTINCT symbol
        FROM daily_price
        WHERE regexp_matches(symbol, '^[0-9]{1,5}$')
          AND symbol NOT IN (SELECT symbol FROM stock_info)
    """).fetchdf()
    cn_df = conn.execute("""
        SELECT DISTINCT symbol
        FROM daily_price
        WHERE regexp_matches(symbol, '^[0-9]{6}$')
          AND symbol NOT IN (SELECT symbol FROM stock_info)
    """).fetchdf()

    inserted_hk = 0
    inserted_cn = 0
    if not hk_df.empty:
        hk_df["country"] = "HK"
        hk_df["name"] = hk_df["symbol"]
        hk_df["exchange"] = "SEHK"
        hk_df["currency"] = "HKD"
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_hk_meta AS SELECT * FROM hk_df")
        conn.execute("""
            INSERT OR REPLACE INTO stock_info (symbol, country, name, exchange, currency)
            SELECT symbol, country, name, exchange, currency FROM _tmp_hk_meta
        """)
        inserted_hk = len(hk_df)

    if not cn_df.empty:
        cn_df["country"] = "CN"
        cn_df["name"] = cn_df["symbol"]
        cn_df["exchange"] = cn_df["symbol"].map(lambda s: "SSE" if str(s).startswith(("6", "5")) else "SZSE")
        cn_df["currency"] = "CNY"
        conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_cn_meta AS SELECT * FROM cn_df")
        conn.execute("""
            INSERT OR REPLACE INTO stock_info (symbol, country, name, exchange, currency)
            SELECT symbol, country, name, exchange, currency FROM _tmp_cn_meta
        """)
        inserted_cn = len(cn_df)

    if inserted_hk or inserted_cn:
        logger.info(f"Repaired market metadata: HK={inserted_hk}, CN={inserted_cn}")
    return {"hk": inserted_hk, "cn": inserted_cn}


def upsert_daily_price(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入日线行情（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["symbol", "trade_date", "open", "high", "low", "close",
               "pre_close", "volume", "amount", "adj_close", "adj_factor",
               "turnover_rate", "pe_ttm", "pb", "is_st", "is_suspended"]
    existing = [c for c in columns if c in df.columns]
    # 确保 trade_date 是 date 类型
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_daily AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO daily_price ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_daily
    """)
    return len(df)


def upsert_market_snapshot(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入日终行情快照（覆盖更新）。"""
    if df.empty:
        return 0
    columns = [
        "symbol", "market", "trade_date", "update_time", "last_price", "open", "high", "low",
        "prev_close", "pct_chg", "volume", "amount", "turnover_rate", "volume_ratio",
        "amplitude", "pe_ttm", "pb", "market_cap", "float_market_cap", "is_suspended", "source",
    ]
    existing = [c for c in columns if c in df.columns]
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if "update_time" in df.columns:
        df["update_time"] = pd.to_datetime(df["update_time"], errors="coerce")
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_market_snapshot AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO market_snapshot ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_market_snapshot
    """)
    return len(df)


def build_market_snapshot_from_daily(
    conn: duckdb.DuckDBPyConnection,
    markets: Optional[list[str]] = None,
) -> int:
    """从最新日线派生日终快照，作为券商级快照字段的基础层。

    外部行情源补齐前，先用本地日线统一计算昨收、涨跌幅、量比和振幅。
    """
    market_values = [m.upper() for m in (markets or ["CN", "HK"])]
    markets_df = pd.DataFrame({"market": market_values})
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_snapshot_markets AS SELECT * FROM markets_df")
    df = conn.execute("""
        WITH priced AS (
            SELECT
                dp.symbol,
                COALESCE(
                    si.country,
                    CASE
                        WHEN regexp_matches(dp.symbol, '^[0-9]{6}$') THEN 'CN'
                        WHEN regexp_matches(dp.symbol, '^[0-9]{1,5}$') THEN 'HK'
                        ELSE 'OTHER'
                    END
                ) AS market,
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
                dp.is_suspended
            FROM daily_price dp
            LEFT JOIN stock_info si ON dp.symbol = si.symbol
        ),
        latest AS (
            SELECT *
            FROM priced
            WHERE market IN (SELECT market FROM _tmp_snapshot_markets)
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
            NULL::DOUBLE AS float_market_cap,
            COALESCE(is_suspended, FALSE) AS is_suspended,
            'daily_price' AS source
        FROM latest
    """).fetchdf()
    return upsert_market_snapshot(conn, df)


def upsert_index_daily(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入指数日线"""
    if df.empty:
        return 0
    columns = ["index_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    existing = [c for c in columns if c in df.columns]
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_idx AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO index_daily ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_idx
    """)
    return len(df)


def record_data_source_health(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> int:
    """Persist per-source update health rows for Dashboard diagnostics."""
    if not rows:
        return 0
    now = pd.Timestamp.now()
    normalized = []
    columns = [
        "run_id", "source", "market", "operation", "started_at", "ended_at", "status",
        "attempted", "updated", "no_data", "source_error", "rate_limited",
        "circuit_skip", "failed", "message", "stats_json",
    ]
    for row in rows:
        item = {col: row.get(col) for col in columns}
        item["run_id"] = str(item.get("run_id") or f"UPDATE-{now.strftime('%Y%m%d%H%M%S')}")
        item["source"] = str(item.get("source") or "unknown")
        item["market"] = str(item.get("market") or "UNKNOWN")
        item["operation"] = str(item.get("operation") or "daily_update")
        item["started_at"] = pd.to_datetime(item.get("started_at") or now)
        item["ended_at"] = pd.to_datetime(item.get("ended_at") or now)
        item["status"] = str(item.get("status") or "OK")
        for metric in ["attempted", "updated", "no_data", "source_error", "rate_limited", "circuit_skip", "failed"]:
            item[metric] = int(item.get(metric) or 0)
        stats_json = item.get("stats_json")
        if isinstance(stats_json, (dict, list)):
            item["stats_json"] = json.dumps(stats_json, ensure_ascii=False, default=str)
        elif stats_json is not None:
            item["stats_json"] = str(stats_json)
        normalized.append(item)
    df = pd.DataFrame(normalized, columns=columns)
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_data_source_health AS SELECT * FROM df")
    conn.execute("""
        INSERT OR REPLACE INTO data_source_health (
            run_id, source, market, operation, started_at, ended_at, status,
            attempted, updated, no_data, source_error, rate_limited, circuit_skip,
            failed, message, stats_json
        )
        SELECT
            run_id, source, market, operation, started_at, ended_at, status,
            attempted, updated, no_data, source_error, rate_limited, circuit_skip,
            failed, message, stats_json
        FROM _tmp_data_source_health
    """)
    return len(df)


def upsert_fund_info(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入指数基金基本信息（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["fund_code", "name", "fund_type", "tracking_index", "market", "currency", "enabled"]
    existing = [c for c in columns if c in df.columns]
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fund_info AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO fund_info ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_fund_info
    """)
    return len(df)


def upsert_fund_nav(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入指数基金净值/行情（覆盖更新）"""
    if df.empty:
        return 0
    columns = ["fund_code", "trade_date", "nav", "close", "premium_discount"]
    existing = [c for c in columns if c in df.columns]
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fund_nav AS SELECT * FROM df")
    conn.execute(f"""
        INSERT OR REPLACE INTO fund_nav ({", ".join(existing)})
        SELECT {", ".join(existing)} FROM _tmp_fund_nav
    """)
    return len(df)


def upsert_financials(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """导入财务数据"""
    if df.empty:
        return 0
    conn.execute("CREATE OR REPLACE TEMP TABLE _tmp_fin AS SELECT * FROM df")
    conn.execute("""
        INSERT OR REPLACE INTO financials
        SELECT * FROM _tmp_fin
    """)
    return len(df)


def get_last_trade_date(conn: duckdb.DuckDBPyConnection, symbol: str) -> Optional[date]:
    """获取某股票已存储的最新交易日期，用于增量更新"""
    result = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE symbol = ?", [symbol]
    ).fetchone()
    return result[0] if result and result[0] else None


def get_all_symbols(conn: duckdb.DuckDBPyConnection, country: Optional[str] = None) -> list[str]:
    """获取已存储的所有股票代码"""
    if country:
        result = conn.execute(
            "SELECT DISTINCT symbol FROM stock_info WHERE country = ?", [country]
        ).fetchall()
    else:
        result = conn.execute("SELECT DISTINCT symbol FROM stock_info").fetchall()
    return [r[0] for r in result]


def export_to_csv(conn: duckdb.DuckDBPyConnection, table: str, output_path: str) -> str:
    """将表导出为 CSV（用于 Qlib 数据转换等）"""
    df = conn.execute(f"SELECT * FROM {table}").fetchdf()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info(f"Exported {table} → {output_path} ({len(df)} rows)")
    return output_path


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接，自动建表。写模式遇到锁冲突时自动重试。"""
    import time as _time
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    max_retries = 5 if not read_only else 1
    for attempt in range(max_retries):
        try:
            conn = duckdb.connect(db_path, read_only=read_only)
            return conn
        except duckdb.IOException as e:
            if "Conflicting lock" in str(e) and attempt < max_retries - 1:
                logger.warning(f"DB locked, retrying ({attempt + 1}/{max_retries})...")
                _time.sleep(2)
            else:
                raise
    raise RuntimeError("Failed to acquire DuckDB lock")
