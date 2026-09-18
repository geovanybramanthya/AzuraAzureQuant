import zipfile
import json
from pathlib import Path
import pandas as pd
import numpy as np
import talib.abstract as ta
from freqtrade.strategy import merge_informative_pair

def run_simulation(
    df_v11_trades,
    opp_signals,
    missed_fill_rate=0.0,
    exit_slippage=0.0,
    idle_threshold_h=24.0,
    seed=None
):
    rng = np.random.default_rng(seed)
    
    selected_ai_signals = []
    for s in opp_signals:
        if missed_fill_rate > 0.0 and rng.random() < missed_fill_rate:
            continue
        sig = dict(s)
        if exit_slippage > 0.0:
            lev = sig['leverage']
            sig['profit_ratio'] -= (exit_slippage * lev)
        selected_ai_signals.append(sig)
        
    all_events = []
    for _, r in df_v11_trades.iterrows():
        lev = 7.0 if 'BTC' in r['pair'] else (5.0 if 'SOL' in r['pair'] else 3.0)
        p_ratio = r['profit_ratio']
        if exit_slippage > 0.0:
            p_ratio -= (exit_slippage * lev)
        all_events.append({
            'time': r['open_date'],
            'pair': r['pair'],
            'source': 'v11_native',
            'close_date': r['close_date'],
            'profit_ratio': p_ratio,
            'is_v11': True
        })
        
    for r in selected_ai_signals:
        all_events.append({
            'time': r['open_date'],
            'pair': r['pair'],
            'source': 'ai_supervisor_opportunistic',
            'close_date': r['close_date'],
            'profit_ratio': r['profit_ratio'],
            'is_v11': False
        })
        
    all_events.sort(key=lambda x: x['time'])
    
    wallet = 1000.0
    peak_wallet = 1000.0
    max_dd = 0.0
    active_trades = []
    executed_trades = []
    last_trade_closed_time = pd.to_datetime('2024-01-09 08:00:00', utc=True)
    
    for ev in all_events:
        t = ev['time']
        surviving = []
        for tr in active_trades:
            if tr['close_date'] <= t:
                wallet += tr['profit_abs']
                if wallet > peak_wallet:
                    peak_wallet = wallet
                dd = (peak_wallet - wallet) / peak_wallet
                if dd > max_dd:
                    max_dd = dd
                executed_trades.append(tr)
                if tr['close_date'] > last_trade_closed_time:
                    last_trade_closed_time = tr['close_date']
            else:
                surviving.append(tr)
        active_trades = surviving
        
        if ev['is_v11']:
            if len(active_trades) < 3:
                if ev['pair'] not in [tr['pair'] for tr in active_trades]:
                    stake = (wallet / 3.0) * 0.95
                    ev['profit_abs'] = stake * ev['profit_ratio']
                    active_trades.append(ev)
        else:
            ai_active = sum(1 for tr in active_trades if tr['source'] == 'ai_supervisor_opportunistic')
            idle_duration_h = (t - last_trade_closed_time).total_seconds() / 3600.0
            if len(active_trades) == 0 and idle_duration_h >= idle_threshold_h and ai_active == 0:
                if ev['pair'] not in [tr['pair'] for tr in active_trades]:
                    stake = (wallet / 3.0) * 0.95
                    ev['profit_abs'] = stake * ev['profit_ratio']
                    active_trades.append(ev)
                    
    for tr in active_trades:
        wallet += tr['profit_abs']
        if wallet > peak_wallet:
            peak_wallet = wallet
        dd = (peak_wallet - wallet) / peak_wallet
        if dd > max_dd:
            max_dd = dd
        executed_trades.append(tr)
        
    df_exec = pd.DataFrame(executed_trades)
    if df_exec.empty:
        return {
            'final_wallet': 1000.0,
            'profit_pct': 0.0,
            'total_trades': 0,
            'v11_trades': 0,
            'ai_trades': 0,
            'winrate': 0.0,
            'max_drawdown': 0.0,
            'trades_per_day': 0.0,
            'max_gap_days': 0.0
        }
        
    df_exec = df_exec.sort_values('time').reset_index(drop=True)
    v11_count = sum(1 for x in executed_trades if x['source'] == 'v11_native')
    ai_count = sum(1 for x in executed_trades if x['source'] == 'ai_supervisor_opportunistic')
    wins = sum(1 for x in executed_trades if x['profit_ratio'] > 0)
    days = (df_exec['time'].max() - df_exec['time'].min()).total_seconds() / 86400.0
    df_exec['gap_h'] = df_exec['time'].diff().dt.total_seconds() / 3600.0
    
    return {
        'final_wallet': round(wallet, 2),
        'profit_pct': round((wallet - 1000.0) / 1000.0 * 100.0, 2),
        'total_trades': len(df_exec),
        'v11_trades': v11_count,
        'ai_trades': ai_count,
        'winrate': round(wins / len(df_exec) * 100.0, 2),
        'max_drawdown': round(max_dd * 100.0, 2),
        'trades_per_day': round(len(df_exec) / days, 2) if days > 0 else 0.0,
        'max_gap_days': round(df_exec['gap_h'].max() / 24.0, 2) if not df_exec['gap_h'].isna().all() else 0.0
    }

