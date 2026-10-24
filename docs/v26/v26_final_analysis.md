# V26 Final Analysis - ApexDualAlpha_Omni_V26_Optimized (v2)

Date: 2026-10-20. Backtest settings identical for every run: freqtrade backtesting --config user_data/config_futures.live.json --strategy <Class> --timerange 20240101-20260925 --export trades --cache none. Wallet 1000 USDT, unlimited compounding stake (absolute profit is a compounding-path artifact; PF / WR / DD are the robust signals).
V2 composition (only): F4a tight-stop rework + PBP pullback-scoped prune_18h -0.0220 + HYPE1 stake 0.70 -> 1.00 + live-only F3c / F5 / F1b (zero backtest effect by design). Changelog: v26_changes_v2.md. Final log: v26_v2_full.log. Export: v26_v2_export.zip.
Baseline export: backtest-result-2026-10-20_06-39-56.zip (V25). STACK-only export: ApexDualAlpha_Omni_AB_STACK_export.zip (round 4, F4a + PBP without HYPE1).

## Headline: V26 v2 vs baseline V25 vs STACK-only

| Variant | Trades | WR % | PF | Profit USDT | Profit % | Max DD % | Max DD abs | Final balance | Long trades / profit | Short trades / profit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V26 v2 (final) | 1043 | 73.92 | 1.93 | 134,505.64 | 134.51 | 9.74 | 12,687.42 | 135,505.64 | 695 / 73,968.23 | 348 / 60,537.40 |
| Baseline V25 | 1044 | 73.47 | 1.85 | 97,342.04 | 97.34 | 9.23 | 8,829.44 | 98,342.04 | 694 / 51,724.36 | 350 / 45,617.68 |
| STACK-only (F4a+PBP) | 1043 | 73.92 | 1.91 | 112,902.45 | 112.90 | 9.42 | 10,225.95 | 113,902.45 | 695 / 59,034.87 | 348 / 53,867.58 |

Long/short detail (recomputed from trades) - V2: long 695 trades, WR 71.94%, PF 1.74, profit 73,968.23; short 348 trades, WR 77.87%, PF 2.36, profit 60,537.40. Baseline: long 694 / WR 72.33% / PF 1.64 / 51,724.36; short 350 / WR 75.71% / PF 2.35 / 45,617.68.

STACK-only vs final isolates the HYPE1 contribution: STACK is F4a + PBP (the two exit-side winners); the final adds only HYPE1 sizing on top (the three live-only fixes have zero backtest effect by design, so final-vs-STACK differences are HYPE1 plus compounding-path interaction).

## Monthly PnL by close_date (USDT)

| Month | V26 v2 | Baseline V25 | STACK-only |
|---|---:|---:|---:|
| 2024-01 | 0.00 | 0.00 | 0.00 |
| 2024-02 | 160.40 | 159.18 | 160.40 |
| 2024-03 | 164.95 | 147.85 | 164.95 |
| 2024-04 | 227.93 | 276.65 | 227.93 |
| 2024-05 | -21.27 | 3.03 | -21.27 |
| 2024-06 | 278.52 | 288.51 | 278.52 |
| 2024-07 | 349.05 | 359.80 | 349.05 |
| 2024-08 | -57.94 | -58.92 | -57.94 |
| 2024-09 | 510.41 | 442.73 | 510.41 |
| 2024-10 | 84.21 | -88.99 | 84.21 |
| 2024-11 | 2,193.99 | 2,185.88 | 2,193.99 |
| 2024-12 | 1,686.39 | 1,605.55 | 1,665.40 |
| 2025-01 | 2,329.00 | 2,183.54 | 2,322.83 |
| 2025-02 | 1,268.70 | 661.22 | 1,157.17 |
| 2025-03 | 1,564.08 | 1,310.03 | 1,574.88 |
| 2025-04 | 1,553.89 | 1,348.03 | 1,444.42 |
| 2025-05 | 1,451.12 | 1,101.27 | 1,213.50 |
| 2025-06 | 2,861.88 | 2,359.56 | 2,599.98 |
| 2025-07 | 6,542.32 | 5,456.34 | 6,369.65 |
| 2025-08 | 5,547.96 | 5,228.12 | 5,260.93 |
| 2025-09 | 5,412.78 | 4,381.84 | 4,808.59 |
| 2025-10 | 9,671.36 | 6,906.67 | 7,734.47 |
| 2025-11 | 13,857.20 | 10,436.59 | 12,224.33 |
| 2025-12 | 7,600.75 | 6,984.86 | 6,349.81 |
| 2026-01 | -4,779.28 | -2,858.35 | -4,251.84 |
| 2026-02 | 15,579.83 | 12,915.14 | 13,798.37 |
| 2026-03 | 5,651.81 | 4,016.11 | 4,042.16 |
| 2026-04 | 20,828.56 | 16,700.11 | 17,779.66 |
| 2026-05 | 15,601.10 | 8,141.98 | 9,427.64 |
| 2026-06 | 779.05 | -4,795.41 | 511.45 |
| 2026-07 | -2,271.43 | -1,973.88 | -2,433.76 |
| 2026-08 | 4,024.64 | 2,669.55 | 4,453.69 |
| 2026-09 | 13,853.68 | 8,847.41 | 10,958.86 |

