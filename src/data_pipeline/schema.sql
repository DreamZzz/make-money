-- DuckDB 数据库建表语句
-- 所有表均为本地嵌入式存储，无需用户/权限管理

-- ============================================
-- 1. 股票基本信息表
-- ============================================
CREATE TABLE IF NOT EXISTS stock_info (
    symbol          VARCHAR NOT NULL,        -- 股票代码，如 600519.SH, 0700.HK
    country         VARCHAR NOT NULL,        -- 市场: CN / HK
    name            VARCHAR,                 -- 股票名称
    industry        VARCHAR,                 -- GICS 行业分类
    sector          VARCHAR,                 -- 板块: 主板/创业板/科创板
    market_cap      DOUBLE,                  -- 总市值（亿）
    listed_date     DATE,                    -- 上市日期
    exchange        VARCHAR,                 -- 交易所: SSE/SZSE/SEHK
    currency        VARCHAR,                 -- CNY / HKD
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol)
);

-- ============================================
-- 2. 日线行情表
-- ============================================
CREATE TABLE IF NOT EXISTS daily_price (
    symbol          VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    pre_close       DOUBLE,
    volume          DOUBLE,                  -- 成交量（手）
    amount          DOUBLE,                  -- 成交额（万元）
    adj_close       DOUBLE,                  -- 复权收盘价
    adj_factor      DOUBLE,                  -- 复权因子
    turnover_rate   DOUBLE,                  -- 换手率（%）
    pe_ttm          DOUBLE,                  -- 市盈率 TTM
    pb              DOUBLE,                  -- 市净率
    is_st           BOOLEAN DEFAULT FALSE,   -- 是否 ST
    is_suspended    BOOLEAN DEFAULT FALSE,   -- 是否停牌
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

-- ============================================
-- 2b. 日终行情快照表
-- ============================================
CREATE TABLE IF NOT EXISTS market_snapshot (
    symbol           VARCHAR NOT NULL,
    market           VARCHAR NOT NULL,       -- CN / HK
    trade_date       DATE NOT NULL,
    update_time      TIMESTAMP,
    last_price       DOUBLE,
    open             DOUBLE,
    high             DOUBLE,
    low              DOUBLE,
    prev_close       DOUBLE,
    pct_chg          DOUBLE,                 -- 涨跌幅（%）
    volume           DOUBLE,
    amount           DOUBLE,
    turnover_rate    DOUBLE,
    volume_ratio     DOUBLE,                 -- 量比（对比近20日均量）
    amplitude        DOUBLE,                 -- 振幅（%）
    pe_ttm           DOUBLE,
    pb               DOUBLE,
    market_cap       DOUBLE,
    float_market_cap DOUBLE,
    is_suspended     BOOLEAN DEFAULT FALSE,
    source           VARCHAR DEFAULT 'daily_price',
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

-- ============================================
-- 3. 指数行情表
-- ============================================
CREATE TABLE IF NOT EXISTS index_daily (
    index_code      VARCHAR NOT NULL,        -- 指数代码: 000300, HSI, HSTECH
    trade_date      DATE NOT NULL,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    amount          DOUBLE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (index_code, trade_date)
);

-- ============================================
-- 3b. 指数历史成分表
-- ============================================
CREATE TABLE IF NOT EXISTS index_member_history (
    index_code      VARCHAR NOT NULL,        -- 指数代码: 000300 / 000905 / HSI / HSTECH
    symbol          VARCHAR NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE,
    source          VARCHAR,                 -- historical / akshare_snapshot 等
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (index_code, symbol, start_date)
);

-- ============================================
-- 4. 财务数据表（季度）
-- ============================================
CREATE TABLE IF NOT EXISTS financials (
    symbol          VARCHAR NOT NULL,
    report_date     DATE NOT NULL,           -- 报告期
    revenue         DOUBLE,                  -- 营业收入（亿）
    net_profit      DOUBLE,                  -- 归母净利润（亿）
    total_assets    DOUBLE,                  -- 总资产（亿）
    total_equity    DOUBLE,                  -- 股东权益（亿）
    operating_cf    DOUBLE,                  -- 经营活动现金流（亿）
    roe             DOUBLE,                  -- ROE（%）
    roa             DOUBLE,                  -- ROA（%）
    gross_margin    DOUBLE,                  -- 毛利率（%）
    net_margin      DOUBLE,                  -- 净利率（%）
    debt_ratio      DOUBLE,                  -- 资产负债率（%）
    eps             DOUBLE,                  -- 每股收益
    bvps            DOUBLE,                  -- 每股净资产
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, report_date)
);

-- ============================================
-- 5. 信号表
-- ============================================
CREATE TABLE IF NOT EXISTS signals (
    signal_id       VARCHAR NOT NULL,        -- 唯一信号ID
    model_name      VARCHAR NOT NULL,        -- 模型/策略名称
    model_version   VARCHAR,                 -- 模型版本
    symbol          VARCHAR NOT NULL,
    signal_ts       TIMESTAMP NOT NULL,      -- 信号生成时间
    horizon         VARCHAR,                 -- 预期持仓周期: 1d / 5d / 20d
    score           DOUBLE,                  -- 信号强度
    side            VARCHAR,                 -- BUY / SELL / HOLD
    confidence      DOUBLE,                  -- 置信度 [0, 1]
    expected_holding_days INTEGER,           -- 预期持有天数
    max_position_pct DOUBLE,                 -- 最大仓位百分比
    thesis          VARCHAR,                 -- 信号理由简述
    risk_tags       VARCHAR[],               -- 风险标签
    executed        BOOLEAN DEFAULT FALSE,   -- 是否已被纸交易引擎执行
    execution_price DOUBLE,                  -- 实际成交价
    execution_date  DATE,                    -- 实际成交日期
    status          VARCHAR DEFAULT 'ACTIVE',-- ACTIVE / FILLED / NO_ACTION / EXPIRED / SUPERSEDED
    status_reason   VARCHAR,                 -- 状态说明
    expires_at      DATE,                    -- 信号最晚有效日期（可选）
    superseded_by   VARCHAR,                 -- 替代该信号的新信号ID
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id)
);

