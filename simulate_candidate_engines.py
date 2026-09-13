import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path

data_dir = Path('user_data/data/bybit/futures')
pairs = ['BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT', 'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT', 'PAXG_USDT_USDT']

pair_data = {}
for p in pairs:
    f_1h = data_dir / f"{p}-1h-futures.feather"
    f_4h = data_dir / f"{p}-4h-futures.feather"
    if not (f_1h.exists() and f_4h.exists()):
        continue
    df_1h = pd.read_feather(f_1h)
    df_4h = pd.read_feather(f_4h)
    
    # 4h indicators
    df_4h['ema_50'] = ta.EMA(df_4h, timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h, timeperiod=200)
    df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
    df_4h['rsi'] = ta.RSI(df_4h, timeperiod=14)
    df_4h['macro_bull_4h'] = (df_4h['ema_50'] > df_4h['ema_200']) & (df_4h['close'] > df_4h['ema_50'])
    df_4h['macro_bear_4h'] = (df_4h['ema_50'] < df_4h['ema_200']) & (df_4h['close'] < df_4h['ema_50'])
    
    from freqtrade.strategy import merge_informative_pair
    df = merge_informative_pair(df_1h, df_4h[['date', 'macro_bull_4h', 'macro_bear_4h', 'adx', 'rsi']], '1h', '4h', ffill=True)
    
    # 1h indicators
    df['ema_9'] = ta.EMA(df, timeperiod=9)
    df['ema_21'] = ta.EMA(df, timeperiod=21)
    df['ema_50'] = ta.EMA(df, timeperiod=50)
    df['ema_200'] = ta.EMA(df, timeperiod=200)
    df['rsi'] = ta.RSI(df, timeperiod=14)
    df['adx'] = ta.ADX(df, timeperiod=14)
    df['atr'] = ta.ATR(df, timeperiod=14)
    
    boll = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=20, stds=2.0)
    df['bb_lowerband'] = boll['lower']
    df['bb_middleband'] = boll['mid']
    df['bb_upperband'] = boll['upper']
    df['bb_width'] = (df['bb_upperband'] - df['bb_lowerband']) / df['bb_middleband']
    
    kelt = qtpylib.keltner_channel(df, window=20, atrs=1.5)
    df['kc_upperband'] = kelt['upper']
    df['kc_middleband'] = kelt['mid']
    df['kc_lowerband'] = kelt['lower']
    
    # Squeeze
    df['squeeze_on'] = (df['bb_lowerband'] > df['kc_lowerband']) & (df['bb_upperband'] < df['kc_upperband'])
    df['was_in_squeeze_3'] = df['squeeze_on'].rolling(window=3).max() > 0
    df['was_in_squeeze_5'] = df['squeeze_on'].rolling(window=5).max() > 0
    
    stoch = ta.STOCH(df, fastk_period=14, slowk_period=3, slowd_period=3)
    df['slowk'] = stoch['slowk']
    df['slowd'] = stoch['slowd']
    
    df['body_size'] = (df['close'] - df['open']).abs()
    df['is_green'] = df['close'] > df['open']
    df['is_red'] = df['close'] < df['open']
    df['lower_wick'] = np.where(df['is_green'], df['open'] - df['low'], df['close'] - df['low'])
    df['upper_wick'] = np.where(df['is_green'], df['high'] - df['close'], df['high'] - df['open'])
    df['volume_mean_20'] = df['volume'].rolling(window=20).mean()
    
    pair_data[p] = df

print(f"Loaded {len(pair_data)} pairs successfully.")

