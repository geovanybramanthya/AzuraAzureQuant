import pandas as pd
import numpy as np
import talib.abstract as ta

# Load BTC 1h as the Lead Asset
df_btc = pd.read_feather("user_data/data/bybit/futures/BTC_USDT_USDT-1h-futures.feather")
df_btc['date'] = pd.to_datetime(df_btc['date'], utc=True)
df_btc['btc_ema_21'] = ta.EMA(df_btc['close'], timeperiod=21)
df_btc['btc_ema_50'] = ta.EMA(df_btc['close'], timeperiod=50)
df_btc['btc_rsi'] = ta.RSI(df_btc['close'], timeperiod=14)
df_btc['btc_bull_impulse'] = (df_btc['close'] > df_btc['btc_ema_21']) & (df_btc['btc_rsi'] >= 52.0)

# Load BTC 4h for macro
df_btc_4h = pd.read_feather("user_data/data/bybit/futures/BTC_USDT_USDT-4h-futures.feather")
df_btc_4h['date'] = pd.to_datetime(df_btc_4h['date'], utc=True)
df_btc_4h['ema_50'] = ta.EMA(df_btc_4h['close'], timeperiod=50)
df_btc_4h['ema_200'] = ta.EMA(df_btc_4h['close'], timeperiod=200)
df_btc_4h['adx'] = ta.ADX(df_btc_4h, timeperiod=14)
df_btc_4h['btc_macro_bull'] = (df_btc_4h['close'] > df_btc_4h['ema_50']) & (df_btc_4h['ema_50'] > df_btc_4h['ema_200']) & (df_btc_4h['adx'] >= 19.0)

# Resample 4H to 1H forward fill
df_btc = pd.merge_asof(df_btc.sort_values('date'), 
                       df_btc_4h[['date', 'btc_macro_bull']].sort_values('date'), 
                       on='date', direction='backward')

alt_pairs = ['ETH', 'SOL', 'ADA', 'DOGE', 'LINK']
all_trades = []

print("=== TESTING CROSS-ASSET LEAD-LAG PRE-BREAKOUT ABSORPTION ===")

for p in alt_pairs:
    fname = f"user_data/data/bybit/futures/{p}_USDT_USDT-1h-futures.feather"
    df = pd.read_feather(fname)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    
    # Merge BTC lead indicators
    df = pd.merge(df, df_btc[['date', 'btc_bull_impulse', 'btc_macro_bull']], on='date', how='left')
    df['btc_macro_bull'] = df['btc_macro_bull'].fillna(False)
    df['btc_bull_impulse'] = df['btc_bull_impulse'].fillna(False)

    # 1H indicators for altcoin
    df['ema_9'] = ta.EMA(df['close'], timeperiod=9)
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=14)

    # Volatility Squeeze
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
    df['bb_width_p35'] = df['bb_width'].rolling(100).quantile(0.35)
    df['is_squeeze'] = df['bb_width'] <= df['bb_width_p35']

    # Higher Lows base
    low_0_6 = df['low'].rolling(6).min()
    low_6_12 = df['low'].shift(6).rolling(6).min()
    df['higher_low_base'] = low_0_6 > low_6_12 * 1.002

    # Near Resistance: within 2% of 48h High
    df['high_48'] = df['high'].rolling(48).max()
    df['near_res'] = df['high'].rolling(6).max() >= df['high_48'] * 0.980

    # Altcoin Ribbon alignment
    df['ribbon_bull'] = (df['close'] >= df['ema_21']) & (df['ema_21'] >= df['ema_50'])

    # Volume Absorption
    df['is_green'] = df['close'] > df['open']
    vol_green_6h = (df['volume'] * df['is_green']).rolling(6).sum()
    vol_red_6h = (df['volume'] * (~df['is_green'])).rolling(6).sum()
    df['vol_absorption'] = vol_green_6h >= vol_red_6h

    # CROSS-ASSET LEAD-LAG SIGNAL:
    # Alt is coiled in pre-breakout squeeze AND BTC is in confirmed Macro Bull Impulse!
    df['signal'] = (
        df['btc_macro_bull'] &
        df['btc_bull_impulse'] &
        df['ribbon_bull'] &
        df['is_squeeze'] &
        df['higher_low_base'] &
        df['near_res'] &
        df['vol_absorption'] &
        (df['rsi'] >= 48.0) & (df['rsi'] <= 68.0)
    )

    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    entry_date = None
    leverage = 3.0

    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if not in_trade:
            if prev['signal']:
                limit_bid = min(float(prev['ema_9']), float(df['low'].iloc[i-3:i].min()))
                limit_bid = min(limit_bid, float(prev['close']) * 0.9985)
                
                if row['low'] <= limit_bid:
                    in_trade = True
                    entry_price = limit_bid
                    entry_date = row['date']
                    base_floor = float(df['low'].iloc[i-6:i].min())
                    stop_loss = min(base_floor * 0.992, entry_price * 0.982)
                    risk = entry_price - stop_loss
                    take_profit = entry_price + 2.8 * risk
        else:
            duration_h = (row['date'] - entry_date).total_seconds() / 3600.0
            
            if row['high'] >= take_profit:
                pnl_spot = (take_profit - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'WIN_TP', 'duration_h': duration_h})
                in_trade = False
            elif row['low'] <= stop_loss:
                pnl_spot = (stop_loss - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'LOSS_SL', 'duration_h': duration_h})
                in_trade = False
            elif duration_h >= 24.0:
                pnl_spot = (row['close'] - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                res = 'STALE_WIN' if net_pnl > 0 else 'STALE_LOSS'
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': res, 'duration_h': duration_h})
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
