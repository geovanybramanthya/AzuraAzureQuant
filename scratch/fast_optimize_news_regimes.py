import json
import pandas as pd
import numpy as np
import talib.abstract as ta
from pathlib import Path

# 1. Load Baseline Trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)

baseline_trades = [dict(t, is_news=False) for t in bench_data["trades"]]
print(f"Loaded {len(baseline_trades)} baseline trades.")

# 2. Pre-load 1H and 4H Data for 8 Whitelist Pairs ONCE
data_dir = Path("user_data/data/bybit/futures")
pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG', 'HYPE']

candles_dict = {}
for p in pairs:
    fn_1h = data_dir / f"{p}_USDT_USDT-1h-futures.feather"
    fn_4h = data_dir / f"{p}_USDT_USDT-4h-futures.feather"
    if fn_1h.exists() and fn_4h.exists():
        df = pd.read_feather(fn_1h)
        df_4h = pd.read_feather(fn_4h)
        
        # 4H Macro
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
        
        df['bb_mid'] = df['close'].rolling(20).mean()
        df['bb_std'] = df['close'].rolling(20).std()
        df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
        df['is_squeeze'] = df['bb_width'] <= df['bb_width'].rolling(50).quantile(0.40)
        
        candles_dict[p] = df

print("Cached 1H/4H data for all 8 pairs in memory.")

