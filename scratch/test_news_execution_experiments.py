import json
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import talib.abstract as ta

# 1. Load Baseline Benchmark Trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)

baseline_trades = bench_data["trades"]
print(f"Loaded {len(baseline_trades)} baseline trades.")

# 2. Load 1H and 4H Data for 8 Whitelist Pairs
data_dir = Path("user_data/data/bybit/futures")
pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG', 'HYPE']
pair_syms = {p: f"{p}/USDT:USDT" for p in pairs}

candles_1h = {}
candles_4h = {}
for p in pairs:
    fn_1h = data_dir / f"{p}_USDT_USDT-1h-futures.feather"
    fn_4h = data_dir / f"{p}_USDT_USDT-4h-futures.feather"
    if fn_1h.exists() and fn_4h.exists():
        df1 = pd.read_feather(fn_1h)
        df4 = pd.read_feather(fn_4h)
        df1['date'] = pd.to_datetime(df1['date'], utc=True)
        df4['date'] = pd.to_datetime(df4['date'], utc=True)
        
        # 4H indicators
        df4['ema_50'] = ta.EMA(df4['close'], timeperiod=50)
        df4['ema_200'] = ta.EMA(df4['close'], timeperiod=200)
        df4['macro_bull'] = (df4['close'] > df4['ema_50']) & (df4['ema_50'] > df4['ema_200'])
        df4['macro_bear'] = (df4['close'] < df4['ema_50']) & (df4['ema_50'] < df4['ema_200'])
        
        # Merge 4H macro to 1H
        df1 = pd.merge_asof(
            df1.sort_values('date'),
            df4[['date', 'macro_bull', 'macro_bear']].sort_values('date'),
            on='date',
            direction='backward'
        )
        
        # 1H indicators
        df1['vol_mean'] = df1['volume'].rolling(20).mean()
        df1['atr'] = ta.ATR(df1, timeperiod=14)
        df1['rsi'] = ta.RSI(df1, timeperiod=14)
        df1['ema_9'] = ta.EMA(df1, timeperiod=9)
        df1['ema_21'] = ta.EMA(df1, timeperiod=21)
        df1['ema_50'] = ta.EMA(df1, timeperiod=50)
        df1['rolling_high_48'] = df1['high'].rolling(48).max()
        df1['rolling_low_48'] = df1['low'].rolling(48).min()
        df1['rolling_low_12'] = df1['low'].rolling(12).min()
        
        # Volatility squeeze
        df1['bb_mid'] = df1['close'].rolling(20).mean()
        df1['bb_std'] = df1['close'].rolling(20).std()
        df1['bb_width'] = (df1['bb_std'] * 4.0) / df1['bb_mid']
        df1['is_squeeze'] = df1['bb_width'] < df1['bb_width'].rolling(50).quantile(0.35)
        
        candles_1h[pair_syms[p]] = df1
        candles_4h[pair_syms[p]] = df4

print(f"Loaded 1H and 4H candles for {len(candles_1h)} pairs.")

