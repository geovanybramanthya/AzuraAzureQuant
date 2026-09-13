import pandas as pd
import numpy as np
from simulate_candidate_engines import pair_data, evaluate_signals

# Combine Widened Long Pullback, Squeeze Long, and Widened Short ATR Fade
combined_long_sigs = {}
combined_short_sigs = {}

for p, df in pair_data.items():
    # 1. Widened Long Pullback
    cross_cur = (df['slowk'].shift(1) <= df['slowd'].shift(1)) & (df['slowk'] > df['slowd'])
    cross_prev = (df['slowk'].shift(2) <= df['slowd'].shift(2)) & (df['slowk'].shift(1) > df['slowd'].shift(1))
    cross_2c_long = cross_cur | (cross_prev & (df['slowk'] > df['slowd']) & (df['is_green'] == True))
    
    long_pullback = (
        (df["macro_bull_4h_4h"] == True) &
        (df["adx_4h"] > 21) &
        ((df["close"] <= df["ema_21"] * 1.006) | (df["low"] <= df["bb_middleband"])) &
        (df["close"] >= df["ema_50"] * 0.990) &
        (df["rsi"] >= 41) & (df["rsi"] <= 63) &
        (cross_2c_long == True) &
        (df["slowk"] < 55) &
        ((df["is_green"] == True) | (df["lower_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    
    # 2. Squeeze Long
    bw_comp = df['bb_width'] < df['bb_width'].rolling(50).quantile(0.25)
    was_comp = bw_comp.rolling(3).max() > 0
    squeeze_long = (
        (was_comp == True) & (~bw_comp) &
        (df["macro_bull_4h_4h"] == True) &
        (df["close"] > df["bb_middleband"]) & (df["close"] < df["bb_upperband"] * 0.998) &
        (df["close"] > df["ema_9"]) &
        (df["rsi"] >= 52) & (df["rsi"] <= 68) &
        (df["slowk"] > df["slowd"]) &
        (df["is_green"] == True) &
        (df["volume"] > df["volume_mean_20"] * 1.05)
    )
    
    combined_long_sigs[p] = long_pullback | squeeze_long
    
    # 3. Widened Short ATR Fade
    cross_cur_s = (df['slowk'].shift(1) >= df['slowd'].shift(1)) & (df['slowk'] < df['slowd'])
    cross_prev_s = (df['slowk'].shift(2) >= df['slowd'].shift(2)) & (df['slowk'].shift(1) < df['slowd'].shift(1))
    cross_2c_short = cross_cur_s | (cross_prev_s & (df['slowk'] < df['slowd']) & (df['is_red'] == True))
    
    short_fade = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 20) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.35 * df["atr"]) &
        (df["rsi"] >= 50) & (df["rsi"] <= 68) &
        (cross_2c_short == True) &
        (df["slowk"] > 50) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    combined_short_sigs[p] = short_fade

nl, wrl, avgl, totl = evaluate_signals(combined_long_sigs, is_short=False)
ns, wrs, avgs, tots = evaluate_signals(combined_short_sigs, is_short=True)

print(f"Combined Longs : Signals: {nl:4d} | Winrate: {wrl:5.1f}% | AvgPnL: {avgl:5.2f}% | TotPnL: {totl:6.1f}%")
print(f"Combined Shorts: Signals: {ns:4d} | Winrate: {wrs:5.1f}% | AvgPnL: {avgs:5.2f}% | TotPnL: {tots:6.1f}%")
print(f"TOTAL Signals  : {nl + ns:4d} across 965 days ({(nl+ns)/965:.2f} signals/day)")
print(f"Blended Winrate: {(wrl*nl + wrs*ns)/(nl+ns):.1f}% | Total Combined PnL: {totl + tots:.1f}%")
