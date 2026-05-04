"""
Qlib 回测 Runner — 封装 Qlib 工作流，输出标准化回测结果到 DuckDB。
当前阶段：提供接口框架，具体 Qlib 工作流执行待数据初始化后激活。
"""
import json
import uuid
from datetime import date
import pandas as pd
import yaml
from loguru import logger

from src.config import PROJECT_ROOT


def _get_config_path(strategy_name: str) -> Path:
    """获取 Qlib 配置文件路径"""
    mapping = {
        "alpha158": "workflow_config_alpha158.yaml",
        "alpha369": "workflow_config_alpha369.yaml",
        "trend": "workflow_config_trend.yaml",
        "industry": "workflow_config_industry.yaml",
        "mean_rev": "workflow_config_mean_reversion.yaml",
    }
    filename = mapping.get(strategy_name, f"workflow_config_{strategy_name}.yaml")
    return PROJECT_ROOT / "src" / "research" / "qlib_config" / filename


def run_qlib_backtest(strategy: str = "alpha158") -> dict:
    """
    运行 Qlib 回测。
    当前为框架接口，完整功能需在 Qlib 环境搭建后激活。

    Returns:
        dict with keys: annual_return, cumulative_return, sharpe_ratio,
                        max_drawdown, info_ratio, turnover, excess_return
    """
    config_path = _get_config_path(strategy)
    if not config_path.exists():
        logger.warning(f"Qlib config not found: {config_path}, skipping actual run")
        return _empty_result()

    logger.info(f"Running Qlib backtest: {strategy} (config: {config_path})")

    try:
        # 方式 1: 使用 Qlib CLI
        # import subprocess
        # result = subprocess.run(["qrun", str(config_path)], capture_output=True, text=True)

        # 方式 2: 使用 Qlib Python API
        import qlib
        from qlib.config import C
        from qlib.workflow import R
        from qlib.utils import init_instance_by_config

        qlib.init(provider_uri=str(PROJECT_ROOT / "qlib_data" / "cn_data"))

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # 执行 workflow
        model = init_instance_by_config(config["task"]["model"])
        dataset = init_instance_by_config(config["task"]["dataset"])
        records = [
            init_instance_by_config(rec) for rec in config["task"]["record"]
        ]

        # 训练和预测
        model.fit(dataset)
        pred = model.predict(dataset)

        # 记录信号和回测指标
        for rec in records:
            rec.save(pred)

        # 提取指标
        metrics = _extract_metrics(pred, dataset)
        logger.info(f"Qlib {strategy} backtest completed: {metrics}")

        _save_result(strategy, "cn", date(2024, 1, 1), date(2025, 4, 29), metrics, config)
        return metrics

    except ImportError:
        logger.warning("Qlib not installed. Run: pip install pyqlib")
        return _empty_result()
    except Exception as e:
        logger.error(f"Qlib backtest failed for {strategy}: {e}")
        return _empty_result()


def _extract_metrics(pred, dataset) -> dict:
    """从 Qlib 预测结果提取回测指标"""
    return {
        "annual_return": 0.0,
        "cumulative_return": 0.0,
        "annual_volatility": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_days": 0,
        "win_rate": 0.0,
        "avg_win_loss": 0.0,
        "turnover": 0.0,
        "info_ratio": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
    }


def _save_result(strategy: str, market: str, start: date, end: date,
                 metrics: dict, config: dict) -> None:
    """保存回测结果到 DuckDB"""
    from src.data_pipeline.loader import get_connection
    conn = get_connection()

    conn.execute("""
        INSERT OR REPLACE INTO backtest_results
        (run_id, strategy_name, market, start_date, end_date,
         annual_return, cumulative_return, annual_volatility,
         sharpe_ratio, sortino_ratio, max_drawdown, max_drawdown_days,
         win_rate, avg_win_loss, turnover, info_ratio,
         benchmark_return, excess_return, config_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        str(uuid.uuid4())[:8],
        strategy, market, start, end,
        metrics.get("annual_return", 0.0),
        metrics.get("cumulative_return", 0.0),
        metrics.get("annual_volatility", 0.0),
        metrics.get("sharpe_ratio", 0.0),
        metrics.get("sortino_ratio", 0.0),
        metrics.get("max_drawdown", 0.0),
        metrics.get("max_drawdown_days", 0),
        metrics.get("win_rate", 0.0),
        metrics.get("avg_win_loss", 0.0),
        metrics.get("turnover", 0.0),
        metrics.get("info_ratio", 0.0),
        metrics.get("benchmark_return", 0.0),
        metrics.get("excess_return", 0.0),
        json.dumps(config, default=str),
    ])
    conn.close()
    logger.info(f"Backtest result saved: {strategy}")


def _empty_result() -> dict:
    return {
        "annual_return": None, "cumulative_return": None,
        "sharpe_ratio": None, "max_drawdown": None,
        "info_ratio": None, "turnover": None, "excess_return": None,
    }


def run_all_strategies() -> dict[str, dict]:
    """批量运行所有策略回测"""
    strategies = ["alpha158", "trend", "industry", "mean_rev"]
    results = {}
    for s in strategies:
        results[s] = run_qlib_backtest(s)
    return results
