# Research Alpha Validation - 2026-05-22

Scope: research-only validation for macro/industry diversification candidates. None of these candidates is wired into `signals.generate_all()` or paper trading.

Validation window: 2024-01-01 to 2026-05-20  
Portfolio rule: Top 20, monthly rebalance, 20 trading-day holding period  
Benchmark: `MIXED_EQUAL`  
Alpha158 reference: `QLIB-WALK_FORWARD-20260520223710-62A3FC`

## Gate Summary

| Candidate | Buffer | IR | Excess Return | MaxDD | Turnover | Corr vs Alpha158 | Corr vs Benchmark | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| low_vol | none | -1.09 | -28.36pp | -28.07% | 0.44 | 0.53 | 0.58 | FAIL |
| cross_reversal | none | 0.69 | +8.78pp | -15.67% | 11.58 | 0.85 | 0.92 | FAIL |
| industry_relative_momentum | none | -0.27 | -9.65pp | -22.23% | 9.57 | 0.65 | 0.74 | FAIL |
| low_vol | 80 | -1.09 | -28.36pp | -28.07% | 0.44 | 0.53 | 0.58 | FAIL |
| cross_reversal | 80 | 0.50 | +5.72pp | -16.72% | 8.77 | 0.84 | 0.93 | FAIL |
| cross_reversal | 160 | 0.53 | +5.84pp | -18.14% | 5.57 | 0.86 | 0.94 | FAIL |
| industry_relative_momentum | 80 | -0.23 | -8.93pp | -21.19% | 7.36 | 0.71 | 0.81 | FAIL |
| cross_reversal 60d | 160 | 1.31 | +16.07pp | -11.13% | 5.14 | 0.80 | 0.94 | FAIL |
| cross_reversal 120d | 160 | -0.15 | -3.21pp | -9.41% | 3.74 | 0.77 | 0.95 | FAIL |
| cross_reversal 60d top50 | 300 | 0.34 | +3.01pp | -14.15% | 4.55 | 0.79 | 0.93 | FAIL |
| cross_reversal 60d top80 | 500 | 0.32 | +2.56pp | -14.93% | 2.52 | 0.81 | 0.94 | FAIL |
| cross_reversal 60d top20 max2 | 500 | 0.36 | +4.07pp | -12.87% | 1.87 | 0.76 | 0.90 | FAIL |
| cross_reversal 60d top20 max1 | 500 | 0.26 | +2.30pp | -13.01% | 1.50 | 0.76 | 0.91 | FAIL |
| cross_reversal 60d smooth5 max2 | 500 | 0.26 | +3.36pp | -9.62% | 1.88 | 0.64 | 0.85 | FAIL |
| cross_reversal 60d quarterly max1 | 500 | -0.04 | -0.71pp | -6.01% | 0.83 | 0.53 | 0.92 | FAIL |
| cross_reversal 60d smooth5 quarterly max1 | 500 | -0.30 | -2.16pp | -10.64% | 0.83 | 0.25 | 0.84 | FAIL |
| cross_reversal 60d smooth5 size-neutral max1 | 500 | -0.24 | -3.88pp | -12.79% | 1.36 | 0.73 | 0.87 | FAIL |
| cross_reversal 60d smooth5 beta60 max2 | 500 | 0.55 | +5.59pp | -9.21% | 2.06 | 0.79 | 0.93 | FAIL |
| cross_reversal 60d smooth5 beta60 max1 | 500 | 0.48 | +5.54pp | -8.92% | 1.50 | 0.75 | 0.90 | FAIL |
| cross_reversal 60d beta120 max1 | 500 | 0.18 | +1.84pp | -14.29% | 1.55 | 0.73 | 0.89 | FAIL |

## Interpretation

`cross_reversal` is the only candidate with positive excess return and acceptable drawdown, but it fails the retail low-turnover constraint by a wide margin and behaves too much like the current Alpha158/beta stack. Increasing the buffer lowers turnover but does not bring it close to the `<= 1.00` annual turnover gate.

`low_vol` is low-turnover but has negative excess return and a drawdown breach, so it is not a useful sleeve in the current construction.

`industry_relative_momentum` does not improve portfolio economics and remains too correlated with the benchmark.

The 60-day `cross_reversal` variant is economically interesting, with IR 1.31 and +16.07pp excess return in this window. It still fails because it is not an independent, low-turnover sleeve: annual turnover remains 5.14 and correlations are 0.80 vs Alpha158 and 0.94 vs benchmark. Expanding to Top 50/80 and wider buffers reduces turnover, but the strategy becomes lower-alpha while still too correlated.

The专项 follow-up added three research-only levers: `max_replacements_per_rebalance`, score smoothing, and size-neutral residual scoring. The best monthly low-turnover variant (`top20 / buffer500 / max2`) keeps IR above the 0.30 threshold but still fails turnover and correlation gates. Quarterly variants can meet turnover and reduce Alpha158 correlation, but lose IR and remain too benchmark-correlated. Size-neutral residualization removes part of the size exposure, but also removes the return edge.

The second专项 follow-up added beta-neutral residual scoring. The best beta-neutral variant (`smooth5 / beta60 / max2`) improves IR to 0.55 and keeps drawdown low, but does not solve the core problem: annual turnover is still 2.06, Alpha158 correlation is 0.79, and benchmark correlation is 0.93. Tightening replacements to max1 reduces turnover to 1.50 but still fails all correlation gates. Beta residualization therefore improves some portfolio statistics but does not create an independent low-frequency sleeve.

## Decision

Do not promote any candidate. Keep all three research-only.

## Next Research Moves

1. For `cross_reversal`, test a weekly signal with monthly execution and stronger retention logic rather than pure Top-N monthly refresh.
2. Add sector/industry neutralization and beta/size controls before re-testing, because current candidates still carry too much market/beta exposure.
3. Add a `buffer_n` grid to future validation jobs, but keep the promotion gate unchanged: IR >= 0.30, Alpha158 correlation <= 0.50, benchmark correlation <= 0.70, MaxDD >= -25%, annual turnover <= 1.00, coverage >= 80%.
4. Treat `cross_reversal_60d` as the next research target, but do not relax the gate. The right fix is risk residualization and stronger retention, not lowering the turnover/correlation thresholds.
5. Current专项 decision: do not promote. The next serious path is not more Top-N/buffer tuning; it is beta/market residualization or combining reversal only as a small overlay inside the global arbiter after a separate ensemble gate is designed.
6. After beta-neutral testing, residualization alone is not enough. The next iteration should shift from "single-factor promotion" to "overlay ensemble": use `cross_reversal_60d` only as a secondary vote when Alpha158 already accepts the name, with a separate ensemble gate and position-size cap.
