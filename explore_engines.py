import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path

data_dir = Path('user_data/data/bybit/futures')
pairs = ['BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT', 'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT', 'PAXG_USDT_USDT']

pair_dfs = {}
for p in pairs:
    f_1h = data_dir / f"{p}-1h-futures.feather"
    f_4h = data_dir / f"{p}-4h-futures.feather"
    if f_1h.exists() and f_4h.exists():
        df_1h = pd.read_feather(f_1h)
        df_4h = pd.read_feather(f_4h)
        
        # 4h indicators
        df_4h['ema_50'] = ta.EMA(df_4h, timeperiod=50)
        df_4h['ema_200'] = ta.EMA(df_4h, timeperiod=200)
        df_4h['macro_bull'] = (df_4h['ema_50'] > df_4h['ema_200']) & (df_4h['close'] > df_4h['ema_50'])
        df_4h['macro_bear'] = (df_4h['ema_50'] < df_4h['ema_200']) & (df_4h['close'] < df_4h['ema_50'])
        df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
        
        # Merge 4h to 1h
        from freqtrade.strategy import merge_informative_pair
        df = merge_informative_pair(df_1h, df_4h[['date', 'macro_bull', 'macro_bear', 'adx']], '1h', '4h', ffill=True)
        
        # 1h indicators
        df['ema_9'] = ta.EMA(df, timeperiod=9)
        df['ema_21'] = ta.EMA(df, timeperiod=21)
        df['ema_50'] = ta.EMA(df, timeperiod=50)
        df['rsi'] = ta.RSI(df, timeperiod=14)
        df['atr'] = ta.ATR(df, timeperiod=14)
        
        boll = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=20, stds=2.0)
        df['bb_lower'] = boll['lower']
        df['bb_mid'] = boll['mid']
        df['bb_upper'] = boll['upper']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
        
        kelt = qtpylib.keltner_channel(df, window=20, atrs=1.5)
        df['kc_upper'] = kelt['upper']
        df['kc_mid'] = kelt['mid']
        df['kc_lower'] = kelt['lower']
        
        # Squeeze
        df['squeeze_on'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
        df['squeeze_off'] = ~df['squeeze_on']
        
        stoch = ta.STOCH(df, fastk_period=14, slowk_period=3, slowd_period=3)
        df['slowk'] = stoch['slowk']
        df['slowd'] = stoch['slowd']
        
        df['vol_mean'] = df['volume'].rolling(20).mean()
        df['body_size'] = (df['close'] - df['open']).abs()
        df['is_green'] = df['close'] > df['open']
        df['is_red'] = df['close'] < df['open']
        df['lower_wick'] = np.where(df['is_green'], df['open'] - df['low'], df['close'] - df['low'])
        df['upper_wick'] = np.where(df['is_green'], df['high'] - df['close'], df['high'] - df['open'])
        
        pair_dfs[p] = df

print("Loaded all pairs successfully. Total length:", {p: len(df) for p, df in pair_dfs.items()})