def generate_ai_signals(pairs, rolling_window=48):
    pair_map = {p: p.replace('_', '/').replace('/USDT/USDT', '/USDT:USDT') for p in pairs}
    opp_signals = []
    
    for p in pairs:
        df_1h = pd.read_feather(f'user_data/data/bybit/futures/{p}-1h-futures.feather')
        df_4h = pd.read_feather(f'user_data/data/bybit/futures/{p}-4h-futures.feather')
        df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
        df_4h['date'] = pd.to_datetime(df_4h['date'], utc=True)
        df_4h['adx_4h'] = ta.ADX(df_4h, timeperiod=14)
        
        df = merge_informative_pair(df_1h, df_4h[['date', 'adx_4h']], '1h', '4h', ffill=True)
        df['rolling_low'] = df['low'].rolling(rolling_window).min()
        df['rolling_high'] = df['high'].rolling(rolling_window).max()
        df['rsi'] = ta.RSI(df, timeperiod=14)
        df['vol_mean'] = df['volume'].rolling(20).mean()
        df['is_green'] = df['close'] > df['open']
        df['body_size'] = (df['close'] - df['open']).abs()
        df['lower_wick'] = np.where(df['is_green'], df['open'] - df['low'], df['close'] - df['low'])
        range_pct = (df['rolling_high'] - df['rolling_low']) / df['rolling_low']
        
        cond = (
            (df['adx_4h_4h'] < 30.0) &
            (range_pct >= 0.035) &
            (df['low'] <= df['rolling_low'].shift(1) * 1.003) &
            (df['close'] > df['rolling_low'].shift(1)) &
            (df['rsi'] >= 32.0) & (df['rsi'] <= 46.0) &
            (df['lower_wick'] > df['body_size'] * 0.75) &
            (df['volume'] > df['vol_mean'] * 0.75)
        )
        
        lev = 7.0 if 'BTC' in p else (5.0 if 'SOL' in p else 3.0)
        
        for idx in df.index[cond].tolist():
            if idx + 48 >= len(df):
                continue
            entry_time = df.loc[idx, 'date']
            entry_price = df.loc[idx, 'low'] + 0.20 * df.loc[idx, 'lower_wick']
            pnl = 0.0
            dur_h = 0
            
            for step in range(1, 49):
                cur = idx + step
                high = df.loc[cur, 'high']
                low = df.loc[cur, 'low']
                close = df.loc[cur, 'close']
                dur_h = step
                max_gain = (high - entry_price) / entry_price
                cur_gain = (close - entry_price) / entry_price
                min_gain = (low - entry_price) / entry_price
                
                if min_gain <= -0.297:
                    pnl = -0.297
                    break
                if step <= 7 and max_gain >= 0.526:
                    pnl = 0.526
                    break
                elif 8 <= step <= 11 and max_gain >= 0.161:
                    pnl = 0.161
                    break
                elif 12 <= step <= 19 and max_gain >= 0.090:
                    pnl = 0.090
                    break
                elif step > 19 and cur_gain >= 0.011:
                    pnl = cur_gain
                    break
                elif 18 <= step < 36 and cur_gain <= -0.015:
                    pnl = -0.015
                    break
                elif step >= 36 and cur_gain < -0.010:
                    pnl = cur_gain
                    break
                elif step >= 48:
                    pnl = cur_gain
                    break
                    
            exit_time = entry_time + pd.Timedelta(hours=dur_h)
            opp_signals.append({
                'pair': pair_map[p],
                'open_date': entry_time,
                'close_date': exit_time,
                'profit_ratio': pnl * lev,
                'duration_h': dur_h,
                'leverage': lev,
                'source': 'ai_supervisor_opportunistic'
            })
            
    return pd.DataFrame(opp_signals).sort_values('open_date').to_dict('records')

