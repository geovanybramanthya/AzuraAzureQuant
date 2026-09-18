import pandas as pd
import numpy as np
import talib.abstract as ta

pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG']
all_trades = []

print("=== TESTING M15 QUANT ENGINE ON 2.5Y BYBIT FUTURES DATASET ===")

for p in pairs:
    fname = f"user_data/data/bybit/futures/{p}_USDT_USDT-15m-futures.feather"
    df = pd.read_feather(fname)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    
    # 15m Indicators
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['ema_200'] = ta.EMA(df['close'], timeperiod=200)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=14)
    stoch = ta.STOCH(df, fastk_period=14, slowk_period=3, slowd_period=3)
    df['slowk'] = stoch['slowk']
    df['slowd'] = stoch['slowd']
    df['vol_mean'] = df['volume'].rolling(20).mean()

    # Macro 4H trend proxy on 15m (16 periods of 15m = 4h EMA, 800 periods = 200h)
    df['ema_macro'] = ta.EMA(df['close'], timeperiod=200) # ~50h trend
    df['is_bull_macro'] = df['close'] > df['ema_macro']
    df['is_bear_macro'] = df['close'] < df['ema_macro']

    # M15 Micro-Pullback Alpha (High Frequency, High Winrate)
    # Long: Macro Bull + Dip to EMA 21 or lower + RSI in dip zone + Stoch cross
    df['long_signal'] = (
        df['is_bull_macro'] &
        (df['close'] <= df['ema_21'] * 1.002) &
        (df['close'] >= df['ema_50'] * 0.995) &
        (df['rsi'] >= 38.0) & (df['rsi'] <= 58.0) &
        (df['slowk'] > df['slowd']) & (df['slowk'].shift(1) <= df['slowd'].shift(1)) &
        (df['slowk'] < 50.0) &
        (df['volume'] > df['vol_mean'] * 0.70)
    )

    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    entry_date = None
    leverage = 7.0 if p == 'BTC' else 3.0

    for i in range(250, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if not in_trade:
            if prev['long_signal']:
                # Resting maker limit order at low of previous candle or EMA 21
                limit_bid = min(float(prev['ema_21']), float(prev['low']))
                limit_bid = min(limit_bid, float(prev['close']) * 0.999)
                
                if row['low'] <= limit_bid:
                    in_trade = True
                    entry_price = limit_bid
                    entry_date = row['date']
                    stop_loss = entry_price * (1.0 - 0.015) # 1.5% stop
                    take_profit = entry_price * (1.0 + 0.035) # 3.5% take profit (2.3:1 R:R)
        else:
            duration_m = (row['date'] - entry_date).total_seconds() / 60.0
            
            if row['high'] >= take_profit:
                pnl_spot = (take_profit - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'WIN_TP', 'duration_h': duration_m/60.0})
                in_trade = False
            elif row['low'] <= stop_loss:
                pnl_spot = (stop_loss - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'LOSS_SL', 'duration_h': duration_m/60.0})
                in_trade = False
            elif duration_m >= 720.0: # 12 hours timeout
                pnl_spot = (row['close'] - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                res = 'TIMEOUT_WIN' if net_pnl > 0 else 'TIMEOUT_LOSS'
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': res, 'duration_h': duration_m/60.0})
                in_trade = False

tdf = pd.DataFrame(all_trades)
if not tdf.empty:
    wins = len(tdf[tdf['net_pnl'] > 0])
    total = len(tdf)
    wr = wins / total * 100
    avg_pnl = tdf['net_pnl'].mean() * 100
    tot_pnl = tdf['net_pnl'].sum() * 100
    print(f"Total Trades: {total} ({total / 982:.2f} trades/day)")
    print(f"Winrate: {wr:.2f}% ({wins}/{total})")
    print(f"Average PnL per Trade: {avg_pnl:.2f}%")
    print(f"Total Cumulative Return: {tot_pnl:.2f}%")
    print("\nTrades per Pair:")
    print(tdf['pair'].value_counts())
    print("\nResult Breakdown:")
    print(tdf['result'].value_counts())
    
    # Check trades on Sep 17-18, 2026
    sep = tdf[(tdf['entry_date'] >= '2026-09-17') & (tdf['entry_date'] <= '2026-09-19')]
    print("\nTrades around Sep 17-19, 2026:")
    print(sep.to_string())
else:
    print("No trades generated.")
