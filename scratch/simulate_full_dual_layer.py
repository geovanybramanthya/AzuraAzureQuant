import sys
import json
import zipfile
from pathlib import Path
import pandas as pd
import numpy as np

# Load Layer 1 trades (Golden Checkpoint HypeTuned_TP45 on 8 pairs)
freq_dir = Path("C:/Users/geova/Downloads/FREQTRADE")
zip_path = freq_dir / "user_data/backtest_results/backtest-result-2026-09-19_20-10-40.zip"

print("Loading Layer 1 Freqtrade Core trades...")
with zipfile.ZipFile(zip_path) as z:
    d = json.loads(z.read('backtest-result-2026-09-19_20-10-40.json').decode('utf-8'))
    layer1_trades = d['strategy']['HypeTuned_TP45']['trades']

print(f"Layer 1: {len(layer1_trades)} trades loaded.")

# Load Layer 2 AI candidate signals
with open("scratch/ai_candidates_8pairs.json", "r") as f:
    ai_candidates = json.load(f)

print(f"Layer 2: {len(ai_candidates)} AI candidate signals loaded.")

# Define clusters
clusters = {
    'major': ['BTC/USDT:USDT', 'ETH/USDT:USDT'],
    'alt': ['SOL/USDT:USDT', 'ADA/USDT:USDT', 'DOGE/USDT:USDT', 'LINK/USDT:USDT'],
    'momentum': ['HYPE/USDT:USDT'],
    'defensive': ['PAXG/USDT:USDT']
}

def get_cluster(pair):
    for c_name, c_pairs in clusters.items():
        if pair in c_pairs:
            return c_name
    return 'alt'

# Prepare unified chronological event stream
all_events = []
exit_slip = 0.0005  # 5 bps execution friction

for t in layer1_trades:
    lev = float(t.get('leverage', 3.0) or 3.0)
    p_ratio = float(t.get('profit_ratio', 0.0)) - (exit_slip * lev)
    all_events.append({
        'time': pd.to_datetime(t['open_date'], utc=True),
        'close_time': pd.to_datetime(t['close_date'], utc=True),
        'open_date': str(t['open_date'])[:19],
        'close_date': str(t['close_date'])[:19],
        'pair': t['pair'],
        'profit_ratio': p_ratio,
        'open_rate': float(t.get('open_rate', 0.0)),
        'close_rate': float(t.get('close_rate', 0.0)),
        'exit_reason': t.get('exit_reason', 'roi'),
        'enter_tag': t.get('enter_tag', 'long_pullback_alpha'),
        'is_short': bool(t.get('is_short', False)),
        'leverage': lev,
        'is_ai': False,
        'is_squeeze': False,
        'cluster': get_cluster(t['pair'])
    })

# Sample AI candidate signals with realistic miss rate (25% missed due to resting limit non-fill)
rng = np.random.default_rng(185)
for s in ai_candidates:
    if rng.random() < 0.25:
        continue
    lev = float(s['leverage'])
    p_ratio = float(s['profit_ratio']) - (exit_slip * lev)
    all_events.append({
        'time': pd.to_datetime(s['open_date'], utc=True),
        'close_time': pd.to_datetime(s['close_date'], utc=True),
        'open_date': str(s['open_date'])[:19],
        'close_date': str(s['close_date'])[:19],
        'pair': s['pair'],
        'profit_ratio': p_ratio,
        'open_rate': s['open_rate'],
        'close_rate': s['close_rate'],
        'exit_reason': s['exit_reason'],
        'enter_tag': s['enter_tag'],
        'is_short': False,
        'leverage': lev,
        'is_ai': True,
        'is_squeeze': s.get('is_squeeze', False),
        'cluster': get_cluster(s['pair'])
    })

# Sort events chronologically
all_events.sort(key=lambda x: x['time'])
print(f"Total chronological candidates: {len(all_events)}")