def main():
    print("================================================================================")
    print("   MODEL B DUAL-LAYER HYBRID: ADVERSARIAL STRESS TEST & SENSITIVITY AUDIT")
    print("================================================================================")
    
    zip_path = 'user_data/backtest_results/backtest-result-2026-09-17_22-12-59.zip'
    with zipfile.ZipFile(zip_path) as z:
        d = json.loads(z.read('backtest-result-2026-09-17_22-12-59.json'))
        trades = d['strategy']['ApexDualAlpha_Omni_V11_OptionB']['trades']
        
    df_v11 = pd.DataFrame(trades)
    df_v11['open_date'] = pd.to_datetime(df_v11['open_date'])
    df_v11['close_date'] = pd.to_datetime(df_v11['close_date'])
    
    pairs = [
        'BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT',
        'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT',
        'PAXG_USDT_USDT'
    ]
    
    print("\n[Phase 1] Pre-computing AI Opportunistic Signals for 48h Window...")
    signals_48h = generate_ai_signals(pairs, rolling_window=48)
    print(f"Total AI Candidate Signals Generated: {len(signals_48h)}")
    
    res_ideal = run_simulation(df_v11, signals_48h, missed_fill_rate=0.0, exit_slippage=0.0, idle_threshold_h=24.0)
    print("\n--- Skenario 1: Model B Ideal (Upper Ceiling: 0% Missed Fill, 0% Slippage) ---")
    print(f"Final Wallet: ${res_ideal['final_wallet']:,.2f} USDT | Return: +{res_ideal['profit_pct']:.2f}%")
    print(f"Total Trades: {res_ideal['total_trades']} (V11: {res_ideal['v11_trades']} | AI: {res_ideal['ai_trades']})")
    print(f"Winrate: {res_ideal['winrate']:.2f}% | Max DD: {res_ideal['max_drawdown']:.2f}% | Freq: {res_ideal['trades_per_day']:.2f} trades/day | Max Gap: {res_ideal['max_gap_days']:.1f} days")
    
    print("\n--- Skenario 2: 25% Missed Fill Stress Test (100 Monte Carlo Iterations) ---")
    mc_wallets_25 = []
    mc_returns_25 = []
    mc_trades_25 = []
    mc_winrates_25 = []
    mc_dds_25 = []
    
    for i in range(100):
        res_mc = run_simulation(df_v11, signals_48h, missed_fill_rate=0.25, exit_slippage=0.0, seed=i+42)
        mc_wallets_25.append(res_mc['final_wallet'])
        mc_returns_25.append(res_mc['profit_pct'])
        mc_trades_25.append(res_mc['total_trades'])
        mc_winrates_25.append(res_mc['winrate'])
        mc_dds_25.append(res_mc['max_drawdown'])
        
    p5_w = np.percentile(mc_wallets_25, 5)
    p50_w = np.percentile(mc_wallets_25, 50)
    p95_w = np.percentile(mc_wallets_25, 95)
    
    p5_ret = np.percentile(mc_returns_25, 5)
    p50_ret = np.percentile(mc_returns_25, 50)
    p95_ret = np.percentile(mc_returns_25, 95)
    
    p50_trades = np.percentile(mc_trades_25, 50)
    p50_wr = np.percentile(mc_winrates_25, 50)
    p50_dd = np.percentile(mc_dds_25, 50)
    
    print(f"5th Percentile (Pessimistic): ${p5_w:,.2f} USDT (+{p5_ret:.2f}%)")
    print(f"Median (Realistic Expected):  ${p50_w:,.2f} USDT (+{p50_ret:.2f}%) | Trades: {int(p50_trades)} | WR: {p50_wr:.2f}% | Max DD: {p50_dd:.2f}%")
    print(f"95th Percentile (Optimistic): ${p95_w:,.2f} USDT (+{p95_ret:.2f}%)")
    
    print("\n--- Skenario 3: 25% Missed Fill + 0.05% Exit Slippage Penalty (Full Friction Floor) ---")
    mc_wallets_fric = []
    mc_returns_fric = []
    mc_dds_fric = []
    
    for i in range(100):
        res_fric = run_simulation(df_v11, signals_48h, missed_fill_rate=0.25, exit_slippage=0.0005, seed=i+100)
        mc_wallets_fric.append(res_fric['final_wallet'])
        mc_returns_fric.append(res_fric['profit_pct'])
        mc_dds_fric.append(res_fric['max_drawdown'])
        
    fric_p5 = np.percentile(mc_wallets_fric, 5)
    fric_p50 = np.percentile(mc_wallets_fric, 50)
    fric_p95 = np.percentile(mc_wallets_fric, 95)
    fric_p50_ret = np.percentile(mc_returns_fric, 50)
    fric_p50_dd = np.percentile(mc_dds_fric, 50)
    
    print(f"Friction Floor (P50 Median):  ${fric_p50:,.2f} USDT (+{fric_p50_ret:.2f}%) | Max DD: {fric_p50_dd:.2f}%")
    print(f"Friction Floor (P5 Worst):    ${fric_p5:,.2f} USDT (+{(fric_p5-1000)/10:.2f}%)")
    
    print("\n--- Skenario 4: Severe 40% Missed Fill + 0.10% Slippage (Extreme Market Adversity) ---")
    mc_wallets_ext = []
    for i in range(50):
        res_ext = run_simulation(df_v11, signals_48h, missed_fill_rate=0.40, exit_slippage=0.0010, seed=i+300)
        mc_wallets_ext.append(res_ext['final_wallet'])
    ext_p50 = np.percentile(mc_wallets_ext, 50)
    print(f"Extreme Adversity Median:     ${ext_p50:,.2f} USDT (+{(ext_p50-1000)/10:.2f}%)")
    
    print("\n[Phase 2] Parameter Sensitivity Analysis (Support Window: 36h vs 48h vs 60h)...")
    print("Computing 36h window signals...")
    signals_36h = generate_ai_signals(pairs, rolling_window=36)
    res_36h = run_simulation(df_v11, signals_36h, missed_fill_rate=0.25, exit_slippage=0.0005, seed=42)
    
    print("Computing 60h window signals...")
    signals_60h = generate_ai_signals(pairs, rolling_window=60)
    res_60h = run_simulation(df_v11, signals_60h, missed_fill_rate=0.25, exit_slippage=0.0005, seed=42)
    
    print(f"36-Hour Window (with friction): ${res_36h['final_wallet']:,.2f} USDT (+{res_36h['profit_pct']:.2f}%) | Trades: {res_36h['total_trades']} (AI: {res_36h['ai_trades']}) | WR: {res_36h['winrate']:.2f}% | Max DD: {res_36h['max_drawdown']:.2f}%")
    print(f"48-Hour Window (with friction): ${fric_p50:,.2f} USDT (+{fric_p50_ret:.2f}%) | Trades: {int(p50_trades)} | WR: {p50_wr:.2f}% | Max DD: {fric_p50_dd:.2f}%")
    print(f"60-Hour Window (with friction): ${res_60h['final_wallet']:,.2f} USDT (+{res_60h['profit_pct']:.2f}%) | Trades: {res_60h['total_trades']} (AI: {res_60h['ai_trades']}) | WR: {res_60h['winrate']:.2f}% | Max DD: {res_60h['max_drawdown']:.2f}%")
    
    print("\n================================================================================")
    print("                        ADVERSARIAL STRESS TEST COMPLETE                        ")
    print("================================================================================")

if __name__ == '__main__':
    main()
