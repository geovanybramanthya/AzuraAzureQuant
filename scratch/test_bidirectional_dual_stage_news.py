import pandas as pd
import numpy as np
import talib.abstract as ta
from pathlib import Path

data_dir = Path("user_data/data/bybit/futures")
pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG', 'HYPE']

def generate_bidirectional_news_trades(
    vol_mult=2.5,
    thrust_mult=1.4,
    rsi_long_min=50.0,
    rsi_long_max=65.0,
    rsi_short_min=35.0,
    rsi_short_max=50.0,
    dist_res_min=0.020,
    dist_sup_min=0.020,
    tp1_r=1.0,
    tp2_r=2.2,
    be_r=0.15,
    sl_pct=0.015,
    rapid_invalidation_h=6,
    rapid_invalidation_spot=-0.008,
    max_hold_h=12
):
    trades = []
    
    for p in pairs:
        fn_1h = data_dir / f"{p}_USDT_USDT-1h-futures.feather"
        fn_4h = data_dir / f"{p}_USDT_USDT-4h-futures.feather"
        if not fn_1h.exists() or not fn_4h.exists():
            continue
            
        df = pd.read_feather(fn_1h)
        df_4h = pd.read_feather(fn_4h)
        
        # 4H Macro indicators
        df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
        df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)
        df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
        df_4h['macro_bull'] = (df_4h['close'] > df_4h['ema_50']) & (df_4h['ema_50'] > df_4h['ema_200'])
        df_4h['macro_bear'] = (df_4h['close'] < df_4h['ema_50']) & (df_4h['ema_50'] < df_4h['ema_200'])
        
        df = pd.merge_asof(
            df.sort_values('date'),
            df_4h[['date', 'macro_bull', 'macro_bear', 'adx']].rename(columns={'adx': 'adx_4h'}).sort_values('date'),
            on='date',
            direction='backward'
        )
        
        df['vol_mean'] = df['volume'].rolling(20).mean()
        df['atr'] = ta.ATR(df, timeperiod=14)
        df['rsi'] = ta.RSI(df, timeperiod=14)
        df['ema_9'] = ta.EMA(df, timeperiod=9)
        df['rolling_high_48'] = df['high'].rolling(48).max()
        df['rolling_low_48'] = df['low'].rolling(48).min()
        
        # Bollinger Squeeze
        df['bb_mid'] = df['close'].rolling(20).mean()
        df['bb_std'] = df['close'].rolling(20).std()
        df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
        df['is_squeeze'] = df['bb_width'] <= df['bb_width'].rolling(50).quantile(0.40)
        
        lev = 7.0 if p == 'BTC' else 3.0
        
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
        dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']
        
        cond_long = (
            (df['volume'] >= df['vol_mean'] * vol_mult) &
            ((df['close'] - df['open']) >= thrust_mult * df['atr']) &
            (df['macro_bull'] == True) &
            (df['is_squeeze'] | (df['adx_4h'] < 26.0)) &
            (df['rsi'] >= rsi_long_min) & (df['rsi'] <= rsi_long_max) &
            (dist_to_res >= dist_res_min) &
            (df['close'] > df['ema_9'])
        )
        
        cond_short = (
            (df['volume'] >= df['vol_mean'] * vol_mult) &
            ((df['open'] - df['close']) >= thrust_mult * df['atr']) &
            (df['macro_bear'] == True) &
            (df['is_squeeze'] | (df['adx_4h'] < 26.0)) &
            (df['rsi'] >= rsi_short_min) & (df['rsi'] <= rsi_short_max) &
            (dist_to_sup >= dist_sup_min) &
            (df['close'] < df['ema_9']) &
            (p != 'BTC')
        )
        
        # 1. Simulate Longs
        for idx in df.index[cond_long]:
            if idx + max_hold_h + 1 >= len(df): continue
            candle = df.loc[idx]
            
            limit_bid = candle['close'] - 0.20 * (candle['high'] - candle['low'])
            limit_bid = min(limit_bid, candle['close'] * 0.9985)
            
            next_c = df.loc[idx+1]
            if next_c['low'] > limit_bid: continue
            
            entry_p = limit_bid
            entry_date = next_c['date']
            stop_loss = entry_p * (1.0 - sl_pct)
            risk = entry_p - stop_loss
            tp1 = entry_p + tp1_r * risk
            tp2 = entry_p + tp2_r * risk
            
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'news_{max_hold_h}h_cutoff'
            exit_date = None
            
            for step in range(1, max_hold_h + 1):
                cur_idx = idx + 1 + step
                if cur_idx >= len(df): break
                c_row = df.loc[cur_idx]
                
                if not tp1_hit and c_row['high'] >= tp1:
                    tp1_hit = True
                    pnl_tp1 = (tp1 - entry_p) / entry_p
                    stop_loss = entry_p + be_r * risk
                    
                if tp1_hit and c_row['high'] >= tp2:
                    pnl_tp2 = (tp2 - entry_p) / entry_p
                    exit_reason = 'news_tp2_runner_hit'
                    exit_date = c_row['date']
                    break
                    
                if c_row['low'] <= stop_loss:
                    if tp1_hit:
                        pnl_tp2 = (stop_loss - entry_p) / entry_p
                        exit_reason = 'news_be_stopped_after_tp1'
                    else:
                        pnl_tp1 = (stop_loss - entry_p) / entry_p
                        pnl_tp2 = (stop_loss - entry_p) / entry_p
                        exit_reason = 'news_sl_hit'
                    exit_date = c_row['date']
                    break
                    
                cur_spot = (c_row['close'] - entry_p) / entry_p
                if step >= rapid_invalidation_h and cur_spot <= rapid_invalidation_spot and not tp1_hit:
                    pnl_tp1 = cur_spot
                    pnl_tp2 = cur_spot
                    exit_reason = f'news_invalidation_{rapid_invalidation_h}h'
                    exit_date = c_row['date']
                    break
            else:
                if cur_idx < len(df):
                    c_row = df.loc[cur_idx]
                    cur_spot = (c_row['close'] - entry_p) / entry_p
                    if not tp1_hit:
                        pnl_tp1 = cur_spot
                        pnl_tp2 = cur_spot
                    else:
                        pnl_tp2 = cur_spot
                    exit_date = c_row['date']
                    
            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            fee = 0.0006
            net_pnl = (tot_spot_pnl - fee) * lev
            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': 'long',
                'is_short': False,
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or next_c['date'])[:19],
                'profit_ratio': round(float(net_pnl), 6),
                'open_rate': round(float(entry_p), 4),
                'close_rate': round(float(entry_p * (1.0 + tot_spot_pnl)), 4),
                'exit_reason': exit_reason,
                'enter_tag': 'ai_news_catalyst_long',
                'leverage': lev,
                'is_ai': True,
                'is_news': True
            })
            
        # 2. Simulate Shorts
        for idx in df.index[cond_short]:
            if idx + max_hold_h + 1 >= len(df): continue
            candle = df.loc[idx]
            
            limit_ask = candle['close'] + 0.20 * (candle['high'] - candle['low'])
            limit_ask = max(limit_ask, candle['close'] * 1.0015)
            
            next_c = df.loc[idx+1]
            if next_c['high'] < limit_ask: continue
            
            entry_p = limit_ask
            entry_date = next_c['date']
            stop_loss = entry_p * (1.0 + sl_pct)
            risk = stop_loss - entry_p
            tp1 = entry_p - tp1_r * risk
            tp2 = entry_p - tp2_r * risk
            
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'news_{max_hold_h}h_cutoff'
            exit_date = None
            
            for step in range(1, max_hold_h + 1):
                cur_idx = idx + 1 + step
                if cur_idx >= len(df): break
                c_row = df.loc[cur_idx]
                
                # Check Short TP1
                if not tp1_hit and c_row['low'] <= tp1:
                    tp1_hit = True
                    pnl_tp1 = (entry_p - tp1) / entry_p
                    stop_loss = entry_p - be_r * risk
                    
                # Check Short TP2
                if tp1_hit and c_row['low'] <= tp2:
                    pnl_tp2 = (entry_p - tp2) / entry_p
                    exit_reason = 'news_tp2_runner_hit'
                    exit_date = c_row['date']
                    break
                    
                # Check Short SL
                if c_row['high'] >= stop_loss:
                    if tp1_hit:
                        pnl_tp2 = (entry_p - stop_loss) / entry_p
                        exit_reason = 'news_be_stopped_after_tp1'
                    else:
                        pnl_tp1 = (entry_p - stop_loss) / entry_p
                        pnl_tp2 = (entry_p - stop_loss) / entry_p
                        exit_reason = 'news_sl_hit'
                    exit_date = c_row['date']
                    break
                    
                cur_spot = (entry_p - c_row['close']) / entry_p
                if step >= rapid_invalidation_h and cur_spot <= rapid_invalidation_spot and not tp1_hit:
                    pnl_tp1 = cur_spot
                    pnl_tp2 = cur_spot
                    exit_reason = f'news_invalidation_{rapid_invalidation_h}h'
                    exit_date = c_row['date']
                    break
            else:
                if cur_idx < len(df):
                    c_row = df.loc[cur_idx]
                    cur_spot = (entry_p - c_row['close']) / entry_p
                    if not tp1_hit:
                        pnl_tp1 = cur_spot
                        pnl_tp2 = cur_spot
                    else:
                        pnl_tp2 = cur_spot
                    exit_date = c_row['date']
                    
            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            fee = 0.0006
            net_pnl = (tot_spot_pnl - fee) * lev
            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': 'short',
                'is_short': True,
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or next_c['date'])[:19],
                'profit_ratio': round(float(net_pnl), 6),
                'open_rate': round(float(entry_p), 4),
                'close_rate': round(float(entry_p * (1.0 - tot_spot_pnl)), 4),
                'exit_reason': exit_reason,
                'enter_tag': 'ai_news_catalyst_short',
                'leverage': lev,
                'is_ai': True,
                'is_news': True
            })
            
    return trades

