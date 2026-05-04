"""公共配置：项目路径 + 默认参数。YAML 配置文件为可选覆盖项。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("MM_PROJECT_ROOT", Path.cwd()))

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
    },
    "qlib": {
        "cn_data_path": "qlib_data/cn_data",
        "hk_data_path": "qlib_data/hk_data",
        "train_start": "2019-01-01",
        "train_end": "2022-12-31",
        "valid_start": "2023-01-01",
        "valid_end": "2023-12-31",
        "test_start": "2024-01-01",
        "test_end": "2025-04-29",
    },
    "portfolio": {
        "initial_capital_cn": 100000,
        "initial_capital_hk": 100000,
        "max_single_position_pct": 0.10,
        "max_industry_pct": 0.30,
        "max_drawdown_limit": 0.20,
        "rebalance": {"frequency": "weekly", "weekday": 5},
        "benchmark_weights": {"000300.SH": 0.35, "000905.SH": 0.15, "^HSI": 0.35, "3032.HK": 0.15},
    },
    "signals": {
        "min_confidence": 0.6,
        "output_format": "csv",
        "output_path": "output/signals",
    },
    "logging": {"level": "INFO", "file": "output/system.log"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 中的键覆盖 base，缺失的键保留 base 默认值"""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> dict:
    """加载配置：以 DEFAULT_CONFIG 为基准，YAML 存在时深度合并覆盖"""
    import yaml

    yaml_path = PROJECT_ROOT / "config" / "settings.yaml"
    if yaml_path.exists():
        with open(yaml_path) as f:
            overrides = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULT_CONFIG, overrides)
    return DEFAULT_CONFIG.copy()