# Function to simulate simple forward trade returns using ROI / Stale loss
def evaluate_signals(signal_series_dict, is_short=False):
    total_trades = 0
    wins = 0
    losses = 0
    pnls = []
    
    for p, sigs in signal_series_dict.items():
        df = pair_data[p]
        indices = df.index[sigs].tolist()
        
        for idx in indices:
            if idx + 48 >= len(df):
                continue
            total_trades += 1
            entry_price = df.loc[idx, 'close']
            
            # Walk forward up to 48 candles
            hit_roi = False
            hit_stale = False
            hit_tp = False
            trade_pnl = 0.0
            
            for step in range(1, 49):
                cur_idx = idx + step
                high = df.loc[cur_idx, 'high']
                low = df.loc[cur_idx, 'low']
                close = df.loc[cur_idx, 'close']
                
                # Check profit
                if not is_short:
                    max_p = (high - entry_price) / entry_price
                    cur_p = (close - entry_price) / entry_price
                    min_p = (low - entry_price) / entry_price
                else:
                    max_p = (entry_price - low) / entry_price
                    cur_p = (entry_price - close) / entry_price
                    min_p = (entry_price - high) / entry_price
                
                # Short explosive dump
                if is_short and cur_p > 0.045:
                    trade_pnl = 0.045
                    hit_tp = True
                    break
                
                # Check ROI
                # 0-7.8h: 52.6%
                # 7.8h-11.5h: 16.1%
                # 11.5h-19.5h: 9.0%
                # >19.5h: 0.0%
                if step <= 7 and max_p >= 0.526:
                    trade_pnl = 0.526; hit_roi = True; break
                elif 8 <= step <= 11 and max_p >= 0.161:
                    trade_pnl = 0.161; hit_roi = True; break
                elif 12 <= step <= 19 and max_p >= 0.09:
                    trade_pnl = 0.09; hit_roi = True; break
                elif step > 19 and cur_p > 0.002:
                    trade_pnl = cur_p; hit_roi = True; break
                
                # Stale loss at 36h
                if step >= 36 and cur_p < -0.010:
                    trade_pnl = cur_p
                    hit_stale = True
                    break
                
                # Cutoff at 48h
                if step >= 48:
                    trade_pnl = cur_p
                    break
            
            pnls.append(trade_pnl)
            if trade_pnl > 0:
                wins += 1
            else:
                losses += 1
                
    winrate = wins / total_trades * 100 if total_trades else 0
    avg_pnl = np.mean(pnls) * 100 if pnls else 0
    tot_pnl = np.sum(pnls) * 100 if pnls else 0
    return total_trades, winrate, avg_pnl, tot_pnl

print("Simulator ready.")

