from simulate_candidate_engines import pair_data, evaluate_signals

exact_longs = {}
exact_shorts = {}

for p, df in pair_data.items():
    # Engine 1: Widened Long Pullback (Stoch cross 1 or 2 candles with green confirmation)
    cross_cur = (df['slowk'].shift(1) <= df['slowd'].shift(1)) & (df['slowk'] > df['slowd'])
    cross_prev = (df['slowk'].shift(2) <= df['slowd'].shift(2)) & (df['slowk'].shift(1) > df['slowd'].shift(1))
    cross_2c_long = cross_cur | (cross_prev & (df['slowk'] > df['slowd']) & (df['is_green'] == True))
    
    e1_pullback = (
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
    
    # Engine 2: Volatility Squeeze Expansion (Quantile 0.25 compression release)
    bw_comp = df['bb_width'] < df['bb_width'].rolling(50).quantile(0.25)
    was_comp = bw_comp.rolling(3).max() > 0
    e2_squeeze = (
        (was_comp == True) & (~bw_comp) &
        (df["macro_bull_4h_4h"] == True) &
        (df["close"] > df["bb_middleband"]) & (df["close"] < df["bb_upperband"] * 0.998) &
        (df["close"] > df["ema_9"]) &
        (df["rsi"] >= 52) & (df["rsi"] <= 68) &
        (df["slowk"] > df["slowd"]) &
        (df["is_green"] == True) &
        (df["volume"] > df["volume_mean_20"] * 1.05)
    )
    
    exact_longs[p] = e1_pullback | e2_squeeze
    
    # Engine 3: Gentle Sniper Shorts
    cross_cur_s = (df['slowk'].shift(1) >= df['slowd'].shift(1)) & (df['slowk'] < df['slowd'])
    cross_prev_s = (df['slowk'].shift(2) >= df['slowd'].shift(2)) & (df['slowk'].shift(1) < df['slowd'].shift(1))
    cross_gentle_s = cross_cur_s | (cross_prev_s & (df['slowk'] < df['slowd']) & (df['close'] < df['open']) & (df['upper_wick'] > df['body_size'] * 0.5))
    
    e3_shorts = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 22) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.30 * df["atr"]) &
        (df["rsi"] >= 53) & (df["rsi"] <= 67) &
        (cross_gentle_s == True) &
        (df["slowk"] > 55) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.75)
    )
    
    exact_shorts[p] = e3_shorts

nl, wrl, avgl, totl = evaluate_signals(exact_longs, is_short=False)
ns, wrs, avgs, tots = evaluate_signals(exact_shorts, is_short=True)

print("=== EXACT 3-ENGINE ARCHITECTURE EVALUATION ===")
print(f"Engine 1+2 (Longs) : Signals: {nl:4d} | Winrate: {wrl:5.1f}% | AvgPnL: {avgl:5.2f}% | TotPnL: {totl:6.1f}%")
print(f"Engine 3   (Shorts): Signals: {ns:4d} | Winrate: {wrs:5.1f}% | AvgPnL: {avgs:5.2f}% | TotPnL: {tots:6.1f}%")
print(f"TOTAL Signals      : {nl + ns:4d} across 965 days ({(nl+ns)/965:.2f} signals/day)")
print(f"Blended Winrate    : {(wrl*nl + wrs*ns)/(nl+ns):.1f}% | Total Combined PnL: {totl + tots:.1f}%")
