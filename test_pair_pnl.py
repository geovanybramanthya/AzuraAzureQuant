from simulate_candidate_engines import pair_data, evaluate_signals

# Test pairs individually to see pair-by-pair performance of Widened Long Pullback
print("=== Individual Pair Analysis of Widened Long Pullback ===")
for p in pair_data.keys():
    df = pair_data[p]
    cross_cur = (df['slowk'].shift(1) <= df['slowd'].shift(1)) & (df['slowk'] > df['slowd'])
    cross_prev = (df['slowk'].shift(2) <= df['slowd'].shift(2)) & (df['slowk'].shift(1) > df['slowd'].shift(1))
    cross_2c_long = cross_cur | (cross_prev & (df['slowk'] > df['slowd']) & (df['is_green'] == True))
    
    sig = (
        (df["macro_bull_4h_4h"] == True) &
        (df["adx_4h"] > 21) &
        ((df["close"] <= df["ema_21"] * 1.006) | (df["low"] <= df["bb_middleband"])) &
        (df["close"] >= df["ema_50"] * 0.990) &
        (df["rsi"] >= 41) & (df["rsi"] <= 63) &
        (cross_2c_long == True) &
        (df["slowk"] < 52) &
        ((df["is_green"] == True) | (df["lower_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    
    n, wr, avg_p, tot_p = evaluate_signals({p: sig}, is_short=False)
    print(f"{p:20s} | Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")
