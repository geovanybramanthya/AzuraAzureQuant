import json
import pandas as pd
import numpy as np
from pathlib import Path

# 1. Load baseline trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)

baseline_trades = [dict(t, is_news=False) for t in bench_data["trades"]]

# 2. Load dual-stage news trades from scratch/test_dual_stage_news.py
import sys
sys.path.append('.')
from scratch.test_dual_stage_news import test_dual_stage_news
import scratch.test_dual_stage_news as dsn

# Capture trades
news_trades = []
for p in dsn.pairs:
    fn_1h = dsn.data_dir / f"{p}_USDT_USDT-1h-futures.feather"
    fn_4h = dsn.data_dir / f"{p}_USDT_USDT-4h-futures.feather"
    df = pd.read_feather(fn_1h)
    df_4h = pd.read_feather(fn_4h)
    
    import talib.abstract as ta
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
        if next_c['low'] > limit_bid: continue
        
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
            
            if not tp1_hit and c_row['high'] >= tp1:
                tp1_hit = True
                pnl_tp1 = (tp1 - entry_p) / entry_p
                stop_loss = entry_p + 0.15 * risk
                
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
            'pair': f"{p}/USDT:USDT",
            'open_date': str(entry_date)[:19],
            'close_date': str(exit_date or next_c['date'])[:19],
            'profit_ratio': round(float(net_pnl), 6),
            'open_rate': round(float(entry_p), 4),
            'close_rate': round(float(entry_p * (1.0 + tot_spot_pnl)), 4),
            'exit_reason': exit_reason,
            'enter_tag': 'ai_news_catalyst_long',
            'is_short': False,
            'leverage': lev,
            'is_ai': True,
            'is_news': True
        })

print(f"Loaded {len(news_trades)} refined dual-stage news trades.")

# 3. Simulate Full Portfolio
all_trades = baseline_trades + news_trades
df_all = pd.DataFrame(all_trades)
df_all['open_date'] = pd.to_datetime(df_all['open_date'], utc=True)
df_all['close_date'] = pd.to_datetime(df_all['close_date'], utc=True)
df_all = df_all.sort_values('open_date').reset_index(drop=True)

# Compute overlaps
for i, t in df_all.iterrows():
    overlaps = df_all[(df_all['open_date'] < t['close_date']) & (df_all['close_date'] > t['open_date']) & (df_all.index < i)]
    same_side = overlaps[overlaps['is_short'] == t['is_short']]
    df_all.loc[i, 'active_same_side_at_entry'] = len(same_side)
    df_all.loc[i, 'active_total_at_entry'] = len(overlaps)

# Multipliers matching Config 08
w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def simulate_portfolio(stake_scale_news=0.60):
    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    executed = []
    
    for idx in range(len(df_all)):
        r = df_all.iloc[idx]
        
        # Max 3 slots concurrent
        if r['active_total_at_entry'] >= 3:
            continue
            
        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0
        
        base_stake = (wallet / 3.0) * 0.95 * brake
        
        if r['active_same_side_at_entry'] >= 2:
            base_stake *= 0.70
            
        if r.get('is_news', False):
            stake = base_stake * stake_scale_news
        else:
            stake = base_stake
            
        pr = r['profit_ratio']
        if pr > 0:
            pnl = stake * pr * w_scale
            gross_w += pnl
        else:
            pnl = stake * pr * l_scale
            gross_l += abs(pnl)
            
        wallet += pnl
        if wallet > peak:
            peak = wallet
        dd = (peak - wallet) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            
        r_dict = dict(r)
        r_dict['profit_abs'] = pnl
        executed.append(r_dict)
        
    df_exec = pd.DataFrame(executed)
    total_trades = len(df_exec)
    wins = (df_exec['profit_ratio'] > 0).sum()
    losses = (df_exec['profit_ratio'] <= 0).sum()
    winrate = (wins / total_trades) * 100.0
    pf = gross_w / gross_l if gross_l > 0 else 99.0
    net_profit = wallet - 1000.0
    net_profit_pct = (net_profit / 1000.0) * 100.0
    trades_per_day = round(total_trades / 988.0, 2)
    news_executed = int(df_exec['is_news'].sum())
    
    return {
        'total_trades': total_trades,
        'wins': int(wins),
        'losses': int(losses),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'net_profit_usdt': round(net_profit, 2),
        'net_profit_pct': round(net_profit_pct, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(pf, 2),
        'trades_per_day': trades_per_day,
        'news_trades_executed': news_executed
    }

print("\n--- TESTING SIZING CALIBRATIONS FOR NEWS LAYER ---")
for s in [0.0, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00]:
    res = simulate_portfolio(stake_scale_news=s)
    print(f"Stake Scale {s:4.2f}x: Trades = {res['total_trades']:3d} (News: {res['news_trades_executed']:2d}) | WR = {res['winrate_pct']:5.2f}% | Final Wallet = ${res['final_wallet']:11,.2f} | Max DD = {res['max_drawdown_pct']:5.2f}% | PF = {res['profit_factor']:4.2f}")
