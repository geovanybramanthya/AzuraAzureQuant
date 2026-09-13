import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path

# Load data for our 7 pairs
pairs = ['BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT', 'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT', 'PAXG_USDT_USDT']
data_dir = Path('user_data/data/bybit/futures')

for pair in pairs:
    fpath = data_dir / f"{pair}-1h-futures.feather"
    if not fpath.exists():
        print(f"Missing {fpath}")
        continue
    df = pd.read_feather(fpath)
    
    # Calculate indicators
    df['ema_9'] = ta.EMA(df, timeperiod=9)
    df['ema_21'] = ta.EMA(df, timeperiod=21)
    df['ema_50'] = ta.EMA(df, timeperiod=50)
    df['ema_200'] = ta.EMA(df, timeperiod=200)
    df['rsi'] = ta.RSI(df, timeperiod=14)
    df['atr'] = ta.ATR(df, timeperiod=14)
    
    bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=20, stds=2.0)
    df['bb_lower'] = bollinger['lower']
    df['bb_mid'] = bollinger['mid']
    df['bb_upper'] = bollinger['upper']
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    
    keltner = qtpylib.keltner_channel(df, window=20, atrs=1.5)
    df['kc_upper'] = keltner['upper']
    df['kc_mid'] = keltner['mid']
    df['kc_lower'] = keltner['lower']
    
    # Squeeze
    df['in_squeeze'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
    df['was_in_squeeze'] = df['in_squeeze'].shift(1) | df['in_squeeze'].shift(2) | df['in_squeeze'].shift(3)
    df['squeeze_release'] = (~df['in_squeeze']) & df['was_in_squeeze']
    
    # Momentum (MACD histogram or Carter Momentum)
    macd = ta.MACD(df, fastperiod=12, slowperiod=26, signalperiod=9)
    df['macd_hist'] = macd['macdhist']
    
    # Volume
    df['vol_mean'] = df['volume'].rolling(20).mean()
    
    # Test Long Squeeze Release:
    # 1. Was in squeeze
    # 2. Release with positive momentum expansion: macd_hist > 0 and macd_hist > macd_hist.shift(1)
    # 3. Price crossed above EMA 9 or BB mid (early impulse, not late)
    # 4. Trend filter: EMA 50 > EMA 200 (or close > EMA 50)
    long_sig = (
        df['squeeze_release'] &
        (df['macd_hist'] > 0) &
        (df['macd_hist'] > df['macd_hist'].shift(1)) &
        (df['close'] > df['ema_9']) &
        (df['close'] > df['bb_mid']) &
        (df['close'] < df['bb_upper'] * 0.995) & # NOT overextended!
        (df['close'] > df['ema_50']) &
        (df['rsi'] > 50) & (df['rsi'] < 68) &
        (df['volume'] > df['vol_mean'] * 1.0)
    )
    
    # Let's calculate forward 24h max profit and outcome
    n_signals = long_sig.sum()
    if n_signals > 0:
        returns_12h = []
        returns_24h = []
        max_fwd_profit = []
        sig_indices = df.index[long_sig].tolist()
        for idx in sig_indices:
            if idx + 24 < len(df):
                entry_price = df.loc[idx, 'close']
                future_prices = df.loc[idx+1:idx+24, 'high']
                future_closes = df.loc[idx+1:idx+24, 'close']
                max_fwd_profit.append((future_prices.max() - entry_price) / entry_price)
                returns_12h.append((df.loc[min(idx+12, len(df)-1), 'close'] - entry_price) / entry_price)
                returns_24h.append((df.loc[min(idx+24, len(df)-1), 'close'] - entry_price) / entry_price)
        
        avg_max = np.mean(max_fwd_profit) * 100 if max_fwd_profit else 0
        pct_profitable = np.mean([r > 0 for r in returns_24h]) * 100 if returns_24h else 0
        print(f"{pair:18s} | Signals: {n_signals:4d} | 24h Win%: {pct_profitable:5.1f}% | Avg Max 24h Upside: {avg_max:5.2f}%")
