# P1 Portfolio Exposure Monitor Design

Review date: 2026-05-15
Status: Approved for implementation by ongoing P1 work request

## Goal

Add a portfolio exposure monitor for the satellite stock sleeve so a retail user can see whether current paper holdings are concentrated by industry, market-cap bucket, valuation, or benchmark-relative tilt.

## Scope

This release is observational only. It does not block orders and does not rebalance positions. It should feed the next P1 turnover/risk-rule work with clean exposure calculations.

## Data Sources

- Current stock holdings: latest `paper_positions` rows per strategy with positive quantity.
- Stock metadata: `stock_info.name`, `industry`, `sector`, `market_cap`.
- Valuation: latest available `daily_price.pe_ttm` and `daily_price.pb` per held symbol.
- Benchmark comparison: active `index_member_history` rows for a configurable benchmark index, default `000300`; join to `stock_info`. Benchmark industry weights use `market_cap` when available and fall back to equal weight.

## Output

Create `src.portfolio.exposure_monitor` with pure/testable helpers returning:

- `positions`: aggregate symbol-level holdings across strategies.
- `industry`: portfolio industry weight, benchmark weight, and relative weight.
- `size`: market-cap bucket weights.
- `summary`: concentration and valuation metrics.

Dashboard `组合监控` will render:

- summary metrics: holdings count, top-1 weight, top-5 weight, max industry weight, weighted PE/PB;
- industry exposure table with benchmark-relative tilt;
- size bucket table;
- position-level exposure table for drill-down.

## Rules

- Missing industry becomes `未知行业`.
- Missing market cap becomes `未知市值`.
- Weighted valuation only counts symbols with positive valuation values and reports coverage.
- Empty holdings return empty frames and do not crash the Dashboard.

## Acceptance

- Unit tests cover industry aggregation, benchmark-relative weights, size buckets, valuation coverage, and empty holdings.
- Dashboard page renders without Streamlit exceptions.
- `P1-02` is marked Done when quality checks pass.
