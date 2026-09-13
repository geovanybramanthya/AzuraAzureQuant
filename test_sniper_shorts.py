import pandas as pd
import numpy as np
from simulate_candidate_engines import pair_data, evaluate_signals

# Test strict sniper shorts vs loosened shorts
df_doge = pair_data['DOGE_USDT_USDT']

sniper_shorts = {}
for p, df in pair_data.items():
    cross_below = (df['slowk'].shift(1) >= df['slowd'].shift(1)) & (df['slowk'] < df['slowd'])
    sig = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 22) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.30 * df["atr"]) &
        (df["rsi"] >= 53) & (df["rsi"] <= 67) &
        (cross_below == True) &
        (df["slowk"] > 55) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.75)
    )
    sniper_shorts[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(sniper_shorts, is_short=True)
print(f"Sniper Shorts (ApexDualAlpha) : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# Test gentle widening on sniper shorts (exact candle OR 1 candle ago with strong red rejection)
gentle_shorts = {}
for p, df in pair_data.items():
    cross_cur = (df['slowk'].shift(1) >= df['slowd'].shift(1)) & (df['slowk'] < df['slowd'])
    cross_prev = (df['slowk'].shift(2) >= df['slowd'].shift(2)) & (df['slowk'].shift(1) < df['slowd'].shift(1))
    cross_gentle = cross_cur | (cross_prev & (df['slowk'] < df['slowd']) & (df['close'] < df['open']) & (df['upper_wick'] > df['body_size'] * 0.5))
    
    sig = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 22) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.30 * df["atr"]) &
        (df["rsi"] >= 53) & (df["rsi"] <= 67) &
        (cross_gentle == True) &
        (df["slowk"] > 55) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.75)
    )
    gentle_shorts[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(gentle_shorts, is_short=True)
print(f"Gentle Sniper Shorts          : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")