# 3. Formulate Candidate News Catalysts Under Different Restriction Regimes
def generate_news_candidates(regime_mode: str):
    candidates = []
    
    for p in pairs:
        pair_sym = pair_syms[p]
        df = candles_1h[pair_sym]
        lev = 7.0 if p == 'BTC' else 3.0
        
        # Baseline Volume & Price Displacement News Shock
        bull_shock = (df['volume'] >= df['vol_mean'] * 2.2) & ((df['close'] - df['open']) >= 1.5 * df['atr'])
        bear_shock = (df['volume'] >= df['vol_mean'] * 2.2) & ((df['open'] - df['close']) >= 1.5 * df['atr'])
        
        if regime_mode == "unrestricted":
            # Any volume shock triggers entry at close of shock candle
            eligible_long = bull_shock
            eligible_short = bear_shock & (p != 'BTC')
        elif regime_mode == "polarity_only":
            # Polarity filter: Long requires positive return shock, Short requires negative return shock
            eligible_long = bull_shock & ((df['close'] - df['open']) / df['open'] >= 0.015)
            eligible_short = bear_shock & ((df['open'] - df['close']) / df['open'] >= 0.015) & (p != 'BTC')
        elif regime_mode == "technical_corroborated":
            # Technical gates: 4H Macro, RSI bounds, Resistance clearance
            dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
            long_tech = (
                bull_shock &
                (df['macro_bull'] == True) &
                (df['rsi'] >= 48.0) & (df['rsi'] <= 68.0) &
                (dist_to_res >= 0.012)
            )
            dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']
            short_tech = (
                bear_shock &
                (df['macro_bear'] == True) &
                (df['rsi'] >= 32.0) & (df['rsi'] <= 52.0) &
                (dist_to_sup >= 0.012) &
                (p != 'BTC')
            )
            eligible_long = long_tech
            eligible_short = short_tech
        elif regime_mode in ["multi_gate", "fully_optimized"]:
            # Institutional Multi-Gate:
            # 1. Macro 4H confirmation
            # 2. RSI anti-chasing corridor [46.0, 66.0] for Long, [34.0, 54.0] for Short
            # 3. Room to run (distance to resistance >= 1.5%)
            # 4. Pullback maker entry (limit bid at EMA9 or lower wick midpoint)
            dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
            dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']
            
            eligible_long = (
                bull_shock &
                (df['macro_bull'] == True) &
                (df['rsi'] >= 46.0) & (df['rsi'] <= 66.0) &
                (dist_to_res >= 0.015) &
                (df['close'] >= df['ema_21'] * 0.998)
            )
            eligible_short = (
                bear_shock &
                (df['macro_bear'] == True) &
                (df['rsi'] >= 34.0) & (df['rsi'] <= 54.0) &
                (dist_to_sup >= 0.015) &
                (df['close'] <= df['ema_21'] * 1.002) &
                (p != 'BTC')
            )
            
        # Simulate execution for eligible long signals
        for idx in df.index[eligible_long]:
            if idx + 24 >= len(df): continue
            candle = df.loc[idx]
            
            if regime_mode in ["unrestricted", "polarity_only"]:
                # Market taker entry at close
                entry_p = candle['close']
                entry_date = candle['date']
                stop_loss = entry_p * 0.970
                take_profit = entry_p * 1.050
            else:
                # Maker limit entry at pullback (EMA9 or 30% wick)
                limit_bid = min(candle['ema_9'], candle['close'] - 0.25 * (candle['high'] - candle['low']))
                limit_bid = min(limit_bid, candle['close'] * 0.9985)
                # Next candle must touch limit bid
                next_c = df.loc[idx+1]
                if next_c['low'] > limit_bid:
                    continue  # Limit order expired unfilled
                entry_p = limit_bid
                entry_date = next_c['date']
                base_floor = df.loc[idx-3:idx, 'low'].min()
                stop_loss = min(base_floor * 0.992, entry_p * 0.985)
                risk = entry_p - stop_loss
                take_profit = entry_p + 2.0 * risk
                
            # Trade lifecycle simulation
            dur_max = 24
            pnl = 0.0
            exit_reason = 'news_time_cutoff'
            exit_date = None
            
            for step in range(1, dur_max + 1):
                cur_idx = idx + 1 + step
                if cur_idx >= len(df): break
                c_row = df.loc[cur_idx]
                dur_h = step
                
                # Check TP
                if c_row['high'] >= take_profit:
                    pnl = (take_profit - entry_p) / entry_p
                    exit_reason = 'news_tp_hit'
                    exit_date = c_row['date']
                    break
                # Check SL
                if c_row['low'] <= stop_loss:
                    pnl = (stop_loss - entry_p) / entry_p
                    exit_reason = 'news_sl_hit'
                    exit_date = c_row['date']
                    break
                    
                # Pruning in fully_optimized mode
                if regime_mode == "fully_optimized":
                    cur_spot = (c_row['close'] - entry_p) / entry_p
                    if dur_h >= 6 and cur_spot <= -0.010:
                        pnl = cur_spot
                        exit_reason = 'news_rapid_invalidation_6h'
                        exit_date = c_row['date']
                        break
                    if dur_h >= 12 and cur_spot <= -0.006:
                        pnl = cur_spot
                        exit_reason = 'news_stale_prune_12h'
                        exit_date = c_row['date']
                        break
            else:
                if cur_idx < len(df):
                    c_row = df.loc[cur_idx]
                    pnl = (c_row['close'] - entry_p) / entry_p
                    exit_date = c_row['date']
                    exit_reason = 'news_24h_cutoff'
                    
            fee = 0.0006
            net_pnl = (pnl - fee) * lev
            candidates.append({
                'pair': pair_sym,
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or entry_date)[:19],
                'profit_ratio': round(float(net_pnl), 6),
                'open_rate': round(float(entry_p), 4),
                'close_rate': round(float(entry_p * (1.0 + pnl)), 4),
                'exit_reason': exit_reason,
                'enter_tag': 'ai_news_catalyst_long',
                'is_short': False,
                'leverage': lev,
                'is_ai': True,
                'is_news': True
            })
            
        # Eligible Short signals
        for idx in df.index[eligible_short]:
            if idx + 24 >= len(df): continue
            candle = df.loc[idx]
            
            if regime_mode in ["unrestricted", "polarity_only"]:
                entry_p = candle['close']
                entry_date = candle['date']
                stop_loss = entry_p * 1.030
                take_profit = entry_p * 0.950
            else:
                limit_ask = max(candle['ema_9'], candle['close'] + 0.25 * (candle['high'] - candle['low']))
                limit_ask = max(limit_ask, candle['close'] * 1.0015)
                next_c = df.loc[idx+1]
                if next_c['high'] < limit_ask:
                    continue
                entry_p = limit_ask
                entry_date = next_c['date']
                base_ceiling = df.loc[idx-3:idx, 'high'].max()
                stop_loss = max(base_ceiling * 1.008, entry_p * 1.015)
                risk = stop_loss - entry_p
                take_profit = entry_p - 2.0 * risk
                
            dur_max = 24
            pnl = 0.0
            exit_reason = 'news_time_cutoff'
            exit_date = None
            
            for step in range(1, dur_max + 1):
                cur_idx = idx + 1 + step
                if cur_idx >= len(df): break
                c_row = df.loc[cur_idx]
                dur_h = step
                
                # Check TP for Short
                if c_row['low'] <= take_profit:
                    pnl = (entry_p - take_profit) / entry_p
                    exit_reason = 'news_tp_hit'
                    exit_date = c_row['date']
                    break
                # Check SL for Short
                if c_row['high'] >= stop_loss:
                    pnl = (entry_p - stop_loss) / entry_p
                    exit_reason = 'news_sl_hit'
                    exit_date = c_row['date']
                    break
                    
                if regime_mode == "fully_optimized":
                    cur_spot = (entry_p - c_row['close']) / entry_p
                    if dur_h >= 6 and cur_spot <= -0.010:
                        pnl = cur_spot
                        exit_reason = 'news_rapid_invalidation_6h'
                        exit_date = c_row['date']
                        break
                    if dur_h >= 12 and cur_spot <= -0.006:
                        pnl = cur_spot
                        exit_reason = 'news_stale_prune_12h'
                        exit_date = c_row['date']
                        break
            else:
                if cur_idx < len(df):
                    c_row = df.loc[cur_idx]
                    pnl = (entry_p - c_row['close']) / entry_p
                    exit_date = c_row['date']
                    exit_reason = 'news_24h_cutoff'
                    
            fee = 0.0006
            net_pnl = (pnl - fee) * lev
            candidates.append({
                'pair': pair_sym,
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or entry_date)[:19],
                'profit_ratio': round(float(net_pnl), 6),
                'open_rate': round(float(entry_p), 4),
                'close_rate': round(float(entry_p * (1.0 - pnl)), 4),
                'exit_reason': exit_reason,
                'enter_tag': 'ai_news_catalyst_short',
                'is_short': True,
                'leverage': lev,
                'is_ai': True,
                'is_news': True
            })
            
    return candidates