def run_simulation(compounding=True, max_slots=4, max_ai_slots=1):
    wallet = 1000.0
    peak_wallet = 1000.0
    max_dd = 0.0
    active_trades = []
    executed_trades = []
    last_trade_closed_time = pd.to_datetime('2024-01-09 00:00:00', utc=True)
    
    # Drawdown tracking
    dd_start_time = None
    max_dd_duration_days = 0
    
    for ev in all_events:
        t = ev['time']
        
        # Settle finished trades
        surviving = []
        for tr in active_trades:
            if tr['close_time'] <= t:
                wallet += tr['profit_abs']
                if wallet > peak_wallet:
                    peak_wallet = wallet
                dd = (peak_wallet - wallet) / peak_wallet if peak_wallet > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                tr['wallet_after'] = wallet
                executed_trades.append(tr)
                if tr['close_time'] > last_trade_closed_time:
                    last_trade_closed_time = tr['close_time']
            else:
                surviving.append(tr)
        active_trades = surviving
        
        # Portfolio Slots Check
        active_pairs = [tr['pair'] for tr in active_trades]
        active_clusters = [tr['cluster'] for tr in active_trades]
        ai_active_count = sum(1 for tr in active_trades if tr['is_ai'])
        core_active_count = sum(1 for tr in active_trades if not tr['is_ai'])
        
        # Prevent same pair simultaneous duplicate
        if ev['pair'] in active_pairs:
            continue
            
        if not ev['is_ai']:
            # Layer 1: Core Strategy
            # Can enter if total active trades < max_slots
            if len(active_trades) < max_slots:
                if compounding:
                    stake = (wallet / float(max_slots)) * 0.95
                else:
                    stake = (1000.0 / float(max_slots)) * 0.95
                ev_copy = dict(ev)
                ev_copy['stake_amount'] = stake
                ev_copy['profit_abs'] = stake * ev['profit_ratio']
                active_trades.append(ev_copy)
        else:
            # Layer 2: AI Supervisor
            # Conditions:
            # 1. AI active count < max_ai_slots
            # 2. Total active trades < max_slots
            # 3. Idle duration condition:
            idle_h = (t - last_trade_closed_time).total_seconds() / 3600.0
            req_idle = 12.0 if ev['is_squeeze'] else 18.0
            
            # Pre-breakout coiling can enter if cluster has no active trades and idle_h >= 6h
            if ai_active_count < max_ai_slots and len(active_trades) < max_slots:
                # Also avoid overloading the same cluster
                if ev['cluster'] not in active_clusters or ev['cluster'] in ['alt']:
                    if idle_h >= req_idle or (ev['is_squeeze'] and idle_h >= 6.0):
                        if compounding:
                            stake = (wallet / float(max_slots)) * 0.95
                        else:
                            stake = (1000.0 / float(max_slots)) * 0.95
                        ev_copy = dict(ev)
                        ev_copy['stake_amount'] = stake
                        ev_copy['profit_abs'] = stake * ev['profit_ratio']
                        active_trades.append(ev_copy)

    # Settle remaining active trades
    for tr in active_trades:
        wallet += tr['profit_abs']
        if wallet > peak_wallet:
            peak_wallet = wallet
        dd = (peak_wallet - wallet) / peak_wallet if peak_wallet > 0 else 0
        if dd > max_dd:
            max_dd = dd
        tr['wallet_after'] = wallet
        executed_trades.append(tr)
        
    executed_trades.sort(key=lambda x: x['close_time'])
    return executed_trades, wallet, max_dd

print("\n--- RUNNING SIMULATION 1: CONSERVATIVE ISOLATED MARGIN (NO COMPOUNDING) ---")
exec_no_comp, wallet_no_comp, dd_no_comp = run_simulation(compounding=False, max_slots=4)
print(f"Total Trades: {len(exec_no_comp)}")
print(f"Final Wallet: ${wallet_no_comp:,.2f} | Net Profit: {((wallet_no_comp-1000)/1000)*100:,.2f}% | Max DD: {dd_no_comp*100:.2f}%")

print("\n--- RUNNING SIMULATION 2: DYNAMIC EXPONENTIAL COMPOUNDING (4 SLOTS) ---")
exec_comp4, wallet_comp4, dd_comp4 = run_simulation(compounding=True, max_slots=4)
print(f"Total Trades: {len(exec_comp4)}")
print(f"Final Wallet: ${wallet_comp4:,.2f} | Net Profit: {((wallet_comp4-1000)/1000)*100:,.2f}% | Max DD: {dd_comp4*100:.2f}%")

print("\n--- RUNNING SIMULATION 3: DYNAMIC EXPONENTIAL COMPOUNDING (3 SLOTS - MODEL C SPEC) ---")
exec_comp3, wallet_comp3, dd_comp3 = run_simulation(compounding=True, max_slots=3)
print(f"Total Trades: {len(exec_comp3)}")
print(f"Final Wallet: ${wallet_comp3:,.2f} | Net Profit: {((wallet_comp3-1000)/1000)*100:,.2f}% | Max DD: {dd_comp3*100:.2f}%")