if __name__ == "__main__":
    trades = generate_bidirectional_news_trades()
    df_t = pd.DataFrame(trades)
    print(f"Total Bidirectional News Trades: {len(df_t)}")
    longs = df_t[df_t['side'] == 'long']
    shorts = df_t[df_t['side'] == 'short']
    
    print(f"Long Trades: {len(longs)} | Wins: {(longs['profit_ratio'] > 0).sum()} | WinRate: {(longs['profit_ratio'] > 0).mean()*100:.2f}%")
    print(f"Short Trades: {len(shorts)} | Wins: {(shorts['profit_ratio'] > 0).sum()} | WinRate: {(shorts['profit_ratio'] > 0).mean()*100:.2f}%")
    
    wins = (df_t['profit_ratio'] > 0).sum()
    total = len(df_t)
    wr = wins / total * 100.0 if total > 0 else 0
    gross_w = df_t[df_t['profit_ratio'] > 0]['profit_ratio'].sum()
    gross_l = abs(df_t[df_t['profit_ratio'] <= 0]['profit_ratio'].sum())
    pf = gross_w / gross_l if gross_l > 0 else 99.0
    print(f"Combined Standalone: WinRate = {wr:.2f}% | Profit Factor = {pf:.2f}")
    print("\nExit Reason Breakdown:")
    for er, grp in df_t.groupby('exit_reason'):
        print(f"  {er:30s}: {len(grp):2d} trades | WinRate = {(grp['profit_ratio'] > 0).mean()*100:5.1f}%")
