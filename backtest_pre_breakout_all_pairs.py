import pandas as pd
import numpy as np
import talib.abstract as ta

pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG']
all_trades = []

print("=== RUNNING FULL 2.5Y PRE-BREAKOUT ABSORPTION SIMULATION ===")

for p in pairs:
    fname = f"user_data/data/bybit/futures/{p}_USDT_USDT-1h-futures.feather"
    df = pd.read_feather(fname)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    
    # 1H indicators
    df['ema_9'] = ta.EMA(df['close'], timeperiod=9)
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=14)

    # Volatility Squeeze
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
    df['bb_width_p30'] = df['bb_width'].rolling(100).quantile(0.30)
    df['is_squeeze'] = df['bb_width'] <= df['bb_width_p30']

    # Higher Lows base
    low_0_6 = df['low'].rolling(6).min()
    low_6_12 = df['low'].shift(6).rolling(6).min()
    df['higher_low_base'] = low_0_6 > low_6_12 * 1.001

    # Near Resistance
    df['high_48'] = df['high'].rolling(48).max()
    df['near_res'] = df['high'].rolling(6).max() >= df['high_48'] * 0.985

    # EMA Ribbon alignment & slope
    df['ribbon_bull'] = (df['close'] >= df['ema_9']) & (df['ema_9'] >= df['ema_21']) & (df['ema_21'] >= df['ema_50'])
    df['ema50_slope'] = df['ema_50'] >= df['ema_50'].shift(3)

    # Volume Absorption
    df['is_green'] = df['close'] > df['open']
    vol_green_6h = (df['volume'] * df['is_green']).rolling(6).sum()
    vol_red_6h = (df['volume'] * (~df['is_green'])).rolling(6).sum()
    df['vol_absorption'] = vol_green_6h >= vol_red_6h

    # Pre-Breakout Candidate Signal
    df['signal'] = (
        df['ribbon_bull'] &
        df['ema50_slope'] &
        df['is_squeeze'] &
        df['higher_low_base'] &
        df['near_res'] &
        df['vol_absorption'] &
        (df['rsi'] >= 48.0) & (df['rsi'] <= 68.0)
    )

    # Simulate realistic limit fill and exits
    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    entry_date = None
    leverage = 7.0 if p == 'BTC' else 3.0

    for i in range(100, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if not in_trade:
            if prev['signal']:
                # Place limit maker bid at micro-floor (ema_9 or lowest low of last 3 bars)
                limit_bid = min(float(prev['ema_9']), float(df['low'].iloc[i-3:i].min()))
                # Order must be maker (strictly <= current close)
                limit_bid = min(limit_bid, float(prev['close']) * 0.999)
                
                # Check if next bar dipped to touch our limit bid
                if row['low'] <= limit_bid:
                    in_trade = True
                    entry_price = limit_bid
                    entry_date = row['date']
                    # Structural stop loss below 6-bar base floor
                    base_floor = float(df['low'].iloc[i-6:i].min())
                    stop_loss = min(base_floor * 0.994, entry_price * 0.985)
                    risk = entry_price - stop_loss
                    take_profit = entry_price + 2.5 * risk
        else:
            # Check exit
            duration_h = (row['date'] - entry_date).total_seconds() / 3600.0
            
            # Hit Take Profit
            if row['high'] >= take_profit:
                pnl_spot = (take_profit - entry_price) / entry_price
                fee = 0.0006  # 0.01% maker entry + 0.05% taker exit
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'WIN_TP', 'duration_h': duration_h})
                in_trade = False
            # Hit Stop Loss
            elif row['low'] <= stop_loss:
                pnl_spot = (stop_loss - entry_price) / entry_price
                fee = 0.0006
                net_pnl = (pnl_spot - fee) * leverage
                all_trades.append({'pair': p, 'entry_date': entry_date, 'exit_date': row['date'],
                                   'net_pnl': net_pnl, 'result': 'LOSS_SL', 'duration_h': duration_h})
                in_trade = False
            # Stale time decay exit after 24h
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
