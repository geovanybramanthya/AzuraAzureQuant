import pandas as pd
import numpy as np
import talib.abstract as ta
from pathlib import Path

data_dir = Path("user_data/data/bybit/futures")
pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG', 'HYPE']

def test_dual_stage_news():
    news_trades = []
    
    for p in pairs:
        fn_1h = data_dir / f"{p}_USDT_USDT-1h-futures.feather"
        fn_4h = data_dir / f"{p}_USDT_USDT-4h-futures.feather"
        df = pd.read_feather(fn_1h)
        df_4h = pd.read_feather(fn_4h)
        
        # 4H Macro
        df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
        df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)
        df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
        df_4h['macro_bull'] = (df_4h['close'] > df_4h['ema_50']) & (df_4h['ema_50'] > df_4h['ema_200'])
        
        df = pd.merge_asof(
            df.sort_values('date'),
            df_4h[['date', 'macro_bull', 'adx']].rename(columns={'adx': 'adx_4h'}).sort_values('date'),
            on='date',
            direction='backward'
        )
        
        df['vol_mean'] = df['volume'].rolling(20).mean()
        df['atr'] = ta.ATR(df, timeperiod=14)
        df['rsi'] = ta.RSI(df, timeperiod=14)
        df['ema_9'] = ta.EMA(df, timeperiod=9)
        df['rolling_high_48'] = df['high'].rolling(48).max()
        
        df['bb_mid'] = df['close'].rolling(20).mean()
        df['bb_std'] = df['close'].rolling(20).std()
        df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
        df['is_squeeze'] = df['bb_width'] <= df['bb_width'].rolling(50).quantile(0.40)
        
        lev = 7.0 if p == 'BTC' else 3.0
        
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
        
        cond_long = (
            (df['volume'] >= df['vol_mean'] * 2.5) &
            ((df['close'] - df['open']) >= 1.4 * df['atr']) &
            (df['macro_bull'] == True) &
            (df['is_squeeze'] | (df['adx_4h'] < 26.0)) &
            (df['rsi'] >= 50.0) & (df['rsi'] <= 65.0) &
            (dist_to_res >= 0.020) &
            (df['close'] > df['ema_9'])
        )
        
        for idx in df.index[cond_long]:
            if idx + 24 >= len(df): continue
            candle = df.loc[idx]
            
            limit_bid = candle['close'] - 0.20 * (candle['high'] - candle['low'])
            limit_bid = min(limit_bid, candle['close'] * 0.9985)
            
            next_c = df.loc[idx+1]
            if next_c['low'] > limit_bid:
                continue
                
            entry_p = limit_bid
            entry_date = next_c['date']
            stop_loss = entry_p * 0.985
            risk = entry_p - stop_loss
            
            tp1 = entry_p + 1.0 * risk
            tp2 = entry_p + 2.2 * risk
            
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = 'news_12h_cutoff'
            exit_date = None
            
            for step in range(1, 13):
                cur_idx = idx + 1 + step
                if cur_idx >= len(df): break
                c_row = df.loc[cur_idx]
                
                # Check TP1 (50% size)
                if not tp1_hit and c_row['high'] >= tp1:
                    tp1_hit = True
                    pnl_tp1 = (tp1 - entry_p) / entry_p
                    stop_loss = entry_p + 0.15 * risk  # Move stop to guaranteed small profit
                    
                # Check TP2 (remaining 50% size)
                if tp1_hit and c_row['high'] >= tp2:
                    pnl_tp2 = (tp2 - entry_p) / entry_p
                    exit_reason = 'news_tp2_runner_hit'
                    exit_date = c_row['date']
                    break
                    
                # Check Stop Loss
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
                    
                # Rapid Invalidation: 6h stagnation
                cur_spot = (c_row['close'] - entry_p) / entry_p
                if step >= 6 and cur_spot <= -0.008 and not tp1_hit:
                    pnl_tp1 = cur_spot
                    pnl_tp2 = cur_spot
                    exit_reason = 'news_invalidation_6h'
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
            news_trades.append({
                'pair': p,
                'open_date': entry_date,
                'close_date': exit_date or next_c['date'],
                'profit_ratio': net_pnl,
                'exit_reason': exit_reason
            })
            
    df_nt = pd.DataFrame(news_trades)
    total = len(df_nt)
    wins = (df_nt['profit_ratio'] > 0).sum() if total > 0 else 0
    losses = total - wins
    wr = (wins / total) * 100 if total > 0 else 0
    pf = df_nt[df_nt['profit_ratio'] > 0]['profit_ratio'].sum() / abs(df_nt[df_nt['profit_ratio'] <= 0]['profit_ratio'].sum()) if losses > 0 else 99.0
    print(f"DUAL-STAGE NEWS TRADES: Total = {total} | Wins = {wins} | Losses = {losses} | WR = {wr:.2f}% | PF = {pf:.2f}")
    for er, grp in df_nt.groupby('exit_reason'):
        e_wr = (grp['profit_ratio'] > 0).mean() * 100
        print(f"  {er:30s}: {len(grp):2d} trades | WR = {e_wr:5.1f}%")

if __name__ == "__main__":
    test_dual_stage_news()
