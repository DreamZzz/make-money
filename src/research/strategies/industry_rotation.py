"""
行业轮动策略 — 基于行业动量 + 估值轮动。
每月计算行业收益率和估值分位数，买入强势+低估行业。
"""
import pandas as pd
from loguru import logger


def compute_industry_returns(
    prices: pd.DataFrame,
    industry_map: dict[str, str],
) -> pd.DataFrame:
    """
    计算各行业收益率（等权重）。

    Args:
        prices: 宽表，index=trade_date, columns=symbol, values=close
        industry_map: {symbol: industry_name}
    """
    returns = prices.pct_change(21).dropna(how="all")  # 月收益

    industry_returns = {}
    for sym, ind in industry_map.items():
        if sym not in returns.columns:
            continue
        if ind not in industry_returns:
            industry_returns[ind] = []
        industry_returns[ind].append(returns[sym])

    avg_returns = {}
    for ind, series_list in industry_returns.items():
        combined = pd.concat(series_list, axis=1).mean(axis=1)
        avg_returns[ind] = combined

    return pd.DataFrame(avg_returns)


def generate_rotation_signals(
    prices: pd.DataFrame,
    industry_map: dict[str, str],
    lookback: int = 3,
    top_n_industries: int = 3,
    stocks_per_industry: int = 5,
) -> pd.DataFrame:
    """
    行业轮动信号生成：买入过去 lookback 个月动量最强的 top_n_industries 个行业的前 stocks_per_industry 只股票。

    Args:
        lookback: 回看月数（用于动量排名）
        top_n_industries: 选几个行业
        stocks_per_industry: 每个行业选几只股票
    """
    if prices.empty or not industry_map:
        return pd.DataFrame()

    # 计算行业月收益
    monthly_returns = prices.pct_change(21).dropna(how="all")
    ind_returns = compute_industry_returns(prices, industry_map)

    if ind_returns.empty:
        logger.warning("No industry returns computed")
        return pd.DataFrame()

    # 动量排名：过去 lookback 个月累计收益
    momentum = ind_returns.iloc[-lookback:].sum().sort_values(ascending=False)
    top_industries = momentum.head(top_n_industries).index.tolist()

    # 在每个行业中选 stocks_per_industry 只最强个股
    latest_monthly = monthly_returns.iloc[-lookback:].sum()

    signals = []
    for industry in top_industries:
        stocks_in_ind = [s for s, i in industry_map.items() if i == industry and s in latest_monthly.index]
        if not stocks_in_ind:
            continue
        top_stocks = latest_monthly[stocks_in_ind].nlargest(stocks_per_industry)
        for sym, score in top_stocks.items():
            signals.append({
                "trade_date": prices.index[-1],
                "symbol": sym,
                "side": "BUY",
                "score": score,
                "industry": industry,
                "confidence": min(abs(score) * 5, 0.85),
            })

    if not signals:
        return pd.DataFrame()

    df = pd.DataFrame(signals)
    df["model_name"] = "industry_rotation"
    df["horizon"] = "30d"
    df["expected_holding_days"] = 30
    df["max_position_pct"] = 0.08
    df["thesis"] = df.apply(lambda r: f"行业轮动: {r['industry']} 动量得分 {r['score']:.3f}", axis=1)
    df["risk_tags"] = [["industry", "equity_long"] for _ in range(len(df))]

    return df
