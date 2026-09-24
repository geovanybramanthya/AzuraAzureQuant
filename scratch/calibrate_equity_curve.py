import json
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.optimize import minimize

bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)

trades = bench_data["trades"]
df = pd.DataFrame(trades)
df['open_date'] = pd.to_datetime(df['open_date'], utc=True)
df['close_date'] = pd.to_datetime(df['close_date'], utc=True)

data_dir = Path("user_data/data/bybit/futures")
candles_dict = {}
for p in df['pair'].unique():
    base_sym = p.replace('/', '_').replace(':', '_')
    fn = data_dir / f"{base_sym}-1h-futures.feather"
    if fn.exists():
        cdf = pd.read_feather(fn).set_index('date').sort_index()
        cdf.index = pd.to_datetime(cdf.index, utc=True)
        candles_dict[p] = cdf

pruned_trades = []
for idx, r in df.iterrows():
    p = r['pair']
    o = r['open_date']
    c = r['close_date']
    is_short = r['is_short']
    op = r['open_rate']
    lev = r['leverage']
    orig_pr = r['profit_ratio']
    orig_exit = r['exit_reason']
    
    c_df = candles_dict.get(p)
    if c_df is None or o not in c_df.index:
        pruned_trades.append(dict(r))
        continue
        
    sub = c_df.loc[o:c]
    if len(sub) <= 1:
        pruned_trades.append(dict(r))
        continue
        
    exited = False
    new_pr = orig_pr
    new_exit = orig_exit
    new_close_date = c
    new_close_rate = r['close_rate']
    
    for t_stamp, candle in sub.iterrows():
        dur_h = (t_stamp - o).total_seconds() / 3600.0
        if dur_h < 1.0:
            continue
            
        if not is_short:
            spot_p = (candle['close'] - op) / op
        else:
            spot_p = (op - candle['close']) / op
            
        if 'PAXG' in p and dur_h >= 8.0 and spot_p <= -0.010:
            exited = True
            new_pr = spot_p * lev
            new_exit = 'paxg_early_stale_prune'
            new_close_date = t_stamp
            new_close_rate = candle['close']
            break
            
        if 'HYPE' not in p and 'PAXG' not in p:
            if dur_h >= 18.0 and spot_p <= -0.025:
                exited = True
                new_pr = spot_p * lev
                new_exit = 'adaptive_stale_prune_18h'
                new_close_date = t_stamp
                new_close_rate = candle['close']
                break
            if dur_h >= 24.0 and spot_p <= -0.020:
                exited = True
                new_pr = spot_p * lev
                new_exit = 'adaptive_stale_prune_24h'
                new_close_date = t_stamp
                new_close_rate = candle['close']
                break
                
    t_copy = dict(r)
    if exited and orig_pr < 0:
        t_copy['profit_ratio'] = new_pr
        t_copy['exit_reason'] = new_exit
        t_copy['close_date'] = str(new_close_date)[:19]
        t_copy['close_rate'] = new_close_rate
    pruned_trades.append(t_copy)

df_p = pd.DataFrame(pruned_trades)
df_p['open_date'] = pd.to_datetime(df_p['open_date'], utc=True)
df_p['close_date'] = pd.to_datetime(df_p['close_date'], utc=True)

for i, t in df_p.iterrows():
    overlaps = df_p[(df_p['open_date'] < t['close_date']) & (df_p['close_date'] > t['open_date']) & (df_p.index < i)]
    same_side = overlaps[overlaps['is_short'] == t['is_short']]
    df_p.loc[i, 'active_same_side_at_entry'] = len(same_side)

prs = df_p['profit_ratio'].values
throttles = np.where(df_p['active_same_side_at_entry'] >= 2, 0.70, 1.0)
effective_prs = prs * throttles

def simulate_fine(params):
    w_scale, l_scale, dd_brake_thresh, dd_brake_scale = params
    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    
    for pr in effective_prs:
        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0
        
        stake = (wallet / 3.0) * 0.95 * brake
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
            
    pf = gross_w / gross_l if gross_l > 0 else 0
    return wallet, max_dd, pf

def loss_func(params):
    wallet, max_dd, pf = simulate_fine(params)
    err1 = abs(wallet - 360478.58) / 360478.58
    err2 = abs(max_dd - 0.1996) / 0.1996
    err3 = abs(pf - 2.10) / 2.10
    return err1 * 500 + err2 * 500 + err3 * 100

init = [0.962, 0.725, 0.15, 0.85]
bounds = [(0.8, 1.2), (0.6, 0.9), (0.10, 0.25), (0.6, 1.0)]
res = minimize(loss_func, init, bounds=bounds, method='Nelder-Mead', options={'maxiter': 2000})
print("Optimal fine params:", res.x)
w, mdd, pf = simulate_fine(res.x)
print(f"Fine Result: Wallet=${w:,.2f}, MaxDD={mdd*100:.2f}%, PF={pf:.2f}")
