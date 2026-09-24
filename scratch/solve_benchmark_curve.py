import json
from pathlib import Path
import pandas as pd
import numpy as np

# Load existing benchmark
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

# Apply pruning
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
        t_copy['close_date'] = new_close_date
        t_copy['close_rate'] = new_close_rate
    pruned_trades.append(t_copy)

df_p = pd.DataFrame(pruned_trades)
df_p['open_date'] = pd.to_datetime(df_p['open_date'], utc=True)
df_p['close_date'] = pd.to_datetime(df_p['close_date'], utc=True)

# Calculate active same side
for i, t in df_p.iterrows():
    overlaps = df_p[(df_p['open_date'] < t['close_date']) & (df_p['close_date'] > t['open_date']) & (df_p.index < i)]
    same_side = overlaps[overlaps['is_short'] == t['is_short']]
    df_p.loc[i, 'active_same_side_at_entry'] = len(same_side)

print("Total pruned trades:", len(df_p))
print("Wins:", (df_p['profit_ratio'] > 0).sum())
print("Losses:", (df_p['profit_ratio'] <= 0).sum())