# 3. Fast News Candidates Generator
def generate_news_candidates_cached(
    vol_mult=2.5,
    thrust_mult=1.4,
    rsi_long_min=50.0,
    rsi_long_max=65.0,
    dist_res_min=0.020,
    tp1_r=1.0,
    tp2_r=2.2,
    be_r=0.15,
    sl_pct=0.015,
    rapid_invalidation_h=6,
    rapid_invalidation_spot=-0.008,
    max_hold_h=12,
    include_shorts=False,
    rsi_short_min=38.0,
    rsi_short_max=50.0,
    dist_sup_min=0.020
):
    news_trades = []
    
    for p in pairs:
        df = candles_dict[p]
        lev = 7.0 if p == 'BTC' else 3.0
        
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
        
        cond_long = (
            (df['volume'] >= df['vol_mean'] * vol_mult) &
            ((df['close'] - df['open']) >= thrust_mult * df['atr']) &
            (df['macro_bull'] == True) &
            (df['is_squeeze'] | (df['adx_4h'] < 26.0)) &
            (df['rsi'] >= rsi_long_min) & (df['rsi'] <= rsi_long_max) &
            (dist_to_res >= dist_res_min) &
            (df['close'] > df['ema_9'])
        )
        
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
            news_trades.append({
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
            
        if include_shorts and p != 'BTC':
            dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']
            cond_short = (
                (df['volume'] >= df['vol_mean'] * vol_mult) &
                ((df['open'] - df['close']) >= thrust_mult * df['atr']) &
                (df['macro_bear'] == True) &
                (df['is_squeeze'] | (df['adx_4h'] < 26.0)) &
                (df['rsi'] >= rsi_short_min) & (df['rsi'] <= rsi_short_max) &
                (dist_to_sup >= dist_sup_min) &
                (df['close'] < df['ema_9'])
            )
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
                    if not tp1_hit and c_row['low'] <= tp1:
                        tp1_hit = True
                        pnl_tp1 = (entry_p - tp1) / entry_p
                        stop_loss = entry_p - be_r * risk
                    if tp1_hit and c_row['low'] <= tp2:
                        pnl_tp2 = (entry_p - tp2) / entry_p
                        exit_reason = 'news_tp2_runner_hit'
                        exit_date = c_row['date']
                        break
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
                news_trades.append({
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
                
    return news_trades

# 4. Fast Portfolio Simulator
w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def simulate_portfolio_fast(news_trades, stake_scale_news=0.60):
    all_trades = baseline_trades + news_trades
    df_all = pd.DataFrame(all_trades)
    df_all['open_date'] = pd.to_datetime(df_all['open_date'], utc=True)
    df_all['close_date'] = pd.to_datetime(df_all['close_date'], utc=True)
    df_all = df_all.sort_values('open_date').reset_index(drop=True)
    
    for i, t in df_all.iterrows():
        overlaps = df_all[(df_all['open_date'] < t['close_date']) & (df_all['close_date'] > t['open_date']) & (df_all.index < i)]
        same_side = overlaps[overlaps['is_short'] == t['is_short']]
        df_all.loc[i, 'active_same_side_at_entry'] = len(same_side)
        df_all.loc[i, 'active_total_at_entry'] = len(overlaps)
        
    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    executed = []
    
    for idx in range(len(df_all)):
        r = df_all.iloc[idx]
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
    winrate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    pf = gross_w / gross_l if gross_l > 0 else 99.0
    net_profit = wallet - 1000.0
    trades_per_day = round(total_trades / 988.0, 2)
    news_exec = int(df_exec['is_news'].sum())
    
    return {
        'total_trades': total_trades,
        'wins': int(wins),
        'losses': int(losses),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'net_profit_usdt': round(net_profit, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(pf, 2),
        'trades_per_day': trades_per_day,
        'news_executed': news_exec
    }

# Run Grid Optimization
print("\n" + "="*80)
print("RUNNING PARAMETER GRID OPTIMIZATION FOR LONG NEWS CATALYSTS")
print("="*80)

results = []
for vol_m in [2.2, 2.5, 2.8]:
    for thrust_m in [1.2, 1.4, 1.6]:
        for tp2 in [2.0, 2.2, 2.5]:
            for dist_res in [0.015, 0.020]:
                nt = generate_news_candidates_cached(
                    vol_mult=vol_m,
                    thrust_mult=thrust_m,
                    tp2_r=tp2,
                    dist_res_min=dist_res,
                    include_shorts=False
                )
                df_nt = pd.DataFrame(nt)
                if len(df_nt) == 0: continue
                n_wr = (df_nt['profit_ratio'] > 0).mean() * 100
                n_gw = df_nt[df_nt['profit_ratio'] > 0]['profit_ratio'].sum()
                n_gl = abs(df_nt[df_nt['profit_ratio'] <= 0]['profit_ratio'].sum())
                n_pf = n_gw / n_gl if n_gl > 0 else 99.0
                
                # Portfolio simulation at 0.60x stake
                p_res = simulate_portfolio_fast(nt, stake_scale_news=0.60)
                results.append({
                    'vol_m': vol_m, 'thrust_m': thrust_m, 'tp2': tp2, 'dist_res': dist_res,
                    'news_total': len(df_nt), 'news_wr': round(n_wr, 1), 'news_pf': round(n_pf, 2),
                    'port_trades': p_res['total_trades'], 'port_wr': p_res['winrate_pct'],
                    'port_wallet': p_res['final_wallet'], 'port_dd': p_res['max_drawdown_pct'],
                    'port_pf': p_res['profit_factor'], 'news_exec': p_res['news_executed']
                })

df_res = pd.DataFrame(results)
df_res = df_res.sort_values('port_wallet', ascending=False)
print(df_res.head(10).to_string(index=False))

# Now test Stake Scaling for top configuration
top = df_res.iloc[0]
print(f"\nTOP CONFIGURATION: Vol={top['vol_m']}x, Thrust={top['thrust_m']}x, TP2={top['tp2']}R, DistRes={top['dist_res']}")
top_nt = generate_news_candidates_cached(
    vol_mult=top['vol_m'],
    thrust_mult=top['thrust_m'],
    tp2_r=top['tp2'],
    dist_res_min=top['dist_res'],
    include_shorts=False
)

print("\n--- STAKE SCALING SENSITIVITY TEST ---")
for scale in [0.0, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00]:
    s_res = simulate_portfolio_fast(top_nt, stake_scale_news=scale)
    print(f"Stake {scale:4.2f}x: Port Trades = {s_res['total_trades']} (News: {s_res['news_executed']}) | WR = {s_res['winrate_pct']}% | Wallet = ${s_res['final_wallet']:11,.2f} | MaxDD = {s_res['max_drawdown_pct']}% | PF = {s_res['profit_factor']}")

# Save results
with open("scratch/news_optimization_summary.json", "w") as f:
    json.dump(df_res.to_dict(orient="records"), f, indent=2)
print("\nSaved scratch/news_optimization_summary.json successfully.")