# 1. Baseline Long Pullback
base_long_sigs = {}
for p, df in pair_data.items():
    sig = (
        (df["macro_bull_4h_4h"] == True) &
        (df["adx_4h"] > 21) &
        ((df["close"] <= df["ema_21"] * 1.006) | (df["low"] <= df["bb_middleband"])) &
        (df["close"] >= df["ema_50"] * 0.990) &
        (df["rsi"] >= 41) & (df["rsi"] <= 63) &
        (qtpylib.crossed_above(df["slowk"], df["slowd"])) &
        (df["slowk"] < 52) &
        ((df["is_green"] == True) | (df["lower_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    base_long_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(base_long_sigs, is_short=False)
print(f"Engine 1 (Base Long Pullback)      : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# 2. Widened Long Pullback (Stoch cross 1 or 2 candles with green confirmation)
widened_long_sigs = {}
for p, df in pair_data.items():
    cross_cur = qtpylib.crossed_above(df["slowk"], df["slowd"])
    cross_prev = qtpylib.crossed_above(df["slowk"].shift(1), df["slowd"].shift(1))
    cross_2c = cross_cur | (cross_prev & (df["slowk"] > df["slowd"]) & (df["is_green"] == True))
    
    sig = (
        (df["macro_bull_4h_4h"] == True) &
        (df["adx_4h"] > 21) &
        ((df["close"] <= df["ema_21"] * 1.006) | (df["low"] <= df["bb_middleband"])) &
        (df["close"] >= df["ema_50"] * 0.990) &
        (df["rsi"] >= 41) & (df["rsi"] <= 63) &
        (cross_2c == True) &
        (df["slowk"] < 55) &
        ((df["is_green"] == True) | (df["lower_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    widened_long_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(widened_long_sigs, is_short=False)
print(f"Engine 1 (Widened Long Pullback)   : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# 3. Trend Momentum Impulse / Continuation Long
momentum_long_sigs = {}
for p, df in pair_data.items():
    sig = (
        (df["macro_bull_4h_4h"] == True) &
        (df["adx_4h"] > 20) &
        (df["ema_9"] > df["ema_21"]) & (df["ema_21"] > df["ema_50"]) &
        (df["low"] <= df["ema_9"] * 1.003) & (df["close"] > df["ema_9"] * 0.998) &
        (df["rsi"] >= 50) & (df["rsi"] <= 65) &
        (df["slowk"] > df["slowd"]) & (df["slowk"] < 70) &
        (df["is_green"] == True) &
        (df["volume"] > df["volume_mean_20"] * 0.85)
    )
    momentum_long_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(momentum_long_sigs, is_short=False)
print(f"Candidate Engine (Momentum Retest) : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# 4. Squeeze Expansion Long (Carter Style from BB mid)
squeeze_long_sigs = {}
for p, df in pair_data.items():
    sig = (
        (df["was_in_squeeze_3"] == True) &
        (~df["squeeze_on"]) &
        (df["macro_bull_4h_4h"] == True) &
        (df["close"] > df["bb_middleband"]) & (df["close"] < df["bb_upperband"] * 0.995) &
        (df["close"] > df["ema_9"]) &
        (df["rsi"] >= 52) & (df["rsi"] <= 68) &
        (df["slowk"] > df["slowd"]) &
        (df["is_green"] == True) &
        (df["volume"] > df["volume_mean_20"] * 1.05)
    )
    squeeze_long_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(squeeze_long_sigs, is_short=False)
print(f"Engine 2 (Squeeze Expansion Long)  : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# 5. Base Short ATR Fade
base_short_sigs = {}
for p, df in pair_data.items():
    sig = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 22) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.30 * df["atr"]) &
        (df["rsi"] >= 53) & (df["rsi"] <= 67) &
        (qtpylib.crossed_below(df["slowk"], df["slowd"])) &
        (df["slowk"] > 55) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.75)
    )
    base_short_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(base_short_sigs, is_short=True)
print(f"Engine 3 (Base Short ATR Fade)     : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

# 6. Widened Short ATR Fade
widened_short_sigs = {}
for p, df in pair_data.items():
    cross_cur = qtpylib.crossed_below(df["slowk"], df["slowd"])
    cross_prev = qtpylib.crossed_below(df["slowk"].shift(1), df["slowd"].shift(1))
    cross_2c = cross_cur | (cross_prev & (df["slowk"] < df["slowd"]) & (df["is_red"] == True))
    
    sig = (
        (df["macro_bear_4h_4h"] == True) &
        (df["adx_4h"] > 20) &
        ((df["high"] >= df["ema_21"] * 0.996) | (df["high"] >= df["bb_middleband"])) &
        (df["close"] <= df["ema_50"] + 0.35 * df["atr"]) &
        (df["rsi"] >= 50) & (df["rsi"] <= 68) &
        (cross_2c == True) &
        (df["slowk"] > 50) &
        ((df["is_red"] == True) | (df["upper_wick"] > df["body_size"] * 0.70)) &
        (df["volume"] > df["volume_mean_20"] * 0.70)
    )
    widened_short_sigs[p] = sig

n, wr, avg_p, tot_p = evaluate_signals(widened_short_sigs, is_short=True)
print(f"Engine 3 (Widened Short ATR Fade)  : Signals: {n:4d} | Winrate: {wr:5.1f}% | AvgPnL: {avg_p:5.2f}% | TotPnL: {tot_p:6.1f}%")