# Generate detailed audit statistics on exec_comp4
trades_df = pd.DataFrame(exec_comp4)
trades_df['win'] = trades_df['profit_ratio'] > 0
wins = trades_df[trades_df['win']]
losses = trades_df[~trades_df['win']]

wr = len(wins) / len(trades_df) * 100
pf = (wins['profit_abs'].sum()) / abs(losses['profit_abs'].sum())

print("\n--- DETAILED AUDIT (4 SLOTS DYNAMIC COMPOUNDING) ---")
print(f"Winrate: {wr:.2f}% ({len(wins)} wins / {len(losses)} losses)")
print(f"Profit Factor: {pf:.2f}")

# Breakdown by Layer
l1_trades = trades_df[~trades_df['is_ai']]
l2_trades = trades_df[trades_df['is_ai']]
print(f"\nLayer 1 (Quant Core): {len(l1_trades)} trades | Winrate: {(len(l1_trades[l1_trades['win']])/len(l1_trades))*100:.2f}% | Total Profit: ${l1_trades['profit_abs'].sum():,.2f}")
print(f"Layer 2 (AI Supervisor): {len(l2_trades)} trades | Winrate: {(len(l2_trades[l2_trades['win']])/len(l2_trades))*100:.2f}% | Total Profit: ${l2_trades['profit_abs'].sum():,.2f}")

# Breakdown by Pair
print("\nBreakdown by Pair:")
for p, grp in trades_df.groupby('pair'):
    p_wr = (len(grp[grp['win']]) / len(grp)) * 100
    p_pnl = grp['profit_abs'].sum()
    print(f"  {p:16s} : {len(grp):3d} trades | WR: {p_wr:5.1f}% | Net PnL: ${p_pnl:10,.2f}")

# Breakdown by Exit Reason
print("\nBreakdown by Exit Reason:")
for er, grp in trades_df.groupby('exit_reason'):
    er_wr = (len(grp[grp['win']]) / len(grp)) * 100
    print(f"  {er:24s} : {len(grp):3d} trades | WR: {er_wr:5.1f}% | Net PnL: ${grp['profit_abs'].sum():10,.2f}")

# Save full results to json
output_results = {
    'summary_4slots': {
        'total_trades': len(exec_comp4),
        'trades_per_day': round(len(exec_comp4) / 975.0, 2),
        'winrate_pct': round(wr, 2),
        'total_wins': len(wins),
        'total_losses': len(losses),
        'profit_factor': round(pf, 2),
        'initial_balance': 1000.0,
        'final_balance': round(wallet_comp4, 2),
        'total_profit_abs': round(wallet_comp4 - 1000.0, 2),
        'total_profit_pct': round(((wallet_comp4 - 1000.0) / 1000.0) * 100.0, 2),
        'max_drawdown_pct': round(dd_comp4 * 100.0, 2),
    },
    'summary_no_compounding': {
        'total_trades': len(exec_no_comp),
        'trades_per_day': round(len(exec_no_comp) / 975.0, 2),
        'winrate_pct': round((len([t for t in exec_no_comp if t['profit_ratio'] > 0]) / len(exec_no_comp)) * 100.0, 2),
        'final_balance': round(wallet_no_comp, 2),
        'total_profit_pct': round(((wallet_no_comp - 1000.0) / 1000.0) * 100.0, 2),
        'max_drawdown_pct': round(dd_no_comp * 100.0, 2),
    },
    'summary_3slots': {
        'total_trades': len(exec_comp3),
        'trades_per_day': round(len(exec_comp3) / 975.0, 2),
        'winrate_pct': round((len([t for t in exec_comp3 if t['profit_ratio'] > 0]) / len(exec_comp3)) * 100.0, 2),
        'final_balance': round(wallet_comp3, 2),
        'total_profit_pct': round(((wallet_comp3 - 1000.0) / 1000.0) * 100.0, 2),
        'max_drawdown_pct': round(dd_comp3 * 100.0, 2),
    }
}

with open("scratch/full_backtest_audit_results.json", "w") as f:
    json.dump(output_results, f, indent=2)

print("\nSaved scratch/full_backtest_audit_results.json successfully!")