-- ============================================
-- 5a. 全局信号仲裁决策表
-- ============================================
CREATE TABLE IF NOT EXISTS signal_decisions (
    decision_id      VARCHAR NOT NULL,
    signal_id        VARCHAR NOT NULL,
    decision_date    DATE NOT NULL,
    model_name       VARCHAR,
    model_version    VARCHAR,
    symbol           VARCHAR NOT NULL,
    side             VARCHAR,
    signal_ts        TIMESTAMP,
    decision         VARCHAR NOT NULL,        -- ACCEPTED / REJECTED
    decision_reason  VARCHAR,
    consensus_status VARCHAR,                 -- QLIB_HOLDING / CONSENSUS / NEUTRAL / DIVERGENCE / STALE / MISSING / SELL
    arbiter_version  VARCHAR DEFAULT 'signal_arbiter_v1',
    qlib_prediction_date DATE,
    qlib_rank        INTEGER,
    qlib_confidence  DOUBLE,
    priority_score   DOUBLE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id)
);

-- ============================================
-- 5b. 信号实际收益跟踪表
-- ============================================
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id        VARCHAR NOT NULL,
    horizon_days     INTEGER NOT NULL,
    model_name       VARCHAR,
    model_version    VARCHAR,
    symbol           VARCHAR NOT NULL,
    side             VARCHAR,
    signal_date      DATE,
    execution_date   DATE,
    execution_price  DOUBLE,
    outcome_date     DATE,
    outcome_price    DOUBLE,
    return_pct       DOUBLE,
    benchmark_code   VARCHAR,
    benchmark_return_pct DOUBLE,
    alpha_vs_benchmark DOUBLE,
    status           VARCHAR DEFAULT 'PENDING', -- READY / PENDING
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id, horizon_days)
);

-- ============================================
-- 5c. 生产模型监控告警表
-- ============================================
CREATE TABLE IF NOT EXISTS model_monitor_alerts (
    alert_id        VARCHAR NOT NULL,
    model_name      VARCHAR,
    model_version   VARCHAR,
    experiment_id   VARCHAR,
    alert_date      DATE NOT NULL,
    severity        VARCHAR NOT NULL,        -- INFO / WARN / CRITICAL
    metric_name     VARCHAR NOT NULL,
    observed_value  DOUBLE,
    threshold_value DOUBLE,
    status          VARCHAR DEFAULT 'ACTIVE',-- ACTIVE / RESOLVED
    message         VARCHAR,
    context_json    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    PRIMARY KEY (alert_id)
);

-- ============================================
-- 8. 纸交易持仓表
-- ============================================
CREATE TABLE IF NOT EXISTS paper_positions (
    strategy_name   VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    symbol          VARCHAR NOT NULL,
    quantity        DOUBLE NOT NULL DEFAULT 0,
    avg_cost        DOUBLE,                  -- 持仓均价（含佣金）
    current_price   DOUBLE,                  -- 当日收盘价
    market_value    DOUBLE,                  -- 持仓市值
    pnl             DOUBLE,                  -- 浮动盈亏（元）
    pnl_pct         DOUBLE,                  -- 浮动盈亏率
    weight          DOUBLE,                  -- 持仓权重（占总资产）
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, trade_date, symbol)
);

-- ============================================
-- 9. 组合净值表
-- ============================================
CREATE TABLE IF NOT EXISTS portfolio_nav (
    strategy_name   VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    nav             DOUBLE,                  -- 净值（初始=1.0）
    daily_return    DOUBLE,                  -- 当日收益率
    cash            DOUBLE,                  -- 现金余额
    position_value  DOUBLE,                  -- 持仓市值
    total_value     DOUBLE,                  -- 总资产
    external_flow   DOUBLE DEFAULT 0,        -- 当日外部现金流（入金为正，出金为负）
    net_contribution DOUBLE DEFAULT 0,       -- 累计净投入本金
    investment_nav  DOUBLE DEFAULT 1,        -- 现金流校正净值
    drawdown        DOUBLE,                  -- 当日回撤（相对峰值）
    sharpe_rolling  DOUBLE,                  -- 滚动20日夏普
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, trade_date)
);

-- ============================================
-- 6. 回测结果表
-- ============================================
CREATE TABLE IF NOT EXISTS backtest_results (
    run_id          VARCHAR NOT NULL,        -- 回测运行ID
    strategy_name   VARCHAR NOT NULL,
    market          VARCHAR NOT NULL,        -- CN / HK / combined
    engine          VARCHAR DEFAULT 'unknown', -- qlib / vectorbt / rule
    decision_scope  VARCHAR DEFAULT 'decision', -- decision / research_only
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    annual_return   DOUBLE,                  -- 年化收益率
    cumulative_return DOUBLE,                -- 累计收益率
    annual_volatility DOUBLE,               -- 年化波动率
    sharpe_ratio    DOUBLE,                  -- 夏普比率
    sortino_ratio   DOUBLE,                  -- 索提诺比率
    max_drawdown    DOUBLE,                  -- 最大回撤
    max_drawdown_days INTEGER,              -- 最长回撤回补天数
    win_rate        DOUBLE,                  -- 胜率
    avg_win_loss    DOUBLE,                  -- 盈亏比
    turnover        DOUBLE,                  -- 年化换手率
    info_ratio      DOUBLE,                  -- 信息比率
    benchmark_return DOUBLE,                 -- 基准年化收益率
    excess_return   DOUBLE,                  -- 超额收益
    config_snapshot TEXT,                    -- 回测配置快照 JSON
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id)
);

