from freqtrade.data.history import load_pair_history
from pathlib import Path
import pandas as pd, talib.abstract as ta

data_dir = Path('user_data/data/bybit')
pairs = ['ETH/USDT:USDT', 'ADA/USDT:USDT', 'LINK/USDT:USDT', 'SOL/USDT:USDT']

print("================== LIVE MARKET AUDIT (07:00 WIB) ==================")
for p in pairs:
    df_1h = load_pair_history(pair=p, timeframe='1h', datadir=data_dir, candle_type='futures')
    df_4h = load_pair_history(pair=p, timeframe='4h', datadir=data_dir, candle_type='futures')
    
    df_4h['ema_50'] = ta.EMA(df_4h, timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h, timeperiod=200)
    last_4h = df_4h.iloc[-1]
    is_4h_bull = bool((last_4h['ema_50'] > last_4h['ema_200']) and (last_4h['close'] > last_4h['ema_50']))
    
    df_1h['rsi'] = ta.RSI(df_1h, timeperiod=14)
    last_1h = df_1h.iloc[-1]
    
    status = "Trend Bullish (Menunggu Sinyal Dip)" if is_4h_bull else "Konsolidasi / Flat"
    print(f"{p:16} | Harga: ${last_1h['close']:.4f} | RSI 1H: {last_1h['rsi']:.1f} | Status 4H: {status}")
print("===================================================================")
