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
-- 3. 指数行情表
-- ============================================
CREATE TABLE IF NOT EXISTS index_daily (
    index_code      VARCHAR NOT NULL,        -- 指数代码: 000300.SH, ^HSI
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
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_id)
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
-- 7. 订单模拟/跟踪表
-- ============================================
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        VARCHAR NOT NULL,        -- 订单ID
    signal_id       VARCHAR,                 -- 关联信号ID
    symbol          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,        -- BUY / SELL
    order_qty       DOUBLE,                   -- 委托数量（股）
    order_price     DOUBLE,                  -- 委托价格
    order_ts        TIMESTAMP NOT NULL,      -- 下单时间
    status          VARCHAR DEFAULT 'PENDING', -- PENDING / FILLED / CANCELLED
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (order_id)
);