-- ============================================
-- 6b. Qlib 实验记录表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_experiments (
    experiment_id   VARCHAR NOT NULL,
    run_id          VARCHAR,
    model_name      VARCHAR NOT NULL DEFAULT 'alpha158',
    model_version   VARCHAR,
    mode            VARCHAR NOT NULL,        -- fixed / walk_forward / latest_prediction
    status          VARCHAR NOT NULL,        -- RUNNING / SUCCEEDED / FAILED
    market          VARCHAR DEFAULT 'CN',
    train_start     DATE,
    train_end       DATE,
    valid_start     DATE,
    valid_end       DATE,
    test_start      DATE,
    test_end        DATE,
    data_start      DATE,
    data_end        DATE,
    data_symbols    INTEGER,
    qlib_installed  BOOLEAN,
    qlib_data_ready BOOLEAN,
    python_version  VARCHAR,
    qlib_version    VARCHAR,
    lightgbm_version VARCHAR,
    config_snapshot TEXT,
    metrics_json    TEXT,
    log_path        VARCHAR,
    error_message   VARCHAR,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at        TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (experiment_id)
);

-- ============================================
-- 6c. Qlib 预测截面表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_predictions (
    experiment_id   VARCHAR NOT NULL,
    model_name      VARCHAR NOT NULL DEFAULT 'alpha158',
    model_version   VARCHAR,
    mode            VARCHAR,
    prediction_date DATE NOT NULL,
    symbol          VARCHAR NOT NULL,
    score           DOUBLE,
    rank            INTEGER,
    confidence      DOUBLE,
    selected        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (experiment_id, prediction_date, symbol)
);

-- ============================================
-- 6d. Qlib 日度评估表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_daily_metrics (
    experiment_id   VARCHAR NOT NULL,
    metric_date     DATE NOT NULL,
    mode            VARCHAR,
    prediction_count INTEGER,
    ic              DOUBLE,
    rank_ic         DOUBLE,
    top_return      DOUBLE,
    bottom_return   DOUBLE,
    spread_return   DOUBLE,
    portfolio_return DOUBLE,
    benchmark_return DOUBLE,
    turnover        DOUBLE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (experiment_id, metric_date)
);

-- ============================================
-- 6e. Qlib 模型注册表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_model_registry (
    model_version   VARCHAR NOT NULL,
    experiment_id   VARCHAR NOT NULL,
    model_name      VARCHAR NOT NULL DEFAULT 'alpha158',
    status          VARCHAR NOT NULL,        -- candidate / staging / production / archived
    market          VARCHAR DEFAULT 'CN',
    model_path      VARCHAR,
    metrics_json    TEXT,
    published_at    TIMESTAMP,
    archived_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_version)
);

-- ============================================
-- 6f. Qlib 参数网格评估表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_grid_results (
    grid_id          VARCHAR NOT NULL,
    source_experiment_id VARCHAR NOT NULL,
    model_name       VARCHAR NOT NULL DEFAULT 'alpha158',
    mode             VARCHAR,
    top_n            INTEGER NOT NULL,
    holding_days     INTEGER NOT NULL,
    rebalance_freq   VARCHAR NOT NULL,       -- daily / weekly / monthly
    buffer_n         INTEGER,
    benchmark_name   VARCHAR NOT NULL,       -- 000300 / 000905 / ALL_EQ_PROXY / MIXED_EQUAL
    constraint_profile VARCHAR DEFAULT 'theoretical_equal_weight',
    account_capital  DOUBLE,
    max_position_pct DOUBLE,
    cash_reserve_pct DOUBLE,
    lot_size         INTEGER,
    avg_selected_count DOUBLE,
    min_selected_count INTEGER,
    avg_cash_drag   DOUBLE,
    max_actual_position_pct DOUBLE,
    avg_weight_deviation DOUBLE,
    skipped_price_cap_avg DOUBLE,
    skipped_valuation_avg DOUBLE,
    skipped_budget_avg DOUBLE,
    max_pe_ttm      DOUBLE,
    max_pb          DOUBLE,
    start_date       DATE,
    end_date         DATE,
    annual_return    DOUBLE,
    cumulative_return DOUBLE,
    annual_volatility DOUBLE,
    sharpe_ratio     DOUBLE,
    max_drawdown     DOUBLE,
    turnover         DOUBLE,
    benchmark_return DOUBLE,
    excess_return    DOUBLE,
    metrics_json     TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (grid_id)
);

-- ============================================
-- 6g. 规则 vs Qlib A/B 影子跟踪
-- ============================================
CREATE TABLE IF NOT EXISTS rule_qlib_ab_snapshots (
    run_id          VARCHAR NOT NULL,
    snapshot_date   DATE NOT NULL,
    signal_date     DATE,
    prediction_date DATE,
    experiment_id   VARCHAR,
    model_version   VARCHAR,
    top_n           INTEGER,
    secondary_top_n INTEGER,
    champion_source VARCHAR,
    status          VARCHAR,
    summary_json    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS rule_qlib_ab_members (
    run_id          VARCHAR NOT NULL,
    arm             VARCHAR NOT NULL,        -- A_RULE_BUY / B_QLIB_TOPN / C_CONSENSUS
    symbol          VARCHAR NOT NULL,
    name            VARCHAR,
    classification  VARCHAR,
    rule_side       VARCHAR,
    rule_models     VARCHAR,
    rule_confidence DOUBLE,
    rule_score      DOUBLE,
    qlib_rank       INTEGER,
    qlib_score      DOUBLE,
    weight          DOUBLE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, arm, symbol)
);

