"""
多策略横向比较 — 统一口径，输出对比报告。
"""
from datetime import datetime

import pandas as pd

from src.config import load_config


def _get_config():
    return load_config()


def load_all_results() -> pd.DataFrame:
    """从 DuckDB 加载所有回测结果"""
    from src.data_pipeline.loader import get_connection
    conn = get_connection(read_only=True)
    df = conn.execute("""
        SELECT strategy_name, market, start_date, end_date,
               annual_return, cumulative_return, annual_volatility,
               sharpe_ratio, sortino_ratio, max_drawdown,
               max_drawdown_days, win_rate, avg_win_loss, turnover,
               info_ratio, benchmark_return, excess_return
        FROM backtest_results
        ORDER BY strategy_name, start_date DESC
    """).fetchdf()
    conn.close()
    return df


def compare_results(results: pd.DataFrame) -> pd.DataFrame:
    """生成策略对比摘要表"""
    if results.empty:
        return pd.DataFrame()

    summary = results.groupby("strategy_name").agg(
        avg_annual_return=("annual_return", "mean"),
        avg_sharpe=("sharpe_ratio", "mean"),
        avg_max_dd=("max_drawdown", "mean"),
        avg_turnover=("turnover", "mean"),
        latest_excess=("excess_return", "last"),
        runs=("annual_return", "count"),
    ).round(4)

    summary["score"] = (
        summary["avg_sharpe"] * 0.4
        + summary["avg_annual_return"] * 0.3
        - abs(summary["avg_max_dd"]) * 0.3
    )

    return summary.sort_values("score", ascending=False)


def generate_report(output_dir: str = None) -> str:
    """生成多策略对比报告（Markdown）"""
    results = load_all_results()

    if results.empty:
        return "# 策略对比报告\n\n暂无回测数据。请先运行 `bash scripts/run_backtest.sh all`。\n"

    summary = compare_results(results)
    config = _get_config()
    output_dir = output_dir or config.get("signals", {}).get("output_path", "output/signals").replace("signals", "reports")

    import os
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, f"backtest_report_{datetime.now().strftime('%Y%m%d')}.md")

    lines = [
        f"# 策略对比报告 — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 回测配置",
        f"- 股票池: 沪深300 + 恒生指数成分股",
        f"- 时间窗口: train 2019-2022 / valid 2023 / test 2024-至今",
        f"- 成本模型: 见 config/settings.yaml",
        "",
        "## 策略对比",
        "",
        "| 策略 | 年化收益 | 夏普 | 最大回撤 | 换手率 | 超额收益 | 综合得分 |",
        "|------|---------|------|---------|--------|---------|---------|",
    ]

    for idx, row in summary.iterrows():
        ann = f"{row['avg_annual_return']*100:.2f}%" if row['avg_annual_return'] else "N/A"
        sharpe = f"{row['avg_sharpe']:.2f}" if row['avg_sharpe'] else "N/A"
        dd = f"{row['avg_max_dd']*100:.2f}%" if row['avg_max_dd'] else "N/A"
        turnover = f"{row['avg_turnover']:.1f}x" if row['avg_turnover'] else "N/A"
        excess = f"{row['latest_excess']*100:.2f}%" if row.get('latest_excess') else "N/A"
        score = f"{row['score']:.3f}" if row['score'] else "N/A"

        lines.append(f"| {idx} | {ann} | {sharpe} | {dd} | {turnover} | {excess} | {score} |")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    return report_path


if __name__ == "__main__":
    path = generate_report()
    print(f"Report generated: {path}")
