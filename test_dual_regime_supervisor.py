import ccxt
import pandas as pd
import talib.abstract as ta
import time

pairs = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'ADA/USDT:USDT', 'DOGE/USDT:USDT', 'LINK/USDT:USDT', 'PAXG/USDT:USDT']
exchange = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})

print('--- Testing Dual-Regime Scanner with RSI <= 89.0 ---')

results = []
for pair in pairs:
    time.sleep(0.3)
    o1 = exchange.fetch_ohlcv(pair, '1h', limit=100)
    o4 = exchange.fetch_ohlcv(pair, '4h', limit=250)
    df_1h = pd.DataFrame(o1, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_1h['date'] = pd.to_datetime(df_1h['timestamp'], unit='ms', utc=True)
    df_4h = pd.DataFrame(o4, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_4h['date'] = pd.to_datetime(df_4h['timestamp'], unit='ms', utc=True)

    df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
    df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)

    df_1h['rolling_low_48'] = df_1h['low'].rolling(48).min()
    df_1h['rolling_high_48'] = df_1h['high'].rolling(48).max()
    df_1h['rsi'] = ta.RSI(df_1h['close'], timeperiod=14)
    df_1h['vol_mean_20'] = df_1h['volume'].rolling(20).mean()
    df_1h['ema_9'] = ta.EMA(df_1h['close'], timeperiod=9)
    df_1h['ema_21'] = ta.EMA(df_1h['close'], timeperiod=21)

    latest_4h_adx = float(df_4h['adx'].iloc[-2])
    c_4h = df_4h['close'].iloc[-2]
    e50_4h = df_4h['ema_50'].iloc[-2]
    e200_4h = df_4h['ema_200'].iloc[-2]
    macro_bull_4h = bool((c_4h > e50_4h) and (e50_4h > e200_4h)) if not pd.isna(e200_4h) else False

    candle = df_1h.iloc[-2]
    prev_resistance = float(df_1h['rolling_high_48'].iloc[-3])
    current_price = float(df_1h['close'].iloc[-1])
    dec = 4 if current_price < 10 else 2

    broke_out = candle['close'] >= prev_resistance * 0.998 or candle['high'] >= prev_resistance
    vol_expansion = candle['volume'] > candle['vol_mean_20'] * 1.05
    rsi_momentum = 48.0 <= candle['rsi'] <= 89.0
    ema_stack = df_1h['ema_9'].iloc[-2] > df_1h['ema_21'].iloc[-2]

    is_regime2_valid = macro_bull_4h and latest_4h_adx >= 19.0 and broke_out and vol_expansion and rsi_momentum and ema_stack

    limit_price = round(min(max(prev_resistance, float(df_1h['ema_9'].iloc[-2])), current_price * 0.9985), dec)
    stop_loss = round(min(limit_price * 0.986, prev_resistance * 0.988), dec)
    risk = limit_price - stop_loss
    take_profit = round(limit_price + 2.0 * risk, dec)

    results.append({
        'pair': pair,
        'macro_bull': macro_bull_4h,
        'adx': round(latest_4h_adx, 1),
        'broke_out': broke_out,
        'vol': vol_expansion,
        'rsi': round(candle['rsi'], 1),
        'ema_stk': ema_stack,
        'regime2': is_regime2_valid,
        'limit_price': limit_price if is_regime2_valid else None,
        'current_price': current_price,
        'stop_loss': stop_loss if is_regime2_valid else None,
        'take_profit': take_profit if is_regime2_valid else None
    })

res_df = pd.DataFrame(results)
print(res_df.to_string())
