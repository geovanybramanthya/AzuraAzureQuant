# V26 Ablation Results - Single-Change Variants from V25

Date: 2026-10-20. Method: each variant = byte-copy of V25 + ONLY the listed change (builder asserted every replacement pattern matched exactly once; all variants pass py_compile and freqtrade list-strategies OK). Identical backtest settings for every run: config user_data/config_futures.live.json, timerange 20240101-20260925, --export trades --cache none. All 7 runs finished rc=0 in ~21s each.

Integrity: V25 SHA256 before AND after all work = 01B983ADFA3D18854DB9403327C19872584FCC1105A25AB5FF7D9174689D7351 (unchanged). V26 v1 file untouched (SHA256 0D866C65AA019397028E30F352C14F6600D602F5AA7FDE0FEE010DE8603FD52D).

Caveat: absolute profit is path-dependent under compounding (stake sizes follow the wallet path), so cross-variant profit gaps overstate/understate edge changes. WR, PF and max drawdown are the robust signals; per-engine WR/PF likewise.

## Headline comparison

| Variant | Change isolated | Trades | WR % | PF | Profit USDT | Max DD % |
|---|---|---:|---:|---:|---:|---:|
| Baseline V25 | - | 1044 | 73.47 | 1.85 | +97,342 | 9.23 |
| AB_F2 | Engine 1 stoch: crossed_above -> stoch_regime_long window | 1197 | 72.01 | 1.52 | +44,254 | 15.90 |
| AB_F2b | F2 + slowk cap 54 -> 45 | 1163 | 72.06 | 1.51 | +38,473 | 15.90 |
| AB_F1 | Soft regime gates (neutral zone) E1 + E4A + E4B | 1351 | 70.61 | 1.35 | +74,607 | 20.68 |
| AB_F3 | Engine 4B: drop (close<ema_50) and (slowk<slowd) | 1069 | 73.25 | 1.79 | +98,740 | 10.03 |
| AB_F3b | Breakdown entry price: min(proposed, close) | 1051 | 63.84 | 1.16 | +4,037 | 47.90 |
| AB_F4 | Exit reworks: tight_stop + rapid_invalidation + prunes | 1043 | 73.63 | 1.85 | +102,735 | 11.66 |
| AB_F4c | Only prune thresholds 18h -2.20% / 24h -1.80% | 1047 | 72.87 | 1.85 | +94,420 | 10.61 |

## Per-engine detail (enter_tag: trades / WR % / PF / profit USDT)

Baseline reference: long_pullback_alpha 514 / 74.3 / ~1.52 / +34.8k; short_atr_fade_alpha 70 / 88.6 / ~2.1 / +6.4k; short_breakdown_alpha ~280 / 72.5 / ~2.40 / +39.5k.

| Variant | long_pullback_alpha | short_atr_fade_alpha | short_breakdown_alpha |
|---|---|---|---|
| AB_F2 | 672 / 71.3 / 1.16 / +9,564 | 70 / 88.6 / 2.19 / +3,517 | 280 / 72.5 / 2.43 / +21,631 |
| AB_F2b | 629 / 71.1 / 1.14 / +7,171 | 70 / 88.6 / 2.25 / +3,295 | 280 / 72.5 / 2.41 / +19,102 |
| AB_F1 | 721 / 70.7 / 0.99 / -1,895 | 94 / 84.0 / 1.60 / +5,428 | 363 / 68.9 / 2.20 / +50,397 |
| AB_F3 | 514 / 74.3 / 1.52 / +35,637 | 70 / 88.6 / 2.12 / +6,377 | 305 / 71.8 / 2.07 / +39,111 |
| AB_F3b | 514 / 74.3 / 1.55 / +5,303 | 70 / 88.6 / 4.45 / +1,907 | 287 / 37.3 / 0.42 / -7,167 |
| AB_F4 | 517 / 73.5 / 1.56 / +38,006 | 70 / 85.7 / 1.50 / +3,876 | 278 / 75.2 / 2.52 / +44,562 |
| AB_F4c | 517 / 73.5 / 1.57 / +36,079 | 70 / 85.7 / 1.55 / +3,922 | 280 / 72.5 / 2.40 / +38,039 |

Note on AB_F3b per-tag profits outside breakdown (e.g. pullback +5,303 at unchanged WR/PF): pure compounding path effect - the breakdown drawdown shrinks the wallet and therefore every later stake. WR/PF per tag confirm the untouched engines did not change.

## Exit-reason detail for F4 / F4c (exits / profit USDT)

