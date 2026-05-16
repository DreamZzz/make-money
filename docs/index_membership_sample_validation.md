# Index Membership Sample Validation

Validation date: 2026-05-16

This note validates the free Baostock monthly historical membership archive against public adjustment announcements. The goal is not to prove every constituent interval, but to check whether the imported `index_member_history` captures known semiannual index changes in the right direction without using paid data sources.

## Data Under Review

- Archive source: `baostock_monthly_snapshot`
- Archive file: `data/index_membership/baostock_csi_history.csv` (local, ignored)
- Imported table: `index_member_history`
- Coverage report: `docs/index_membership_coverage.md`
- Current coverage:
  - `000300`: 514 interval rows, 481 total symbols, 300 active members
  - `000905`: 1107 interval rows, 995 total symbols, 500 active members

## Important Limitation

Baostock is used as a free monthly snapshot source. The resulting intervals are month-end approximations:

- An official addition effective after a mid-month close appears with `start_date` at that month's snapshot date.
- An official removal appears with `end_date` one day before the next snapshot date.

This is acceptable for the six-month validation period as a free point-in-time approximation. It is conservative for backtests around rebalance days, but it is not a tick-perfect or effective-date-perfect constituent calendar.

## Public Sources

1. Shanghai Stock Exchange article dated 2020-11-27: the 2020 regular adjustment became effective on 2020-12-14. It states that CSI 300 added examples including Transsion Holdings and CSSC, removed examples including Ningbo Port and Tianqi Lithium; CSI 500 added examples including Beijing Junzheng and Raytron, and removed examples including Yuexiu Financial Holdings and Angel Yeast.
   - URL: https://www.sse.com.cn/market/sseindex/diclosure/c/c_20201127_5268298.shtml
2. Securities Times article dated 2024-05-31, citing CSI Index: the 2024 mid-year regular adjustment became effective after the 2024-06-14 close. It states that CSI 300 added examples including China Merchants Expressway and COSCO Shipping Energy; CSI 500 added examples including SUPCON Technology and Ezviz Network.
   - URL: https://www.stcn.com/article/detail/1220049.html
3. Securities Times/Sina article dated 2023-11-24, citing CSI Index: the 2023 year-end regular adjustment became effective after the 2023-12-08 close. It states that CSI 300 added examples including Hygon Information and Zhongji Innolight; CSI 500 added examples including CITIC Metal and GalaxyCore.
   - URL: https://finance.sina.com.cn/stock/relnews/us/2023-11-24/doc-imzvtprq4423759.shtml

## Sample Checks

### 2020-12 Adjustment

Official source says the adjustment became effective on 2020-12-14. Baostock month-end intervals should show additions starting around 2020-12-31 and removals ending around 2020-12-30.

| Index | Symbol | Name | Official Direction | Local Interval Result | Result |
|---|---|---|---|---|---|
| `000300` | `688036` | 传音控股 | Added | `2020-12-31` to open | Pass |
| `000300` | `600150` | 中国船舶 | Added | `2020-12-31` to open | Pass |
| `000300` | `601018` | 宁波港 | Removed | `2020-01-31` to `2020-12-30`; later re-entered `2025-12-31` | Pass |
| `000300` | `002466` | 天齐锂业 | Removed | `2020-01-31` to `2020-12-30`; later re-entered `2022-01-31` | Pass |
| `000905` | `300223` | 北京君正 | Added | `2020-12-31` to `2022-06-29`; later re-entered `2024-12-31` | Pass |
| `000905` | `688002` | 睿创微纳 | Added | `2020-12-31` to open | Pass |
| `000905` | `000987` | 越秀资本 / 越秀金控 | Removed | `2020-01-31` to `2020-12-30`; later re-entered `2022-01-31` | Pass |
| `000905` | `600298` | 安琪酵母 | Removed | `2020-01-31` to `2020-12-30`; later re-entered `2022-01-31` | Pass |

### 2023-12 Adjustment

Official source says the adjustment became effective after the 2023-12-08 close. Baostock month-end intervals should show additions around 2023-12-31.

| Index | Symbol | Name | Official Direction | Local Interval Result | Result |
|---|---|---|---|---|---|
| `000300` | `688041` | 海光信息 | Added | `2023-12-31` to open | Pass |
| `000300` | `300308` | 中际旭创 | Added | `2023-12-31` to open | Pass |
| `000905` | `601061` | 中信金属 | Added | `2023-12-31` to `2024-06-29`; later re-entered `2024-12-31` | Pass |
| `000905` | `688728` | 格科微 | Added | `2023-12-31` to `2024-06-29`; later re-entered `2025-06-30` | Pass |

### 2024-06 Adjustment

Official source says the adjustment became effective after the 2024-06-14 close. Baostock month-end intervals should show additions around 2024-06-30.

| Index | Symbol | Name | Official Direction | Local Interval Result | Result |
|---|---|---|---|---|---|
| `000300` | `001965` | 招商公路 | Added | `2024-06-30` to open | Pass |
| `000300` | `600026` | 中远海能 | Added | `2024-06-30` to open | Pass |
| `000905` | `688777` | 中控技术 | Added | `2024-06-30` to open; had an earlier `2022-06-30` to `2023-12-30` interval | Pass |
| `000905` | `688475` | 萤石网络 | Added | `2024-06-30` to open | Pass |

## Verdict

The sampled official adjustment examples match the imported Baostock archive in direction and approximate month-end timing. The archive is good enough for the validation-period goal of removing the worst survivorship bias from multi-year backtests without paid data.

Residual risk remains: exact effective dates are approximated to monthly snapshots. `survivorship_impact_v2.md` must state this limitation when comparing static vs point-in-time universes.