-- ============================================
-- 6h. Qlib 候选实验批跑结果表
-- ============================================
CREATE TABLE IF NOT EXISTS qlib_candidate_results (
    candidate_id       VARCHAR NOT NULL,
    batch_id           VARCHAR NOT NULL,
    experiment_id      VARCHAR,
    model_name         VARCHAR NOT NULL DEFAULT 'alpha158',
    model_family       VARCHAR NOT NULL,
    model_variant      VARCHAR NOT NULL,
    status             VARCHAR NOT NULL,        -- RUNNING / SUCCEEDED / FAILED / SKIPPED
    mode               VARCHAR NOT NULL,        -- fixed / walk_forward
    params_json        TEXT,
    grid_json          TEXT,
    best_benchmark     VARCHAR,
    best_top_n         INTEGER,
    best_holding_days  INTEGER,
    best_rebalance_freq VARCHAR,
    best_buffer_n      INTEGER,
    best_constraint_profile VARCHAR,
    best_account_capital DOUBLE,
    avg_selected_count DOUBLE,
    avg_cash_drag      DOUBLE,
    max_actual_position_pct DOUBLE,
    annual_return      DOUBLE,
    sharpe_ratio       DOUBLE,
    max_drawdown       DOUBLE,
    turnover           DOUBLE,
    benchmark_return   DOUBLE,
    excess_return      DOUBLE,
    ic_mean            DOUBLE,
    icir               DOUBLE,
    rank_ic_mean       DOUBLE,
    rank_ic_positive_rate DOUBLE,
    score              DOUBLE,
    error_message      VARCHAR,
    started_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at           TIMESTAMP,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (candidate_id, batch_id)
);

-- ============================================
-- 7. 订单模拟/跟踪表
-- ============================================
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        VARCHAR NOT NULL,        -- 订单ID
    signal_id       VARCHAR,                 -- 关联信号ID
    symbol          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,        -- BUY / SELL
    order_qty       DOUBLE,                   -- 委托数量（股）
    order_price     DOUBLE,                  -- 委托价格
    order_value     DOUBLE,                  -- 成交金额（不含费用）
    fee             DOUBLE,                  -- 交易费用
    cash_before     DOUBLE,                  -- 成交前可用现金
    cash_after      DOUBLE,                  -- 成交后可用现金
    order_ts        TIMESTAMP NOT NULL,      -- 下单时间
    status          VARCHAR DEFAULT 'PENDING', -- PENDING / FILLED / CANCELLED
    status_reason   VARCHAR,                 -- 状态说明/跳过原因
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (order_id)
);

-- ============================================
-- 10. 数据质量状态表
-- ============================================
CREATE TABLE IF NOT EXISTS data_quality_status (
    check_ts        TIMESTAMP NOT NULL,
    status          VARCHAR NOT NULL,        -- PASS / WARN
    metric          VARCHAR NOT NULL,
    value           DOUBLE,
    threshold       DOUBLE,
    detail          VARCHAR,
    PRIMARY KEY (check_ts, metric)
);

-- ============================================
-- 10b. 数据源健康状态表
-- ============================================
CREATE TABLE IF NOT EXISTS data_source_health (
    run_id          VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,        -- akshare / yfinance / baostock 等
    market          VARCHAR NOT NULL,        -- CN / HK / INDEX
    operation       VARCHAR NOT NULL,        -- daily_update / index_update
    started_at      TIMESTAMP,
    ended_at        TIMESTAMP,
    status          VARCHAR NOT NULL,        -- OK / DEGRADED / FAILED / SKIPPED
    attempted       INTEGER DEFAULT 0,
    updated         INTEGER DEFAULT 0,
    no_data         INTEGER DEFAULT 0,
    source_error    INTEGER DEFAULT 0,
    rate_limited    INTEGER DEFAULT 0,
    circuit_skip    INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    message         VARCHAR,
    stats_json      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, source, market, operation)
);

-- ============================================
-- 11. 全局资金流水表
-- ============================================
CREATE TABLE IF NOT EXISTS account_cashflows (
    flow_id         VARCHAR NOT NULL,
    flow_date       DATE NOT NULL,
    account_id      VARCHAR NOT NULL DEFAULT 'default',
    currency        VARCHAR NOT NULL DEFAULT 'CNY',
    flow_type       VARCHAR NOT NULL,        -- DEPOSIT / WITHDRAW
    amount          DOUBLE NOT NULL,
    note            VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (flow_id)
);

-- ============================================
-- 12. 全局账户每日状态表
-- ============================================
CREATE TABLE IF NOT EXISTS account_daily (
    account_id      VARCHAR NOT NULL DEFAULT 'default',
    trade_date      DATE NOT NULL,
    cash            DOUBLE,
    position_value  DOUBLE,
    total_value     DOUBLE,
    net_contribution DOUBLE,
    daily_external_flow DOUBLE DEFAULT 0,
    nav             DOUBLE,
    daily_return    DOUBLE,
    drawdown        DOUBLE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, trade_date)
);

-- ============================================
-- 13. 阶段性绩效评估表
-- ============================================
CREATE TABLE IF NOT EXISTS performance_reviews (
    review_id       VARCHAR NOT NULL,
    account_id      VARCHAR NOT NULL DEFAULT 'default',
    period_type     VARCHAR NOT NULL,        -- WEEKLY / MONTHLY
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    nav_return      DOUBLE,
    total_value_change DOUBLE,
    net_flow        DOUBLE,
    benchmark_code  VARCHAR,
    benchmark_return DOUBLE,
    excess_return   DOUBLE,
    max_drawdown    DOUBLE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (review_id)
);