Green months: V26 v2 28 of 32; baseline 27 of 32; STACK-only 28 of 32 (2024-01 excluded: zero closed trades in every variant).
Worst month: V26 v2 2026-01 (-4,779.28); baseline 2026-06 (-4,795.41).

## Per-engine (enter_tag) - V26 v2

| Engine | Trades | WR % | PF | Profit USDT |
|---|---:|---:|---:|---:|
| long_pullback_alpha | 515 | 73.79 | 1.66 | 54,301.98 |
| short_breakdown_alpha | 278 | 75.18 | 2.45 | 54,071.41 |
| long_rs_momentum | 107 | 67.29 | 2.32 | 9,950.78 |
| long_squeeze_expansion | 73 | 65.75 | 1.90 | 9,715.48 |
| short_atr_fade_alpha | 70 | 88.57 | 1.90 | 6,465.99 |

Baseline per-engine reference: short_breakdown_alpha 280 / 72.50% / PF 2.40 / 39,540.67; long_pullback_alpha 514 / 74.32% / PF 1.52 / 34,837.38; long_rs_momentum 107 / 67.29% / PF 2.39 / 8,566.49; long_squeeze_expansion 73 / 65.75% / PF 2.11 / 8,320.50; short_atr_fade_alpha 70 / 88.57% / PF 2.10 / 6,077.02.

## Per-exit_reason - V26 v2 (all)

| Exit reason | Exits | WR % | PF | Profit USDT |
|---|---:|---:|---:|---:|
| roi | 249 | 100.00 | inf | 86,839.61 |
| short_breakdown_quick_tp | 176 | 100.00 | inf | 76,165.92 |
| exit_upper_bb_tp | 61 | 100.00 | inf | 34,733.57 |
| hype_quick_tp | 24 | 100.00 | inf | 24,944.21 |
| breakout_expansion_tp | 80 | 100.00 | inf | 20,299.76 |
| exit_lower_bb_tp | 29 | 100.00 | inf | 12,550.83 |
| short_explosive_dump_tp | 38 | 100.00 | inf | 8,616.57 |
| eth_profit_bank_tp | 31 | 100.00 | inf | 7,730.98 |
| paxg_volatility_tp | 80 | 100.00 | inf | 7,237.87 |
| chandelier_atr_trailing_exit | 1 | 100.00 | inf | 175.47 |
| trailing_stop_loss | 2 | 100.00 | inf | 146.38 |
| time_decay_48h_cutoff | 3 | 0.00 | 0.00 | -166.33 |
| breakout_14h_timeout | 9 | 0.00 | 0.00 | -1,033.43 |
| hype_stale_prune_36h | 1 | 0.00 | 0.00 | -1,723.25 |
| stop_loss | 3 | 0.00 | 0.00 | -2,510.31 |
| hype_stale_loss | 3 | 0.00 | 0.00 | -4,038.66 |
| time_decay_stale_loss | 24 | 0.00 | 0.00 | -4,599.88 |
| paxg_early_stale_prune | 17 | 0.00 | 0.00 | -5,878.21 |
| adaptive_stale_prune_24h | 9 | 0.00 | 0.00 | -10,813.51 |
| short_breakdown_stale_prune | 43 | 0.00 | 0.00 | -14,541.76 |
| breakout_rapid_invalidation_4h | 51 | 0.00 | 0.00 | -17,240.24 |
| short_breakdown_tight_stop | 25 | 0.00 | 0.00 | -21,923.43 |
| adaptive_stale_prune_18h | 84 | 0.00 | 0.00 | -60,466.53 |