| Exit reason | Baseline V25 | AB_F4 | AB_F4c |
|---|---:|---:|---:|
| adaptive_stale_prune_18h | 67 / -41,446 | 82 / -47,692 | 82 / -44,303 |
| adaptive_stale_prune_24h | 23 / -17,474 | 17 / -16,822 | 17 / -15,805 |
| short_breakdown_tight_stop | 51 / -21,631 | 25 / -16,903 | 51 / -20,840 |
| breakout_rapid_invalidation_4h | 51 / -12,805 | 48 / -14,379 | 51 / -12,325 |
| stop_loss | n/a | 3 / -2,152 | 3 / -2,022 |
| roi | n/a | 246 / +71,541 | 245 / +66,786 |

## Verdicts

- AB_F2 (stoch window): HURTS. Pullback trades +31% (514 -> 672) at lower quality: WR 74.3 -> 71.3%, PF ~1.52 -> 1.16, engine profit collapses +34.8k -> +9.6k. Portfolio PF 1.85 -> 1.52, DD 9.23 -> 15.90%. The 3-bar window fires late/weak pullbacks the single-candle cross filtered out. Reject.
- AB_F2b (F2 + slowk cap 45): HURTS. The quality guard trims only 43 of the extra trades; pullback PF stays 1.14, portfolio PF 1.51. The cap does not rescue the window change. Reject.
- AB_F1 (soft regime gates): HURTS. +307 trades; the neutral-zone pullbacks push Engine 1 to PF 0.99 (net negative, -1,895); neutral breakdowns add volume at PF 2.20 but the stale-prune channel explodes (116 exits, -102,781 at 18h alone). Portfolio PF 1.35, DD 20.68%. The dead zone was dead for a reason: no edge there. Reject.
- AB_F3 (4B condition removal): NEUTRAL, slightly negative. +25 breakdown trades at PF 2.07 vs ~2.40 baseline - more, weaker signals; total profit +98,740 is within path noise of baseline, but PF slips to 1.79 and DD rises to 10.03%, and tight_stop losses grow (-29,509 over 57 exits). No benefit. Reject (keep the two conditions).
- AB_F3b (breakdown entry at market): HURTS BADLY - the single most destructive change tested. Breakdown WR collapses 72.5 -> 37.3%, PF 2.40 -> 0.42, portfolio DD 47.90%, total profit +4,037. Resting the limit above market acted as a fill-quality filter (fills only on a retest/bounce); filling at market takes every falling knife mid-plunge. Reject unconditionally.
- AB_F4 (exit rework package): NEUTRAL / MIXED. Headline PF and WR identical to baseline (1.85, 73.6%); the +5,393 profit gap is path-dependent, and DD worsens 9.23 -> 11.66%. Inside the package: the tight_stop rework genuinely helps its channel (exits 51 -> 25, losses -21,631 -> -16,903, and breakdown engine WR/PF rise to 75.2% / 2.52), but the rapid-invalidation rework is slightly worse (-14,379 vs -12,805) and the prune tightening adds losses (see F4c). Net wash with worse DD.
- AB_F4c (prune thresholds only): NEUTRAL, slightly negative. PF flat at 1.85, profit -2,922 vs baseline, DD 10.61%. Prune_18h fires more often (67 -> 82) and loses MORE in total (-44,303 vs -41,446): cutting at -2.20% instead of -2.70% exits trades that would have recovered; average loss per exit falls (-540 vs -619) but not enough. Prune_24h mildly better (17 exits, -15,805 vs 23 / -17,474). Net: do not tighten the prunes.

## Synthesis

The V26 v1 collapse (PF 0.99) is fully explained by stacking three independently bad entry-side changes: F3b (breakdown fill destruction, DD 47.9% solo), F1 (neutral-zone entry flood) and F2 (pullback quality dilution), with F3 adding weaker breakdown volume on top. No single change improves portfolio PF; the only component with a positive channel-level signal is the F4a tight_stop rework inside AB_F4 (fewer, later tight stops; breakdown WR/PF up), which was not ablated alone - if anything is carried into a V26 v2, it should be F4a alone, re-ablated, with the prune thresholds and rapid-invalidation left at V25 values.

Artifacts: variant files user_data/strategies/ApexDualAlpha_Omni_AB_*.py (7), logs user_data/backtest_results/ApexDualAlpha_Omni_AB_*.log, exports user_data/backtest_results/ApexDualAlpha_Omni_AB_*_export.zip, raw aggregates user_data/backtest_results/ablation_analysis.json, run log user_data/backtest_results/ablation_progress.txt.
