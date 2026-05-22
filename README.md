# make-money

个人本地量化研究与纸交易系统，面向散户、有限资金账户和中低频周/月调仓。系统目标不是自动真实下单，而是提供一条可复盘、可审计的决策辅助链路：

```text
数据更新 → 信号生成 → 统一资金池约束 → 调仓计划 → 纸交易/手动执行 → 组合体检与收益复盘
```

---

## Dashboard V2 产品使用手册

新版 Dashboard V2 是当前推荐的日常操作入口：

```text
http://localhost:5173/today
```

日常入口使用 Dashboard V2：

```bash
scripts/run_dashboard_v2.sh
```

打开 `http://localhost:5173/today`。旧 Streamlit 仅作为研究兜底入口，不作为每日操作入口。

完整手册见：

[docs/dashboard_v2_user_guide.md](docs/dashboard_v2_user_guide.md)

Dashboard 内置手册入口：

```text
http://localhost:5173/guide
```

### 使用前先确认预期

这套系统不是“保证赚钱”的黑箱荐股器，而是一套本地化的纪律执行工具。合理目标是长期年化跑赢核心指数 3-8%，但单年最大回撤仍可能达到 20-25%，任意 3 个月也可能跑输。首次使用建议先纸盘跑通，再小额跟随，不要一次性买满。

### 一句话理解 V2

Dashboard V2 不是研究报表集合，而是一个“收盘到调仓驾驶舱”。

它围绕散户每天真正要做的事组织页面：

```text
收盘后检查 → 生成建议 → 风险确认 → 手动执行 → 纸盘/收益复盘
```

### 本地启动

在项目根目录执行：

```bash
scripts/run_dashboard_v2.sh
```

启动后会有两个服务：

| 服务 | 端口 | 作用 |
|---|---:|---|
| V2 前端 | `5173` | React/Vite 页面 |
| V2 API | `8600` | FastAPI 数据接口 |

旧版 Streamlit Dashboard 仍保留为研究/迁移期兜底，不作为日常操作入口：

```text
http://localhost:8501
```

V2 读取的是同一套本地 DuckDB 和现有 domain service，不是 mock 数据，也不是从 8501 转发数据。

### 五个一级模块

| 模块 | 路径 | 用途 |
|---|---|---|
| 今日行动 | `/today` | 默认首页，判断今天数据能不能用、是否需要调仓 |
| 调仓执行 | `/rebalance` | 同屏查看 Core 基金、Satellite 个股、暂缓项和资金缺口 |
| 组合体检 | `/portfolio` | 检查现金、持仓、暴露风险和信号收益跟踪 |
| 市场与数据健康 | `/health` | 判断数据源、字段覆盖、任务状态和模型状态是否可用于决策 |
| 研究实验室 | `/research` | 收纳 Qlib、IC/ICIR、实验摘要和旧 Streamlit 入口 |
| 使用手册 | `/guide` | 查看投资预期、首次使用、复盘阈值、异常处理和术语解释 |

### 每天收盘后的最小流程

1. 打开 `http://localhost:5173/today`。
2. 顶部不是“数据可用”，就不要调仓，先去 `/health`。
3. 收盘闭环由本机定时任务自动执行；首页主按钮只会带你去 `查看调仓计划`、`查看任务状态` 或 `等待数据更新`。
4. 在 `/rebalance` 里优先看 `可执行`，再看 `需人工确认`，`暂缓` 默认不碰。
5. 执行后回 `/portfolio`，确认现金、持仓和风险警告。
6. 每周看一次信号收益跟踪：20 日 `alpha_vs_benchmark` 持续为正才说明策略真的在贡献超额。

### 安全边界

V2 首期只支持安全写入，不会替你真实下单。

允许：

- 查看真实数据、信号、资金池、风险和任务状态
- 记录现金流
- 记录指数基金持仓快照

不允许：

- 从 Dashboard V2 启动收盘闭环、开盘纸交易或研究任务
- 直接切换 production 模型
- 手动改信号状态
- 真实证券账户下单
- 绕过风控规则强行交易

所有写入都会进入 `dashboard_audit_log`。

默认数据保存在本机 DuckDB：`data/duckdb/market.db`。Dashboard V2 不会向外部服务上传你的持仓、现金流、信号或纸交易记录；AkShare、yfinance、Baostock 等只作为数据下载源使用。

### 使用口诀

1. 先打开 `/today`。
2. 顶部不是“数据可用”，就不调仓。
3. 主按钮让你去哪，就去哪。
4. `暂缓` 默认不碰，`冲突信号` 默认不做。
5. 执行后回 `/portfolio` 看现金、持仓和风险警告。

---

## 主要目录

| 路径 | 说明 |
|---|---|
| `src/data_pipeline/` | 数据采集、DuckDB schema、Qlib 数据准备 |
| `src/signals/` | 调仓信号生成和信号收益跟踪 |
| `src/portfolio/` | 统一资金池、风控、纸交易、组合体检 |
| `src/backtest/` | Qlib/vectorbt 回测与模型评估 |
| `src/dashboard/` | 旧 Streamlit Dashboard |
| `src/dashboard_v2/` | Dashboard V2 FastAPI API |
| `frontend/dashboard-v2/` | Dashboard V2 React/Vite 前端 |
| `docs/` | 项目文档、验证报告和使用手册 |
| `tests/` | 单元测试与契约测试 |

---

## 常用命令

```bash
# 启动 Dashboard V2
scripts/run_dashboard_v2.sh

# 运行全部 Python 测试
pytest -q

# Python lint
ruff check .

# 前端测试与构建
npm --prefix frontend/dashboard-v2 run test -- --run
npm --prefix frontend/dashboard-v2 run build
```