## Per-pair - V26 v2 (8 pairs)

| Pair | Trades | WR % | PF | Profit USDT |
|---|---:|---:|---:|---:|
| HYPE/USDT:USDT | 88 | 84.09 | 3.15 | 35,595.10 |
| SOL/USDT:USDT | 138 | 73.19 | 2.22 | 21,548.60 |
| ADA/USDT:USDT | 117 | 74.36 | 2.00 | 20,450.04 |
| DOGE/USDT:USDT | 143 | 73.43 | 1.98 | 19,917.07 |
| LINK/USDT:USDT | 189 | 68.78 | 2.04 | 19,625.39 |
| ETH/USDT:USDT | 122 | 77.87 | 1.97 | 14,945.33 |
| BTC/USDT:USDT | 98 | 74.49 | 1.05 | 1,500.16 |
| PAXG/USDT:USDT | 148 | 71.62 | 1.11 | 923.94 |

## Half split by close_date

H1 = 2024-01-09..2025-06-30, H2 = 2025-07-01..2026-09-25.

| Variant | Half | Trades | WR % | PF | Profit USDT |
|---|---|---:|---:|---:|---:|
| V26 v2 | H1 | 618 | 71.84 | 1.90 | 16,605.31 |
| V26 v2 | H2 | 425 | 76.94 | 1.93 | 117,900.33 |
| Baseline V25 | H1 | 618 | 71.36 | 1.84 | 14,284.95 |
| Baseline V25 | H2 | 426 | 76.53 | 1.86 | 83,057.09 |
Unassigned trades outside both halves: V2 0, baseline 0.

## Sensitivity on the final candidate (single-knob +-15% around F4a / PBP)

| Variant | Change vs final | Trades | WR % | PF | Profit USDT | Max DD % |
|---|---|---:|---:|---:|---:|---:|
| FINAL v2 | - | 1043 | 73.92 | 1.93 | 134,505.64 | 9.74 |
| SENS_TS_LO | F4a delayed leg -0.0250 -> -0.0213 (-15%) | 1043 | 73.44 | 1.89 | 117,587.33 | 9.74 |
| SENS_TS_HI | F4a delayed leg -0.0250 -> -0.0288 (+15%) | 1043 | 73.92 | 1.92 | 134,776.38 | 10.01 |
| SENS_PBP_LO | PBP threshold -0.0220 -> -0.0187 (-15%) | 1046 | 73.42 | 1.86 | 134,086.92 | 9.92 |
| SENS_PBP_HI | PBP threshold -0.0220 -> -0.0253 (+15%) | 1043 | 74.02 | 1.90 | 131,688.61 | 9.56 |

Machine-readable detail: v26_final_analysis.json. Sensitivity logs/exports: ApexDualAlpha_Omni_AB_SENS_*.log / *_export.zip.

## Errata (post-audit corrections)

Date: 2026-10-20.

- Green-month denominator corrected 33 -> 32 (2024-01 excluded: zero closed trades in every variant).
- Final strategy file line count corrected 1,052 -> 1,051.
- Line endings: the final strategy file uses CRLF line endings while V25 uses LF (cosmetic only; content diff verified as exactly the 6 intended changes by independent audit in v26_audit_final.md).