# 4. Unified Portfolio Simulation Engine
def run_portfolio_simulation(news_candidates: list, stake_scale_news: float = 1.0):
    # Combine baseline trades and news candidates
    all_trades = [dict(t, is_news=False) for t in baseline_trades] + news_candidates
    df_all = pd.DataFrame(all_trades)
    df_all['open_date'] = pd.to_datetime(df_all['open_date'], utc=True)
    df_all['close_date'] = pd.to_datetime(df_all['close_date'], utc=True)
    df_all = df_all.sort_values('open_date').reset_index(drop=True)
    
    # Portfolio slot dispatch simulation (Max 3 slots concurrent)
    # Check concurrent overlap
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
    
    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    
    executed = []
    
    for idx in range(len(df_all)):
        r = df_all.iloc[idx]
        
        # Slot limit: maximum 3 concurrent open positions
        if r['active_total_at_entry'] >= 3:
            continue
            
        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0
        
        # Base slot allocation
        base_stake = (wallet / 3.0) * 0.95 * brake
        
        # If directional co-risk >= 2 positions, throttle 0.70x
        if r['active_same_side_at_entry'] >= 2:
            base_stake *= 0.70
            
        # If news trade, apply volatility haircut
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
    winrate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0
    profit_factor = gross_w / gross_l if gross_l > 0 else 99.0
    net_profit = wallet - 1000.0
    net_profit_pct = (net_profit / 1000.0) * 100.0
    trades_per_day = round(total_trades / 988.0, 2)
    
    return {
        'total_trades': total_trades,
        'wins': int(wins),
        'losses': int(losses),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'net_profit_usdt': round(net_profit, 2),
        'net_profit_pct': round(net_profit_pct, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(profit_factor, 2),
        'trades_per_day': trades_per_day,
        'news_trades_executed': int(df_exec['is_news'].sum()) if 'is_news' in df_exec else 0
    }

print("\n=========================================================================================")
print("RUNNING ITERATIVE CALIBRATION EXPERIMENTS FOR FUNDAMENTAL NEWS EXECUTION AUTHORITY")
print("=========================================================================================")

# Experiment 0: Baseline Checkpoint
print("Running Exp 0: Baseline Checkpoint (No News Execution)...")
res_0 = run_portfolio_simulation([], stake_scale_news=0.0)

# Experiment 1: Unrestricted News Execution
print("Running Exp 1: Unrestricted News Execution (Naïve Taker Shock Trading)...")
cand_1 = generate_news_candidates("unrestricted")
print(f"Generated {len(cand_1)} unrestricted news candidates.")
res_1 = run_portfolio_simulation(cand_1, stake_scale_news=1.0)

# Experiment 2: Polarity Threshold Only
print("Running Exp 2: News Execution with Polarity Threshold Only...")
cand_2 = generate_news_candidates("polarity_only")
print(f"Generated {len(cand_2)} polarity-only news candidates.")
res_2 = run_portfolio_simulation(cand_2, stake_scale_news=1.0)

# Experiment 3: Technical Corroborated News Execution
print("Running Exp 3: News Execution with Technical Corroboration Gates...")
cand_3 = generate_news_candidates("technical_corroborated")
print(f"Generated {len(cand_3)} technical-corroborated news candidates.")
res_3 = run_portfolio_simulation(cand_3, stake_scale_news=1.0)

# Experiment 4: Multi-Gate Defense (Technical + Sizing Haircut 0.60x)
print("Running Exp 4: News Execution with Multi-Gate Defense (0.60x Sizing)...")
cand_4 = generate_news_candidates("multi_gate")
print(f"Generated {len(cand_4)} multi-gate news candidates.")
res_4 = run_portfolio_simulation(cand_4, stake_scale_news=0.60)

# Experiment 5: Fully Optimized Multi-Gate + Rapid Invalidation Pruning
print("Running Exp 5: Fully Optimized Multi-Gate + Rapid Invalidation Pruning (0.60x Sizing)...")
cand_5 = generate_news_candidates("fully_optimized")
print(f"Generated {len(cand_5)} fully-optimized news candidates.")
res_5 = run_portfolio_simulation(cand_5, stake_scale_news=0.60)

experiments = [
    ("Exp 0: Baseline Checkpoint (Config 08)", res_0),
    ("Exp 1: Unrestricted News Execution (Naïve)", res_1),
    ("Exp 2: Sentiment Polarity Gate Only", res_2),
    ("Exp 3: Technical Corroborated News Execution", res_3),
    ("Exp 4: Multi-Gate Defense (0.60x Stake)", res_4),
    ("Exp 5: Fully Optimized Multi-Gate + Rapid Prune", res_5)
]

print("\n" + "="*115)
print(f"{'Experiment Architecture':45s} | {'Trades':6s} | {'Freq/d':6s} | {'WinRate':7s} | {'Net Profit ($)':14s} | {'Max DD':7s} | {'PF':4s}")
print("="*115)
for name, r in experiments:
    print(
        f"{name:45s} | "
        f"{r['total_trades']:6d} | "
        f"{r['trades_per_day']:6.2f} | "
        f"{r['winrate_pct']:6.2f}% | "
        f"${r['net_profit_usdt']:13,.2f} | "
        f"{r['max_drawdown_pct']:6.2f}% | "
        f"{r['profit_factor']:4.2f}"
    )
print("="*115)

# Save results for reporting
out_res = {name: r for name, r in experiments}
with open("scratch/news_execution_experiments_results.json", "w", encoding="utf-8") as f:
    json.dump(out_res, f, indent=2)
print("\nSaved scratch/news_execution_experiments_results.json successfully!")
