#!/bin/bash
# 收盘一站式：更新行情 → 持仓基础信息 → 字段覆盖 → 指数基金 → 生成信号 → 全局信号仲裁 → 资金分配计划 → 计算净值 → 信号收益跟踪 → 模型监控
# 注意：股票纸交易只允许在 09:40 开盘闭环执行，收盘闭环不回填开盘成交。
# 用法: bash scripts/daily_close.sh [strategy_name]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$SCRIPT_DIR")"
DASHBOARD="$HOME/Library/LaunchAgents/com.quant.dashboard.plist"

_python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

_resolve_python() {
  if [ -n "${PYTHON:-}" ]; then
    if _python_is_compatible "$PYTHON"; then
      echo "$PYTHON"
      return
    fi
    echo "PYTHON=$PYTHON 不是 Python 3.12+；本项目 requires-python >=3.12。" >&2
    exit 1
  fi

  for candidate in python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if _python_is_compatible "$resolved"; then
        echo "$resolved"
        return
      fi
    elif [ -x "$candidate" ] && _python_is_compatible "$candidate"; then
      echo "$candidate"
      return
    fi
  done

  echo "未找到 Python 3.12+。请安装 python@3.12，或用 PYTHON=/path/to/python3.12 bash scripts/daily_close.sh。" >&2
  exit 1
}

PYTHON="$(_resolve_python)"

_python_can_import() {
  "$1" - "$2" <<'PY' >/dev/null 2>&1
import importlib.util
import sys
raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) is not None else 1)
PY
}

_resolve_qlib_python() {
  if [ -n "${QLIB_PYTHON:-}" ]; then
    if _python_is_compatible "$QLIB_PYTHON" && _python_can_import "$QLIB_PYTHON" qlib; then
      echo "$QLIB_PYTHON"
      return
    fi
    echo "QLIB_PYTHON=$QLIB_PYTHON 不能同时满足 Python 3.12+ 和 import qlib；回退到 $1。" >&2
  fi

  for candidate in "$PROJECT/.venv-qlib/bin/python" "$1" python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if _python_is_compatible "$resolved" && _python_can_import "$resolved" qlib; then
        echo "$resolved"
        return
      fi
    elif [ -x "$candidate" ] && _python_is_compatible "$candidate" && _python_can_import "$candidate" qlib; then
      echo "$candidate"
      return
    fi
  done

  echo "$1"
}

QLIB_PYTHON="$(_resolve_qlib_python "$PYTHON")"
_dashboard_stopped=0

_restart_dashboard() {
  if [ "$_dashboard_stopped" = "1" ]; then
    echo "重启 Dashboard..."
    launchctl load "$DASHBOARD" 2>/dev/null || true
    _dashboard_stopped=0
  fi
}

cd "$PROJECT"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export no_proxy="*"

echo "=== $(date '+%H:%M:%S') 收盘处理开始 ==="
echo "Python: $PYTHON"
echo "Qlib Python: $QLIB_PYTHON"

# 1. 停 Dashboard（释放 DB 锁）
echo "1/14 暂停 Dashboard..."
launchctl unload "$DASHBOARD" 2>/dev/null || true
_dashboard_stopped=1
trap _restart_dashboard EXIT
sleep 3

# 2. 拉取最新行情
echo "2/14 更新行情数据..."
"$PYTHON" -m src.data_pipeline.main update

# 3. 补当前持仓基础信息（失败不阻塞收盘闭环）
echo "3/14 补当前持仓基础信息..."
"$PYTHON" -m src.portfolio.fundamentals_coverage update || true

# 4. 补目标池字段覆盖（失败不阻塞收盘闭环）
echo "4/14 补目标池字段覆盖..."
"$PYTHON" -m src.data_pipeline.field_coverage_backfill --scopes current_holdings,signal_candidates,target_universe --skip-industry-fetch --record-health || true

# 5. 更新指数基金
echo "5/14 更新指数基金数据..."
"$PYTHON" -m src.index_funds.pipeline update

# 6. 生成指数基金信号
echo "6/14 生成指数基金信号..."
"$PYTHON" -m src.index_funds.signals generate

# 7. 生成股票信号
echo "7/14 生成交易信号..."
"$PYTHON" -m src.signals.generator

# 8. Qlib production 日常推理（无 production 模型时自动跳过）
echo "8/14 Qlib production 日常推理..."
"$QLIB_PYTHON" -m src.backtest.qlib_runner predict-latest --model production || true

# 9. 生产预测就绪门禁
echo "9/14 生产预测就绪门禁..."
"$PYTHON" -m src.monitoring.model_monitor assert-prediction-ready

# 10. 规则/Qlib 统一信号仲裁
echo "10/16 全局信号仲裁..."
"$PYTHON" -m src.signals.arbiter

# 11. 市场层：估值刷新 + 市场状态 + T+1 仓位信号 + 指数搭配（在分配前，让权益预算用上当日仓位信号；非阻塞）
echo "11/16 市场温度计（市场状态/仓位信号/指数搭配）..."
"$PYTHON" -m src.market.daily --backfill-valuation || true

# 12. 统一资金分配计划（权益/现金分割由 M3 目标仓位驱动）
echo "12/16 生成统一资金分配计划..."
"$PYTHON" -m src.portfolio.allocator plan

# 13. 净值重算；纸交易只能由 scripts/open_paper_trade.py 在开盘窗口执行。
echo "13/16 重算资金净值..."
"$PYTHON" -m src.portfolio.nav_calculator

# 14. 信号收益跟踪
echo "14/16 更新信号收益跟踪..."
"$PYTHON" -m src.signals.outcome_tracker update

# 15. 生产模型监控
echo "15/16 生产模型监控..."
"$PYTHON" -m src.monitoring.model_monitor update

# 16. 虚拟账户竞赛前向执行（非阻塞，绝不影响收盘主链）
echo "16/18 虚拟账户竞赛前向执行..."
"$PYTHON" -m src.accounts.daily forward || true

# 17. 执行链健康检查（P1-E：自动判定当日是否干净 + 连续干净天数；非阻塞）
echo "17/18 执行链健康检查..."
"$PYTHON" -m src.portfolio.chain_health --days 5 || true

# 18. 基金闭环（G1：scanner 六维评分 + 持仓评估 + 持仓告警 + 推荐;非阻塞）
echo "18/18 基金闭环（scanner + monitor + recommendations）..."
"$PYTHON" -m src.funds.daily || true

# 重启 Dashboard
_restart_dashboard
trap - EXIT

echo "=== $(date '+%H:%M:%S') 收盘处理完成 ==="
