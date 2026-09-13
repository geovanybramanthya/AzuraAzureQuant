from simulate_candidate_engines import pair_data, evaluate_signals

# Test Range Mean-Reversion Bounce in Sideways / Consolidation
range_bounce_sigs = {}
for p, df in pair_data.items():
    cross_cur = (df['slowk'].shift(1) <= df['slowd'].shift(1)) & (df['slowk'] > df['slowd'])
    
    # Sideways / Range condition: ADX < 25, price at lower Bollinger Band
    sig = (
        (df["adx_4h"] < 25) &
        (df["low"] <= df["bb_lowerband"] * 1.002) &
        (df["close"] > df["bb_lowerband"] * 0.995) &
        (df["rsi"] >= 28) & (df["rsi"] <= 42) &
        (cross_cur == True) &
        (df["slowk"] < 35) &
        (df["is_green"] == True) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    range_bounce_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(range_bounce_sigs, is_short=False)
print(f"Range Mean-Reversion Bounce: Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")
