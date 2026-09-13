import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path

data_dir = Path('user_data/data/bybit/futures')
pairs = ['BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT', 'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT', 'PAXG_USDT_USDT']

from simulate_candidate_engines import pair_data, evaluate_signals

# Test different Squeeze definitions
print("Testing Squeeze Formulations for Engine 2:")

for atrs_val in [1.5, 1.2, 1.0]:
    squeeze_sigs = {}
    for p, df in pair_data.items():
        kelt = qtpylib.keltner_channel(df, window=20, atrs=atrs_val)
        sq_on = (df['bb_lowerband'] > kelt['lower']) & (df['bb_upperband'] < kelt['upper'])
        was_sq = sq_on.rolling(window=3).max() > 0
        
        sig = (
            (was_sq == True) &
            (~sq_on) &
            (df["macro_bull_4h_4h"] == True) &
            (df["close"] > df["bb_middleband"]) & (df["close"] < df["bb_upperband"] * 0.998) &
            (df["close"] > df["ema_9"]) &
            (df["rsi"] >= 50) & (df["rsi"] <= 68) &
            (df["slowk"] > df["slowd"]) &
            (df["is_green"] == True) &
            (df["volume"] > df["volume_mean_20"] * 1.05)
        )
        squeeze_sigs[p] = sig
        
    n, wr, avg_p, tot_p = evaluate_signals(squeeze_sigs, is_short=False)
    print(f"KC atrs={atrs_val:.1f} | Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# Test Bandwidth Quantile Squeeze
for q in [0.25, 0.35, 0.40]:
    bw_sigs = {}
    for p, df in pair_data.items():
        bw_compressed = df['bb_width'] < df['bb_width'].rolling(50).quantile(q)
        was_comp = bw_compressed.rolling(window=3).max() > 0
        
        sig = (
            (was_comp == True) &
            (~bw_compressed) &
            (df["macro_bull_4h_4h"] == True) &
            (df["close"] > df["bb_middleband"]) & (df["close"] < df["bb_upperband"] * 0.998) &
            (df["close"] > df["ema_9"]) &
            (df["rsi"] >= 50) & (df["rsi"] <= 68) &
            (df["slowk"] > df["slowd"]) &
            (df["is_green"] == True) &
            (df["volume"] > df["volume_mean_20"] * 1.05)
        )
        bw_sigs[p] = sig
        
    n, wr, avg_p, tot_p = evaluate_signals(bw_sigs, is_short=False)
    print(f"BB Width Quantile q={q:.2f} | Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")