-- ============================================
-- 14. 指数基金基本信息表
-- ============================================
-- F1: fund_info 扩展候选池字段(在原 CREATE 后通过 ALTER 补)
-- 字段:scale_yi(总规模亿) / etf_subcategory(broad/sector/qdii/commodity/...) /
--      establish_date / fee_rate / manager / data_source
CREATE TABLE IF NOT EXISTS fund_info (
    fund_code       VARCHAR NOT NULL,        -- 基金/ETF代码
    name            VARCHAR,
    fund_type       VARCHAR,                 -- ETF / OPEN
    tracking_index  VARCHAR,                 -- 跟踪指数代码，如 000300
    market          VARCHAR DEFAULT 'CN',
    currency        VARCHAR DEFAULT 'CNY',
    enabled         BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fund_code)
);

-- ============================================
-- 15. 指数基金净值/行情表
-- ============================================
CREATE TABLE IF NOT EXISTS fund_nav (
    fund_code        VARCHAR NOT NULL,
    trade_date       DATE NOT NULL,
    nav              DOUBLE,                 -- 开放式基金单位净值
    close            DOUBLE,                 -- ETF收盘价；开放式基金可等于 nav
    premium_discount DOUBLE,                 -- ETF溢价率/折价率，可为空
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fund_code, trade_date)
);

-- ============================================
-- 16. 指数基金持仓快照表
-- ============================================
CREATE TABLE IF NOT EXISTS index_fund_snapshots (
    snapshot_id      VARCHAR NOT NULL,
    snapshot_date    DATE NOT NULL,
    fund_code        VARCHAR NOT NULL,
    shares           DOUBLE NOT NULL DEFAULT 0,
    cost_amount      DOUBLE NOT NULL DEFAULT 0,
    note             VARCHAR,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_id)
);

-- ============================================
-- 17. 指数基金独立信号表
-- ============================================
CREATE TABLE IF NOT EXISTS index_fund_signals (
    signal_id        VARCHAR NOT NULL,
    fund_code        VARCHAR NOT NULL,
    index_code       VARCHAR NOT NULL,
    signal_date      DATE NOT NULL,
    action           VARCHAR NOT NULL,       -- BUY / ADD / HOLD / REDUCE / PAUSE
    target_weight    DOUBLE,
    confidence       DOUBLE,
    thesis           VARCHAR,
    risk_tags        VARCHAR[],
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id)
);

-- ============================================
-- 18. Core-Satellite 统一资金分配计划表
-- ============================================
CREATE TABLE IF NOT EXISTS allocation_plans (
    plan_id              VARCHAR NOT NULL,
    plan_date            DATE NOT NULL,
    account_id           VARCHAR NOT NULL DEFAULT 'default',
    total_value          DOUBLE,
    cash                 DOUBLE,
    core_target_pct      DOUBLE,
    satellite_target_pct DOUBLE,
    cash_target_pct      DOUBLE DEFAULT 0,
    core_value           DOUBLE,
    satellite_value      DOUBLE,
    core_budget          DOUBLE,
    satellite_budget     DOUBLE,
    core_drift_pct       DOUBLE,
    satellite_drift_pct  DOUBLE,
    status               VARCHAR DEFAULT 'ACTIVE',
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (plan_id)
);

-- ============================================
-- 19. Core-Satellite 分配计划明细表
-- ============================================
CREATE TABLE IF NOT EXISTS allocation_plan_items (
    plan_id          VARCHAR NOT NULL,
    sleeve           VARCHAR NOT NULL,       -- core / satellite
    instrument_type  VARCHAR NOT NULL,       -- sleeve / index_fund / stock_strategy
    instrument_id    VARCHAR NOT NULL,
    action           VARCHAR NOT NULL,       -- BUY / ADD / HOLD / REDUCE / PAUSE
    current_value    DOUBLE,
    target_value     DOUBLE,
    budget_delta     DOUBLE,
    execution_mode   VARCHAR DEFAULT 'ADVISORY', -- ADVISORY / BUDGET / MANUAL
    expected_cash    DOUBLE DEFAULT 0,           -- 手动执行时预计操作金额，始终为正
    cash_effect      DOUBLE DEFAULT 0,           -- 买入为负，赎回/减仓为正
    budget_consumption DOUBLE DEFAULT 0,         -- 消耗的 sleeve 预算，减仓/暂停为 0
    priority         INTEGER,
    reason           VARCHAR,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (plan_id, sleeve, instrument_type, instrument_id, action)
);

-- ============================================
-- 20. Dashboard V2 安全写入审计表
-- ============================================
CREATE TABLE IF NOT EXISTS dashboard_audit_log (
    audit_id        VARCHAR NOT NULL,
    action          VARCHAR NOT NULL,
    payload_json    TEXT,
    status          VARCHAR NOT NULL,        -- ok / failed / rejected
    result_json     TEXT,
    error_message   VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (audit_id)
);

-- ============================================
-- 16. 虚拟账户竞赛（多账户并行对标 → 选最优指导实盘）
-- ============================================
-- 每个虚拟账户 = 一套完整可部署配置（模型组合 + 套利门槛 + 配比 + 风险档），
-- 拥有隔离的现金/持仓/订单/NAV，与其它账户在相同行情下并行对标。
CREATE TABLE IF NOT EXISTS virtual_accounts (
    account_id        VARCHAR NOT NULL,
    name              VARCHAR NOT NULL,
    description       VARCHAR,
    initial_capital   DOUBLE NOT NULL DEFAULT 1000000,
    market            VARCHAR NOT NULL DEFAULT 'CN',
    config_json       TEXT NOT NULL,           -- AccountConfig：模型集/套利门槛/配比/风险档
    status            VARCHAR NOT NULL DEFAULT 'ACTIVE', -- ACTIVE / PAUSED / PROMOTED / ARCHIVED
    is_real_candidate BOOLEAN DEFAULT FALSE,    -- 是否已选为实盘指导候选
    inception_date    DATE,                    -- 账户起始日（回放/纸盘起点）
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id)
);

