"""公共配置：项目路径 + 默认参数。YAML 配置文件为可选覆盖项。"""
import copy
import os
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get("MM_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()

# 默认配置（与 config/settings.yaml 同步，作为无 YAML 时的回退）
# ---- 数据质量规则 ----
# init/update 完成后必须满足的硬性条件，不满足则告警
DATA_QUALITY_RULES = {
    "cn_min_stocks": 650,          # A股最少覆盖数（HS300 ~300 + ZZ500 ~500，去重后 ~700）
    "hk_min_stocks": 44,           # 港股最少覆盖数（HSI ~44 + HSTECH ~20，去重后 ~50）
    "cn_max_missing_pct": 0.05,    # A股成分股缺失率阈值（>5% 告警）
    "hk_max_missing_pct": 0.10,    # 港股成分股缺失率阈值（>10% 告警）
    "max_data_age_days": 2,        # 数据最大允许落后天数（交易日，考虑节假日）
    "min_history_years": 4,        # 最少历史数据年数
    "check_on_init": True,         # init 完成后自动全面检查
}


DEFAULT_CONFIG = {
    "markets": {
        "cn": {
            "enabled": True,
            "country": "CN",
            "currency": "CNY",
            "commission_rate": 0.00025,
            "stamp_duty_rate": 0.001,
            "transfer_fee_rate": 0.00002,
            "slippage_bps": 5,
        },
        "hk": {
            "enabled": True,
            "country": "HK",
            "currency": "HKD",
            "commission_rate": 0.001,
            "stamp_duty_rate": 0.001,
            "transfer_fee_rate": 0.00002,
            "slippage_bps": 10,
        },
    },
    "data": {
        "history_years": 5,
        "update_time": "17:00",
        "duckdb_path": "data/duckdb/market.db",
        "raw_data_path": "data/raw",
        "max_update_failures": 0,
        "akshare_cn_min_interval_seconds": 0.8,
        "akshare_cn_error_circuit_threshold": 12,
        "cn_backup_after_akshare_circuit": True,
        "cn_backup_batch_size": 80,
        "cn_backup_max_symbols_after_circuit": 800,
        "sync_index_membership_on_update": True,
    },
    "qlib": {
        "cn_data_path": "qlib_data/cn_data",
        "hk_data_path": "qlib_data/hk_data",
        "train_start": "2019-01-01",
        "train_end": "2022-12-31",
        "valid_start": "2023-01-01",
        "valid_end": "2023-12-31",
        "test_start": "2024-01-01",
        "test_end": None,
        "publish_gate": {
            "min_ic_mean": 0.0,
            "min_icir": 0.30,
            "min_excess_return": 0.0,
            "max_drawdown_floor": -0.35,
            "retail_min_icir": 0.0,
            "retail_min_rank_ic_positive_rate": 0.52,
            "retail_min_excess_return": 0.05,
            "retail_min_sharpe_ratio": 0.80,
            "retail_max_drawdown_floor": -0.20,
            "retail_max_turnover": 12.0,
            "retail_min_avg_selected_count": 10.0,
            "retail_max_cash_drag": 0.35,
            "retail_max_actual_position_pct": 0.08,
        },
    },
    "portfolio": {
        "initial_capital_cn": 300000,
        "initial_capital_hk": 100000,
        "max_single_position_pct": 0.10,
        "overweight_single_position_pct": 0.15,
        "overweight_min_confidence": 0.90,
        "overweight_min_rank_score": 0.85,
        "max_industry_pct": 0.30,
        "max_gross_exposure_pct": 0.95,
        "cash_reserve_pct": 0.05,
        "min_rebalance_buy_confidence": 0.75,
        "min_rebalance_buy_rank_score": 0.50,
        "estimated_trade_fee_rate": 0.0015,
        "max_daily_turnover_pct": 0.30,
        "risk_profile": "auto",
        "risk_profiles": {
            "small": {
                "label": "小资金档",
                "max_stock_positions": 5,
                "max_single_position_pct": 0.20,
                "overweight_single_position_pct": 0.25,
                "estimated_minutes_per_operation": 4,
            },
            "medium": {
                "label": "中等资金档",
                "max_stock_positions": 10,
                "max_single_position_pct": 0.10,
                "overweight_single_position_pct": 0.15,
                "estimated_minutes_per_operation": 3,
            },
            "large": {
                "label": "大资金档",
                "max_stock_positions": 15,
                "max_single_position_pct": 0.10,
                "overweight_single_position_pct": 0.15,
                "estimated_minutes_per_operation": 3,
            },
        },
        "max_drawdown_limit": 0.20,
        "rebalance": {"frequency": "weekly", "weekday": 5},
        "benchmark_weights": {"000300.SH": 0.35, "000905.SH": 0.15, "HSI": 0.35, "HSTECH": 0.15},
        "allocation": {
            "enabled": True,
            "core_target_pct": 0.60,
            "satellite_target_pct": 0.40,
            "rebalance_tolerance_pct": 0.05,
            "min_trade_amount": 1000,
            "core_cash_priority": True,
        },
        "exposure": {
            "enabled": True,
            "benchmark_index": "000300",
            "max_industry_weight_warn": 0.30,
            "max_position_weight_warn": 0.15,
            "max_top5_weight_warn": 0.70,
            "max_unknown_industry_weight_warn": 0.05,
            "min_pe_coverage": 0.80,
            "min_pb_coverage": 0.80,
        },
    },
    "signals": {
        "min_confidence": 0.6,
        "output_format": "csv",
        "output_path": "output/signals",
    },
    "index_funds": {
        "enabled": True,
        "watchlist": [
            {
                "fund_code": "",
                "name": "沪深300指数基金",
                "fund_type": "ETF",
                "tracking_index": "000300",
                "tracking_index_name": "沪深300",
                "market": "CN",
                "currency": "CNY",
                "target_weight": 0.50,
                "enabled": True,
            },
            {
                "fund_code": "",
                "name": "中证500指数基金",
                "fund_type": "ETF",
                "tracking_index": "000905",
                "tracking_index_name": "中证500",
                "market": "CN",
                "currency": "CNY",
                "target_weight": 0.50,
                "enabled": True,
            },
        ],
        "rules": {
            "valuation_window_days": 756,
            "low_valuation_percentile": 0.30,
            "high_valuation_percentile": 0.80,
            "trend_ma_fast": 120,
            "trend_ma_slow": 250,
            "rebalance_threshold_pct": 0.05,
            "single_adjust_pct": 0.10,
            "min_confidence": 0.45,
        },
    },
    "logging": {"level": "INFO", "file": "output/system.log"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 中的键覆盖 base，缺失的键保留 base 默认值"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def load_config(env: str | None = None, config_dir: str | Path | None = None) -> dict:
    """加载配置：DEFAULT_CONFIG -> settings.yaml -> settings.<MM_ENV>.yaml。"""
    import yaml

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    base_dir = Path(config_dir) if config_dir is not None else PROJECT_ROOT / "config"
    for yaml_path in _config_paths(base_dir, env):
        if not yaml_path.exists():
            continue
        with yaml_path.open(encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        if not isinstance(overrides, dict):
            raise ValueError(f"Config file must contain a YAML mapping: {yaml_path}")
        cfg = _deep_merge(cfg, overrides)
    return cfg


def _config_paths(config_dir: Path, env: str | None = None) -> list[Path]:
    paths = [config_dir / "settings.yaml"]
    env_name = _normalize_env_name(os.environ.get("MM_ENV", "") if env is None else env)
    if env_name:
        paths.append(config_dir / f"settings.{env_name}.yaml")
    return paths


def _normalize_env_name(env: str | None) -> str:
    env_name = str(env or "").strip().lower()
    if not env_name:
        return ""
    allowed = env_name.replace("-", "").replace("_", "")
    if not allowed.isalnum():
        raise ValueError("MM_ENV may only contain letters, numbers, hyphens, and underscores")
    return env_name
