import ccxt
import pandas as pd
import talib as ta
import numpy as np

exchange = ccxt.bybit({'options': {'defaultType': 'linear'}, 'timeout': 15000})

for pair in ['HYPE/USDT:USDT', 'ETH/USDT:USDT']:
    print('=====================================================')
    print('AUDITING PAIR:', pair)
    print('=====================================================')
    o1 = exchange.fetch_ohlcv(pair, '1h', limit=100)
    o4 = exchange.fetch_ohlcv(pair, '4h', limit=250)
    df_1h = pd.DataFrame(o1, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df_4h = pd.DataFrame(o4, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    
    # 4H indicators
    df_4h['adx'] = ta.ADX(df_4h['high'], df_4h['low'], df_4h['close'], timeperiod=14)
    df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)
    
    c_4h = float(df_4h['close'].iloc[-2])
    e50_4h = float(df_4h['ema_50'].iloc[-2])
    e200_4h = float(df_4h['ema_200'].iloc[-2]) if not pd.isna(df_4h['ema_200'].iloc[-2]) else 0.0
    adx_4h = float(df_4h['adx'].iloc[-2])
    macro_bull_4h = bool((c_4h > e50_4h) and (e50_4h > e200_4h))
    
    print(f"[4H MACRO] Close: {c_4h:.2f} | EMA50: {e50_4h:.2f} | EMA200: {e200_4h:.2f} | ADX: {adx_4h:.2f}")
    print(f"[4H MACRO] macro_bull_4h: {macro_bull_4h}")
    
    # 1H indicators
    df_1h['rsi'] = ta.RSI(df_1h['close'], timeperiod=14)
    df_1h['ema_9'] = ta.EMA(df_1h['close'], timeperiod=9)
    df_1h['ema_21'] = ta.EMA(df_1h['close'], timeperiod=21)
    df_1h['ema_50'] = ta.EMA(df_1h['close'], timeperiod=50)
    df_1h['rolling_low_48'] = df_1h['low'].rolling(48).min()
    df_1h['rolling_high_48'] = df_1h['high'].rolling(48).max()
    df_1h['rolling_low_12'] = df_1h['low'].rolling(12).min()
    df_1h['rolling_low_24'] = df_1h['low'].rolling(24).min()
    df_1h['bb_mid'] = df_1h['close'].rolling(20).mean()
    df_1h['bb_std'] = df_1h['close'].rolling(20).std()
    df_1h['bb_width'] = (df_1h['bb_std'] * 4.0) / df_1h['bb_mid']
    bb_q35 = df_1h['bb_width'].rolling(50).quantile(0.35)
    is_squeeze = bool(df_1h['bb_width'].iloc[-2] < bb_q35.iloc[-2]) if not pd.isna(bb_q35.iloc[-2]) else False
    
    stoch = ta.STOCH(df_1h['high'], df_1h['low'], df_1h['close'], fastk_period=14, slowk_period=3, slowd_period=3)
    df_1h['slowk'] = stoch[0]
    df_1h['slowd'] = stoch[1]
    
    candle = df_1h.iloc[-2]
    prev_support = float(df_1h['rolling_low_48'].iloc[-3])
    prev_resistance = float(df_1h['rolling_high_48'].iloc[-3])
    current_price = float(df_1h['close'].iloc[-1])
    
    print(f"[1H CANDLE] Open: {candle['open']:.2f}, High: {candle['high']:.2f}, Low: {candle['low']:.2f}, Close: {candle['close']:.2f}")
    print(f"[1H 48H RANGE] Support: {prev_support:.2f}, Resistance: {prev_resistance:.2f}")
    
    # Check AI Supervisor Pre-Breakout Coiling
    range_span = prev_resistance - prev_support
    local_floor_12 = float(df_1h['rolling_low_12'].iloc[-3])
    local_floor_24 = float(df_1h['rolling_low_24'].iloc[-3])
    range_pos = (candle['close'] - prev_support) / range_span if range_span > 0 else 0
    dist_to_res = (prev_resistance - candle['close']) / candle['close']
    is_upper_range = range_pos >= 0.50
    coiling_near_res = (dist_to_res >= 0.002) and (dist_to_res <= 0.040)
    rising_floor = local_floor_12 >= local_floor_24 * 0.998
    ema9_val = float(df_1h['ema_9'].iloc[-2])
    ema21_val = float(df_1h['ema_21'].iloc[-2])
    ema_hold = (candle['close'] >= ema21_val * 0.996) and (ema9_val >= ema21_val * 0.996)
    rsi_acc = 46.0 <= candle['rsi'] <= 64.0
    squeeze_ok = is_squeeze or (not pd.isna(adx_4h) and adx_4h < 28.0)
    doge_adx_ok = True
    
    print("\n>>> AI SUPERVISOR PRE-BREAKOUT COILING AUDIT <<<")
    print(f"  1. is_upper_range (pos >= 0.50): {is_upper_range} (actual pos = {range_pos:.2f})")
    print(f"  2. coiling_near_res (0.2% <= dist <= 4.0%): {coiling_near_res} (actual dist = {dist_to_res*100:.2f}%)")
    print(f"  3. rising_floor (floor12 >= floor24*0.998): {rising_floor} (f12={local_floor_12}, f24={local_floor_24})")
    print(f"  4. ema_hold (c >= ema21*0.996 & ema9 >= ema21*0.996): {ema_hold} (c={candle['close']:.2f}, ema9={ema9_val:.2f}, ema21={ema21_val:.2f})")
    print(f"  5. rsi_acc (46.0 <= rsi <= 64.0): {rsi_acc} (actual rsi = {candle['rsi']:.2f})")
    print(f"  6. squeeze_ok (is_squeeze or adx_4h < 28): {squeeze_ok} (squeeze={is_squeeze}, adx_4h={adx_4h:.1f})")
    print(f"  7. macro_bull_4h: {macro_bull_4h}")
    ai_all_met = is_upper_range and coiling_near_res and rising_floor and ema_hold and rsi_acc and squeeze_ok and macro_bull_4h
    print(f"  ==> AI SUPERVISOR ALL CONDITIONS MET: {ai_all_met}")
    
    # Check Core Strategy Conditions
    print("\n>>> FREQTRADE CORE STRATEGY AUDIT <<<")
    adx_gate = 22.5 if 'ETH' in pair else 21.0
    adx_ok = adx_4h > adx_gate
    pullback_ok = (candle['close'] <= candle['ema_21'] * 1.006) or (candle['low'] <= candle['bb_mid'])
    ema50_ok = candle['close'] >= df_1h['ema_50'].iloc[-2] * 0.990
    rsi_core_ok = 43.0 <= candle['rsi'] <= 63.0
    stoch_k_prev = float(df_1h['slowk'].iloc[-3])
    stoch_d_prev = float(df_1h['slowd'].iloc[-3])
    stoch_k_curr = float(df_1h['slowk'].iloc[-2])
    stoch_d_curr = float(df_1h['slowd'].iloc[-2])
    stoch_crossover = (stoch_k_prev <= stoch_d_prev) and (stoch_k_curr > stoch_d_curr)
    stoch_under_54 = stoch_k_curr < 54.0
    is_green = candle['close'] > candle['open']
    lower_wick = min(candle['open'], candle['close']) - candle['low']
    body_size = abs(candle['close'] - candle['open'])
    wick_or_green = is_green or (lower_wick > body_size * 0.68)
    vol_mean = float(df_1h['volume'].rolling(20).mean().iloc[-2])
    vol_ok = candle['volume'] > vol_mean * 0.70
    
    print(f"  1. macro_bull_4h: {macro_bull_4h}")
    print(f"  2. adx_4h > {adx_gate}: {adx_ok} (actual = {adx_4h:.2f})")
    print(f"  3. pullback_ok (c <= ema21*1.006 or low <= bb_mid): {pullback_ok} (c={candle['close']:.2f}, ema21={candle['ema_21']:.2f}, low={candle['low']:.2f}, bb_mid={candle['bb_mid']:.2f})")
    print(f"  4. ema50_ok (c >= ema50*0.990): {ema50_ok} (c={candle['close']:.2f}, ema50={df_1h['ema_50'].iloc[-2]:.2f})")
    print(f"  5. rsi_core_ok (43.0 <= rsi <= 63.0): {rsi_core_ok} (actual = {candle['rsi']:.2f})")
    print(f"  6. stoch_crossover (EXACT cross on last candle): {stoch_crossover} (k_prev={stoch_k_prev:.1f}, d_prev={stoch_d_prev:.1f} -> k_curr={stoch_k_curr:.1f}, d_curr={stoch_d_curr:.1f})")
    print(f"  7. stoch_under_54 (k_curr < 54.0): {stoch_under_54} (actual = {stoch_k_curr:.1f})")
    print(f"  8. wick_or_green: {wick_or_green} (is_green={is_green}, lower_wick={lower_wick:.4f}, body_size*0.68={body_size*0.68:.4f})")
    print(f"  9. vol_ok (volume > vol_mean*0.70): {vol_ok} (vol={candle['volume']:.2f}, mean*0.7={vol_mean*0.70:.2f})")
    
    core_all_met = (macro_bull_4h and adx_ok and pullback_ok and ema50_ok and rsi_core_ok and 
                    stoch_crossover and stoch_under_54 and wick_or_green and vol_ok)
    print(f"  ==> FREQTRADE CORE ALL CONDITIONS MET: {core_all_met}")
    print()
