import pandas as pd
import numpy as np
import talib.abstract as ta

# Load BTC 15m and 1h
df_btc_15m = pd.read_feather("user_data/data/bybit/futures/BTC_USDT_USDT-15m-futures.feather")
df_btc_1h = pd.read_feather("user_data/data/bybit/futures/BTC_USDT_USDT-1h-futures.feather")

df_btc_15m['date'] = pd.to_datetime(df_btc_15m['date'], utc=True)
df_btc_1h['date'] = pd.to_datetime(df_btc_1h['date'], utc=True)

# Filter around Sep 17-18, 2026
start_dt = "2026-09-17 18:00:00+00:00"
end_dt = "2026-09-18 12:00:00+00:00"

sub_15m = df_btc_15m[(df_btc_15m['date'] >= start_dt) & (df_btc_15m['date'] <= end_dt)].copy()

# Calculate indicators
sub_15m['ema_9'] = ta.EMA(sub_15m['close'], timeperiod=9)
sub_15m['ema_21'] = ta.EMA(sub_15m['close'], timeperiod=21)
sub_15m['rsi'] = ta.RSI(sub_15m['close'], timeperiod=14)
sub_15m['atr'] = ta.ATR(sub_15m['high'], sub_15m['low'], sub_15m['close'], timeperiod=14)
sub_15m['vol_mean'] = sub_15m['volume'].rolling(20).mean()

# Squeeze indicator
sub_15m['bb_mid'] = sub_15m['close'].rolling(20).mean()
sub_15m['bb_std'] = sub_15m['close'].rolling(20).std()
sub_15m['bb_width'] = (sub_15m['bb_std'] * 4.0) / sub_15m['bb_mid']

print("=== BTC 15M CANDLES AROUND BREAKOUT ===")
cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'rsi', 'bb_width']
print(sub_15m[cols].to_string())

# Check SOL and ETH 15m as well
df_sol_15m = pd.read_feather("user_data/data/bybit/futures/SOL_USDT_USDT-15m-futures.feather")
df_sol_15m['date'] = pd.to_datetime(df_sol_15m['date'], utc=True)
sub_sol = df_sol_15m[(df_sol_15m['date'] >= start_dt) & (df_sol_15m['date'] <= end_dt)].copy()
sub_sol['return_pct'] = (sub_sol['close'] - sub_sol['open']) / sub_sol['open'] * 100

print("\n=== SOL 15M RETURNS VS BTC 15M RETURNS ===")
comp = pd.merge(sub_15m[['date', 'close', 'volume']].rename(columns={'close': 'btc_close', 'volume': 'btc_vol'}),
                sub_sol[['date', 'close', 'volume']].rename(columns={'close': 'sol_close', 'volume': 'sol_vol'}),
                on='date')
comp['btc_ret%'] = comp['btc_close'].pct_change() * 100
comp['sol_ret%'] = comp['sol_close'].pct_change() * 100
print(comp[['date', 'btc_close', 'btc_ret%', 'sol_close', 'sol_ret%']].to_string())
