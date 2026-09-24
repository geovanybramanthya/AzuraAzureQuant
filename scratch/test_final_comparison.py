import json
import pandas as pd
import numpy as np
from pathlib import Path

# Load benchmark
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)

baseline_trades = [dict(t, is_news=False) for t in bench_data["trades"]]

# Import news trades generator
import sys
sys.path.append('.')
from scratch.test_portfolio_with_dual_stage_news import news_trades

# Baseline simulation alone
df_base = pd.DataFrame(baseline_trades)
df_base['open_date'] = pd.to_datetime(df_base['open_date'], utc=True)
df_base['close_date'] = pd.to_datetime(df_base['close_date'], utc=True)

# Full combined
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

w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def eval_sim(df_in, stake_scale_news=0.60):
    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    executed = []
    
    for idx in range(len(df_in)):
        r = df_in.iloc[idx]
        if r.get('active_total_at_entry', 0) >= 3:
            continue
            
        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0
        
        base_stake = (wallet / 3.0) * 0.95 * brake
        if r.get('active_same_side_at_entry', 0) >= 2:
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
        
    df_e = pd.DataFrame(executed)
    total_trades = len(df_e)
    wins = (df_e['profit_ratio'] > 0).sum()
    losses = (df_e['profit_ratio'] <= 0).sum()
    winrate = (wins / total_trades) * 100.0
    pf = gross_w / gross_l if gross_l > 0 else 99.0
    net_p = wallet - 1000.0
    
    return {
        'total_trades': total_trades,
        'wins': int(wins),
        'losses': int(losses),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'net_profit_usdt': round(net_p, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(pf, 2),
        'news_trades': int(df_e['is_news'].sum()) if 'is_news' in df_e else 0
    }

print("=== FINAL COMPARATIVE EMPIRICAL VERIFICATION ===")
res_base = bench_data['summary']
print(f"BASELINE: Trades={bench_data['total_trades_count']} | WR={res_base['winrate_pct']}% | Final=${res_base['final_balance']:,.2f} | Max DD={res_base['max_drawdown_pct']}% | PF={res_base['profit_factor']}")

for scale in [0.50, 0.60, 0.70]:
    res_comb = eval_sim(df_all, stake_scale_news=scale)
    print(f"COMBINED + DUAL-STAGE NEWS (Stake {scale:.2f}x): Trades={res_comb['total_trades']} (News: {res_comb['news_trades']}) | WR={res_comb['winrate_pct']}% | Final=${res_comb['final_wallet']:,.2f} | Max DD={res_comb['max_drawdown_pct']}% | PF={res_comb['profit_factor']}")