-- 账户级订单（隔离现金会话执行结果，含历史回放与前向纸盘）
CREATE TABLE IF NOT EXISTS account_orders (
    account_id      VARCHAR NOT NULL,
    order_id        VARCHAR NOT NULL,
    signal_id       VARCHAR,
    symbol          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,        -- BUY / SELL
    order_qty       DOUBLE,
    order_price     DOUBLE,
    order_value     DOUBLE,
    fee             DOUBLE,
    cash_before     DOUBLE,
    cash_after      DOUBLE,
    order_ts        TIMESTAMP NOT NULL,
    source          VARCHAR DEFAULT 'forward', -- forward（前向纸盘）/ replay（历史回放预热）
    status          VARCHAR DEFAULT 'FILLED',
    status_reason   VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, order_id)
);

-- 账户级每日持仓
CREATE TABLE IF NOT EXISTS account_positions (
    account_id      VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    symbol          VARCHAR NOT NULL,
    quantity        DOUBLE NOT NULL DEFAULT 0,
    avg_cost        DOUBLE,
    current_price   DOUBLE,
    market_value    DOUBLE,
    pnl             DOUBLE,
    pnl_pct         DOUBLE,
    weight          DOUBLE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, trade_date, symbol)
);

-- 账户级净值
CREATE TABLE IF NOT EXISTS account_nav (
    account_id      VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    nav             DOUBLE,                  -- 净值（初始=1.0）
    daily_return    DOUBLE,
    cash            DOUBLE,
    position_value  DOUBLE,
    total_value     DOUBLE,
    drawdown        DOUBLE,
    benchmark_nav   DOUBLE,                  -- 同期基准净值（对标用）
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, trade_date)
);

-- 账户级套利决策（去重只在账户内，与全局 signal_decisions 隔离）
CREATE TABLE IF NOT EXISTS account_decisions (
    account_id      VARCHAR NOT NULL,
    decision_id     VARCHAR NOT NULL,
    signal_id       VARCHAR NOT NULL,
    decision_date   DATE NOT NULL,
    model_name      VARCHAR,
    symbol          VARCHAR NOT NULL,
    side            VARCHAR,
    decision        VARCHAR NOT NULL,        -- ACCEPTED / REJECTED
    decision_reason VARCHAR,
    consensus_status VARCHAR,
    priority_score  DOUBLE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, decision_id)
);

-- 账户级绩效快照（竞赛榜）
CREATE TABLE IF NOT EXISTS account_performance (
    account_id        VARCHAR NOT NULL,
    as_of_date        DATE NOT NULL,
    window_label      VARCHAR NOT NULL,      -- live / replay / combined
    sample_days       INTEGER,
    annual_return     DOUBLE,
    cumulative_return DOUBLE,
    annual_volatility DOUBLE,
    sharpe_ratio      DOUBLE,
    max_drawdown      DOUBLE,
    turnover          DOUBLE,
    hit_rate          DOUBLE,
    benchmark_return  DOUBLE,
    excess_return     DOUBLE,
    info_ratio        DOUBLE,
    ready_outcomes    INTEGER,               -- 已结算 outcome 样本量（给晋级闸门用）
    metrics_json      TEXT,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, as_of_date, window_label)
);

-- ============================================
-- 17. 市场估值历史（全A股 PE/PB 中位数 + 历史分位，判断"市场贵不贵"）
-- ============================================
CREATE TABLE IF NOT EXISTS market_valuation (
    trade_date      DATE NOT NULL,
    pe_ttm_median   DOUBLE,                  -- 全A股 PE-TTM 中位数
    pe_ttm_pct_10y  DOUBLE,                  -- PE-TTM 近10年分位 (0-1)
    pe_ttm_pct_all  DOUBLE,                  -- PE-TTM 全历史分位 (0-1)
    pb_median       DOUBLE,                  -- 全A股 PB 中位数
    pb_pct_10y      DOUBLE,                  -- PB 近10年分位 (0-1)
    pb_pct_all      DOUBLE,                  -- PB 全历史分位 (0-1)
    close           DOUBLE,                  -- 全A等权参考点位
    source          VARCHAR DEFAULT 'akshare_lg',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date)
);

-- ============================================
-- 18. 市场状态（阶段+宽度+热度+分化+相对强弱+估值的综合体检）
-- ============================================
CREATE TABLE IF NOT EXISTS market_state (
    trade_date          DATE NOT NULL,
    benchmark           VARCHAR NOT NULL DEFAULT '000300',
    stage               VARCHAR,             -- 强势上升/温和上升/震荡/弱势整理/下跌/危机
    stage_score         DOUBLE,              -- 趋势分 -100~100
    heat_score          DOUBLE,              -- 市场热度分 0-100
    breadth_above_ma50  DOUBLE,              -- %站上MA50
    breadth_above_ma200 DOUBLE,              -- %站上MA200
    advance_ratio       DOUBLE,              -- 当日上涨家数占比
    new_high_low_ratio  DOUBLE,              -- 60日新高/(新高+新低)
    dispersion          DOUBLE,              -- 横截面日收益分化(年化)
    volume_ratio        DOUBLE,              -- 量能 vs 60日均量
    limit_up_ratio      DOUBLE,              -- 涨停代理(当日涨幅>9.5%占比)
    pe_pct_10y          DOUBLE,              -- 估值分位(PE近10年)
    pb_pct_10y          DOUBLE,              -- 估值分位(PB近10年)
    rs_leader           VARCHAR,             -- 相对强弱领先指数
    rs_json             TEXT,                -- 各指数动量明细
    summary             VARCHAR,             -- 一句专业解读
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, benchmark)
);

