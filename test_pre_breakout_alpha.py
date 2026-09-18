import pandas as pd
import numpy as np
import talib.abstract as ta

# Test Pre-Breakout Base Accumulation logic on BTC and SOL
def test_pre_breakout_on_pair(pair_symbol):
    feather_file = f"user_data/data/bybit/futures/{pair_symbol}_USDT_USDT-1h-futures.feather"
    df = pd.read_feather(feather_file)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    
    # 1H indicators
    df['ema_9'] = ta.EMA(df['close'], timeperiod=9)
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['ema_200'] = ta.EMA(df['close'], timeperiod=200)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=14)
    df['vol_mean'] = df['volume'].rolling(20).mean()

    # Volatility Squeeze
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
    df['bb_width_p30'] = df['bb_width'].rolling(100).quantile(0.30)
    df['is_squeeze'] = df['bb_width'] <= df['bb_width_p30']

    # Higher Lows in 12h window (Ascending Base)
    # Check if lowest low in last 6h > lowest low in previous 6-12h
    low_0_6 = df['low'].rolling(6).min()
    low_6_12 = df['low'].shift(6).rolling(6).min()
    df['higher_low_base'] = low_0_6 > low_6_12 * 1.002

    # Resistance proximity: High in last 6h is within 1% of 48h High
    df['high_48'] = df['high'].rolling(48).max()
    df['near_resistance'] = df['high'].rolling(6).max() >= df['high_48'] * 0.990

    # Macro Bull: Close > EMA 50 > EMA 200
    df['macro_bull'] = (df['close'] > df['ema_50']) & (df['ema_50'] > df['ema_200'])

    # Volume Absorption: green volume > red volume
    df['is_green'] = df['close'] > df['open']
    vol_green_6h = (df['volume'] * df['is_green']).rolling(6).sum()
    vol_red_6h = (df['volume'] * (~df['is_green'])).rolling(6).sum()
    df['volume_absorption'] = vol_green_6h > vol_red_6h * 1.10

    # Pre-Breakout Candidate
    df['pre_breakout_signal'] = (
        df['macro_bull'] &
        df['is_squeeze'] &
        df['higher_low_base'] &
        df['near_resistance'] &
        df['volume_absorption'] &
        (df['rsi'] >= 50.0) & (df['rsi'] <= 68.0)
    )

    signals = df[df['pre_breakout_signal']].copy()
    print(f"[{pair_symbol}] Total Pre-Breakout Base Signals in 2.5 Years: {len(signals)}")
    
    # Check if signal fired on Sep 17-18, 2026
    sep18 = signals[(signals['date'] >= '2026-09-17 12:00:00+00:00') & (signals['date'] <= '2026-09-18 06:00:00+00:00')]
    print(f"[{pair_symbol}] Signals around Sep 17-18, 2026:\n", sep18[['date', 'close', 'bb_width', 'rsi']].to_string())

for p in ['BTC', 'ETH', 'SOL']:
    test_pre_breakout_on_pair(p)
