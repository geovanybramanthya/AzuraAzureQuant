from simulate_candidate_engines import pair_data, evaluate_signals

df_sol = pair_data['SOL_USDT_USDT']

# Test different entry filters for SOL
for rsi_max_sol in [63, 58, 54, 50]:
    for ema_level in ['ema_21', 'bb_mid', 'ema_50']:
        cross_cur = (df_sol['slowk'].shift(1) <= df_sol['slowd'].shift(1)) & (df_sol['slowk'] > df_sol['slowd'])
        cross_prev = (df_sol['slowk'].shift(2) <= df_sol['slowd'].shift(2)) & (df_sol['slowk'].shift(1) > df_sol['slowd'].shift(1))
        cross_2c = cross_cur | (cross_prev & (df_sol['slowk'] > df_sol['slowd']) & (df_sol['is_green'] == True))
        
        if ema_level == 'ema_21':
            pb = (df_sol["close"] <= df_sol["ema_21"] * 1.006) | (df_sol["low"] <= df_sol["bb_middleband"])
        elif ema_level == 'bb_mid':
            pb = df_sol["low"] <= df_sol["bb_middleband"]
        else:
            pb = (df_sol["low"] <= df_sol["ema_50"] * 1.005)
            
        sig = (
            (df_sol["macro_bull_4h_4h"] == True) &
            (df_sol["adx_4h"] > 21) &
            pb &
            (df_sol["close"] >= df_sol["ema_50"] * 0.985) &
            (df_sol["rsi"] >= 41) & (df_sol["rsi"] <= rsi_max_sol) &
            (cross_2c == True) &
            (df_sol["slowk"] < 52) &
            ((df_sol["is_green"] == True) | (df_sol["lower_wick"] > df_sol["body_size"] * 0.70)) &
            (df_sol["volume"] > df_sol["volume_mean_20"] * 0.70)
        )
        n, wr, avg_p, tot_p = evaluate_signals({'SOL_USDT_USDT': sig}, is_short=False)
        print(f"SOL: ema={ema_level:6s} rsi_max={rsi_max_sol:2d} | Signals: {n:3d} | Win%: {wr:5.1f}% | Avg: {avg_p:5.2f}% | Tot: {tot_p:6.1f}%")
