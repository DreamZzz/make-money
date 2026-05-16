# Value-Quality Validation 2022-2025

Date: 2026-05-16

## Setup

- Strategy: `value_quality`
- Window: 2022-01-01 to 2025-12-31
- Rebalance: monthly
- Holding days: 20
- Top-N: 20
- Financial reporting lag: 60 calendar days
- Execution model: T signal, T+1 open entry, 20-trading-day open exit, CN open-tradability guards, commission + stamp duty costs
- Benchmark: `MIXED_EQUAL`
- Alpha158 reference: `QLIB-WALK_FORWARD-20260514221005-AD82EC`
- Saved backtest run: `BT-20260516152109-4094BE`

## Result

| Metric | Value |
|---|---:|
| Score rows | 33,305 |
| Score dates | 48 |
| Avg factor coverage | 50.3% |
| Return periods | 48 |
| Annual return | -4.38% |
| Cumulative return | -16.40% |
| Annual volatility | 28.83% |
| Sharpe | -0.26 |
| Max drawdown | -40.67% |
| Benchmark annual return | 4.37% |
| Excess return | -8.75 pp |
| Information ratio | -0.52 |
| Turnover | 177.3% annualized |
| Correlation vs Alpha158 | 0.69 |
| Correlation vs benchmark | 0.90 |

## Judgment

This prototype is not ready to become a production alpha. The standalone 2022-2025 result is negative, it underperforms the mixed benchmark by about 8.75 percentage points annualized, and its realized return stream is highly benchmark-correlated. The Alpha158 correlation is also too high for a useful diversifying sleeve.

The likely cause is not just "value-quality does not work"; the current factor input coverage is still thin. Free AkShare financials now cover the priced research universe, but historical PE/PB and market-cap coverage remain sparse, so the score is mostly a quality-plus-partial-valuation prototype rather than a robust value-quality model.

## Next Actions

- Keep `value_quality` research-only.
- Do not wire it into `signals.generate_all()` or paper trading.
- Improve historical valuation and size coverage before another promotion attempt.
- Re-test with sector-neutral ranking and lower turnover constraints after valuation coverage improves.