-- ============================================
-- 19. 仓位信号（市场状态 → 目标权益仓位 + T+1 增减仓建议）
-- ============================================
CREATE TABLE IF NOT EXISTS market_exposure (
    trade_date          DATE NOT NULL,
    benchmark           VARCHAR NOT NULL DEFAULT '000300',
    stage               VARCHAR,
    base_exposure       DOUBLE,              -- 阶段基准权益仓位 0-1
    valuation_adj       DOUBLE,              -- 估值调整(贵则减/便宜则加)
    breadth_adj         DOUBLE,              -- 宽度调整
    heat_adj            DOUBLE,              -- 热度调整(过热则减)
    target_exposure     DOUBLE,              -- 目标权益仓位 0-1
    current_exposure    DOUBLE,              -- 参考当前仓位(可空)
    action              VARCHAR,             -- ADD / REDUCE / HOLD
    advice              VARCHAR,             -- 一句中文建议
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, benchmark)
);

-- ============================================
-- 20. 指数搭配（权益预算内按相对强弱分配到各指数基金）
-- ============================================
CREATE TABLE IF NOT EXISTS index_allocation (
    trade_date      DATE NOT NULL,
    fund_code       VARCHAR NOT NULL,
    index_code      VARCHAR,
    index_name      VARCHAR,
    rs_score        DOUBLE,              -- 相对强弱(动量)分
    rs_rank         INTEGER,             -- 强弱排名(1=最强)
    weight          DOUBLE,              -- 占总资产的目标权重
    equity_budget   DOUBLE,              -- 当期权益预算(来自 market_exposure)
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, fund_code)
);

-- ============================================
-- 21. scheduler_runs: launchctl watchdog 每次终态转换的结构化历史
-- C2: 取代 scheduler_state.json + cron.log/open_trade.log 文本解析
-- ============================================
CREATE TABLE IF NOT EXISTS scheduler_runs (
    run_id          VARCHAR PRIMARY KEY,   -- 形如 daily_close-2026-05-29-200000
    job_key         VARCHAR NOT NULL,      -- daily_close / open_paper_trade
    job_label       VARCHAR,
    scheduled_for   TIMESTAMP,             -- 当日计划执行时刻
    started_at      TIMESTAMP,             -- 实际启动时刻
    ended_at        TIMESTAMP,             -- 终态时刻
    duration_seconds DOUBLE,
    status          VARCHAR NOT NULL,      -- SUCCEEDED / FAILED / MISSED / DEGRADED
    exit_code       INTEGER,
    result          VARCHAR,               -- watchdog 文本结果
    log_path        VARCHAR,
    source          VARCHAR DEFAULT 'watchdog',
    schedule_alignment VARCHAR,            -- 计划偏移人话(早 / 准点 / 晚)
    schedule_note   VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 22. fund_evaluations: 基金每日多维评估快照
-- D1: 整合 snapshot + nav + index + signal + M4 + macro 给出可执行建议
-- ============================================
CREATE TABLE IF NOT EXISTS fund_evaluations (
    eval_date           DATE NOT NULL,
    fund_code           VARCHAR NOT NULL,
    fund_name           VARCHAR,
    tracking_index      VARCHAR,
    tracking_index_name VARCHAR,
    -- E1: 分类与意图
    category            VARCHAR DEFAULT 'equity_index',
    intent              VARCHAR DEFAULT 'active',
    -- 快照(用户录入)
    snapshot_date       DATE,
    snapshot_stale_days INTEGER,
    shares              DOUBLE,
    cost_amount         DOUBLE,
    -- E2: 从 snapshot.note JSON 升格的 broker 真值
    broker_market_value DOUBLE,
    broker_latest_nav   DOUBLE,
    broker_cost_price   DOUBLE,
    broker_holding_pnl  DOUBLE,
    broker_holding_return_pct DOUBLE,
    broker_day_return_pct DOUBLE,
    broker_yesterday_pnl DOUBLE,
    holding_days        INTEGER,
    snapshot_source     VARCHAR,
    snapshot_captured_at VARCHAR,
    market_value_vs_computed_pct DOUBLE,
    -- 净值
    nav                 DOUBLE,
    nav_date            DATE,
    nav_stale_days      INTEGER,
    current_value       DOUBLE,
    return_amount       DOUBLE,
    return_pct          DOUBLE,
    -- 估值/趋势(沿用 index_fund_signals 的派生)
    price_pct           DOUBLE,
    ma_fast             DOUBLE,
    ma_slow             DOUBLE,
    trend_healthy       BOOLEAN,
    trend_weak          BOOLEAN,
    -- 权重决策
    target_weight_m4    DOUBLE,
    equity_exposure     DOUBLE,         -- 宏观目标权益仓位
    target_value        DOUBLE,         -- account_total * equity_exposure * target_weight_m4
    target_account_weight DOUBLE,       -- target_value / account_total
    current_weight      DOUBLE,         -- 在 Core 池中的权重 (与 signal 口径一致)
    current_account_weight DOUBLE,      -- current_value / account_total
    drift_pct           DOUBLE,
    delta_amount        DOUBLE,         -- 应加(+)/减(-)金额
    delta_shares        DOUBLE,         -- delta_amount / nav
    -- 信号
    action              VARCHAR,
    confidence          DOUBLE,
    thesis              VARCHAR,
    risk_tags           VARCHAR[],
    -- 来源
    account_total_value DOUBLE,
    source              VARCHAR DEFAULT 'fund_evaluator_v1',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (eval_date, fund_code)
);

-- ============================================
-- 23. fund_screening_results: 基金每日多维评分扫描结果
-- F2: 估值/趋势/动量/风险/流动性/宏观契合 六维 + signal_tag
-- ============================================
CREATE TABLE IF NOT EXISTS fund_screening_results (
    eval_date           DATE NOT NULL,
    fund_code           VARCHAR NOT NULL,
    fund_name           VARCHAR,
    etf_subcategory     VARCHAR,
    tracking_index      VARCHAR,
    scale_yi            DOUBLE,
    -- 六维子分(0-100)
    valuation_score     DOUBLE,
    trend_score         DOUBLE,
    momentum_score      DOUBLE,
    risk_score          DOUBLE,
    liquidity_score     DOUBLE,
    macro_score         DOUBLE,
    total_score         DOUBLE,
    -- 关键中间量,前端展示
    price_pct           DOUBLE,           -- 价格 / nav 分位
    ma120_above         BOOLEAN,
    ma250_above         BOOLEAN,
    return_1m           DOUBLE,
    return_3m           DOUBLE,
    return_6m           DOUBLE,
    excess_return_3m    DOUBLE,           -- vs 基准
    volatility_20d      DOUBLE,           -- 年化
    max_drawdown_120d   DOUBLE,
    sharpe_1y           DOUBLE,
    nav_history_days    INTEGER,
    -- 信号
    signal_tag          VARCHAR,          -- in_window / watch_high_value / avoid / insufficient_data
    thesis              VARCHAR,
    risk_tags           VARCHAR[],
    -- 来源
    macro_stage         VARCHAR,
    benchmark_code      VARCHAR,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (eval_date, fund_code)
);

-- ============================================
-- 24. fund_holding_alerts: 持仓基金每日风险告警
-- F3: 严格口径 — 亏损 > 5% / 跌破 MA60 / 10d 回撤 > 8% 等
-- ============================================
CREATE TABLE IF NOT EXISTS fund_holding_alerts (
    eval_date           DATE NOT NULL,
    fund_code           VARCHAR NOT NULL,
    alert_type          VARCHAR NOT NULL,   -- stop_loss / ma60_break / drawdown_10d / trend_weak / target_drift / add_window_open
    alert_level         VARCHAR NOT NULL,   -- info / warning / critical
    metric_name         VARCHAR,
    metric_value        DOUBLE,
    threshold           DOUBLE,
    suggested_action    VARCHAR,            -- hold / reduce_partial / exit_stop_loss / add_window_open / monitor
    headline            VARCHAR,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (eval_date, fund_code, alert_type)
);

-- ============================================
-- 25. fund_rebalance_plan: G5 月度/触发式再平衡执行单
-- ============================================
CREATE TABLE IF NOT EXISTS fund_rebalance_plan (
    plan_id           VARCHAR PRIMARY KEY,
    plan_date         DATE NOT NULL,
    trigger_type      VARCHAR,      -- monthly / drift_threshold / manual
    trigger_reason    VARCHAR,
    account_total     DOUBLE,
    equity_exposure   DOUBLE,
    headline          VARCHAR,
    total_actions     INTEGER,
    total_buy_amount  DOUBLE,
    total_sell_amount DOUBLE,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fund_rebalance_action (
    plan_id           VARCHAR NOT NULL,
    fund_code         VARCHAR NOT NULL,
    fund_name         VARCHAR,
    action            VARCHAR NOT NULL,  -- BUY / SELL / HOLD
    amount            DOUBLE,            -- 元,正数
    estimated_units   DOUBLE,            -- 大约份额
    nav               DOUBLE,
    current_value     DOUBLE,
    target_value      DOUBLE,
    drift_pct         DOUBLE,
    priority          INTEGER,
    reason            VARCHAR,
    rank              INTEGER,
    constraint_tags   VARCHAR[],         -- min_amount / no_action_needed 等
    PRIMARY KEY (plan_id, fund_code)
);

-- ============================================
-- 26. earnings_calendar: R1 财报披露日历(未来 30 天 + 过去 7 天)
-- ============================================
CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol           VARCHAR NOT NULL,
    report_period    DATE NOT NULL,             -- YYYY-03/06/09/12-末日
    disclosure_date  DATE NOT NULL,
    disclosure_type  VARCHAR NOT NULL,           -- forecast / express / annual / quarterly
    status           VARCHAR DEFAULT 'EXPECTED', -- EXPECTED / DISCLOSED
    universe         VARCHAR,                    -- CSI300 / CSI1000 / HSTECH
    source           VARCHAR DEFAULT 'akshare',
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, report_period, disclosure_type)
);

-- ============================================
-- 27. earnings_alerts: R1 业绩预告/快报/主财报 事件流 + 派生分析
-- ============================================
CREATE TABLE IF NOT EXISTS earnings_alerts (
    alert_id          VARCHAR NOT NULL,
    symbol            VARCHAR NOT NULL,
    report_period     DATE NOT NULL,
    event_type        VARCHAR NOT NULL,          -- FORECAST/EXPRESS/ANNUAL/QUARTERLY
    event_date        DATE NOT NULL,
    forecast_text     VARCHAR,                   -- 预增 / 预减 / 扭亏 / 续亏
    np_change_min     DOUBLE,                    -- 净利同比下限 %
    np_change_max     DOUBLE,
    np_change_mid     DOUBLE,
    revenue_yoy       DOUBLE,
    revenue_qoq       DOUBLE,
    np_yoy            DOUBLE,
    np_qoq            DOUBLE,
    surprise_pct      DOUBLE,                    -- 实际 vs 预告中值
    industry_rank_pct DOUBLE,                    -- 同行业内净利同比分位
    cf_to_np_ratio    DOUBLE,                    -- 经营现金流 / 净利润 质量比
    sentiment         VARCHAR NOT NULL,          -- POSITIVE / NEUTRAL / NEGATIVE
    impact_score      DOUBLE,                    -- -1..+1 综合冲击分
    headline          VARCHAR,
    risk_tags         VARCHAR[],
    post_event_return_1d  DOUBLE,
    post_event_return_5d  DOUBLE,
    source            VARCHAR DEFAULT 'akshare',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alert_id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_alerts_symbol_date
    ON earnings_alerts (symbol, event_date);
