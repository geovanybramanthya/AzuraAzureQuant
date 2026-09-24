import sys
import json
import zipfile
from pathlib import Path
import pandas as pd
import numpy as np
import talib.abstract as ta
from freqtrade.strategy import merge_informative_pair

# Paths
freq_dir = Path("C:/Users/geova/Downloads/FREQTRADE")
zip_path = freq_dir / "user_data/backtest_results/backtest-result-2026-09-19_20-10-40.zip"
data_dir = freq_dir / "user_data/data/bybit/futures"

print("1. Loading Golden Checkpoint HypeTuned_TP45 Layer 1 trades...")
with zipfile.ZipFile(zip_path) as z:
    d = json.loads(z.read('backtest-result-2026-09-19_20-10-40.json').decode('utf-8'))
    layer1_trades = d['strategy']['HypeTuned_TP45']['trades']

print(f"Loaded {len(layer1_trades)} Layer 1 trades across 8 pairs.")

pairs = [
    'BTC_USDT_USDT', 'ETH_USDT_USDT', 'SOL_USDT_USDT',
    'ADA_USDT_USDT', 'DOGE_USDT_USDT', 'LINK_USDT_USDT',
    'PAXG_USDT_USDT', 'HYPE_USDT_USDT'
]
pair_map = {p: p.replace('_', '/').replace('/USDT/USDT', '/USDT:USDT') for p in pairs}

print("2. Loading 1H & 4H candles and computing indicators for Layer 2 AI Supervisor...")
pair_dfs = {}
for p in pairs:
    df_1h = pd.read_feather(data_dir / f"{p}-1h-futures.feather")
    df_4h = pd.read_feather(data_dir / f"{p}-4h-futures.feather")
    df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
    df_4h['date'] = pd.to_datetime(df_4h['date'], utc=True)
    
    df_4h['adx_4h'] = ta.ADX(df_4h, timeperiod=14)
    df_4h['ema_50'] = ta.EMA(df_4h, timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h, timeperiod=200)
    df_4h['macro_bear'] = (df_4h['close'] < df_4h['ema_50']) & (df_4h['ema_50'] < df_4h['ema_200'])
    df_4h['macro_bull'] = (df_4h['close'] > df_4h['ema_50']) & (df_4h['ema_50'] > df_4h['ema_200'])
    
    df = merge_informative_pair(df_1h, df_4h[['date', 'adx_4h', 'macro_bear', 'macro_bull']], '1h', '4h', ffill=True)
    df['rolling_low_48'] = df['low'].rolling(48).min()
    df['rolling_high_48'] = df['high'].rolling(48).max()
    df['rolling_low_12'] = df['low'].rolling(12).min()
    df['rolling_low_24'] = df['low'].rolling(24).min()
    df['rsi'] = ta.RSI(df, timeperiod=14)
    df['vol_mean'] = df['volume'].rolling(20).mean()
    df['ema_9'] = ta.EMA(df, timeperiod=9)
    df['ema_21'] = ta.EMA(df, timeperiod=21)
    df['is_green'] = df['close'] > df['open']
    df['body_size'] = (df['close'] - df['open']).abs()
    df['lower_wick'] = np.where(df['is_green'], df['open'] - df['low'], df['close'] - df['low'])
    df['range_pct'] = (df['rolling_high_48'] - df['rolling_low_48']) / df['rolling_low_48']
    
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 4.0) / df['bb_mid']
    df['is_squeeze'] = df['bb_width'] < df['bb_width'].rolling(50).quantile(0.35)
    pair_dfs[p] = df

print("3. Generating Layer 2 AI Strategic Supervisor maker signals...")
ai_candidate_signals = []
for p in pairs:
    df = pair_dfs[p]
    cond_long = (
        (df['adx_4h_4h'] < 30.0) &
        (df['range_pct'] >= 0.035) &
        (df['low'] <= df['rolling_low_48'].shift(1) * 1.003) &
        (df['close'] > df['rolling_low_48'].shift(1)) &
        (df['rsi'] >= 32.0) & (df['rsi'] <= 46.0) &
        (df['lower_wick'] > df['body_size'] * 0.75) &
        (df['volume'] > df['vol_mean'] * 0.75)
    )
    lev = 7.0 if 'BTC' in p else (5.0 if 'SOL' in p else 3.0)
    for idx in df.index[cond_long].tolist():
        if idx + 48 >= len(df): continue
        entry_p = df.loc[idx, 'low'] + 0.20 * df.loc[idx, 'lower_wick']
        pnl = 0.0
        dur_h = 0
        exit_reason = 'roi'
        close_p = entry_p
        
        # Max hold duration: HYPE has 72h runway, others 48h
        max_steps = 72 if 'HYPE' in p else 48
        
        for step in range(1, max_steps + 1):
            cur = idx + step
            if cur >= len(df): break
            high = df.loc[cur, 'high']
            low = df.loc[cur, 'low']
            close = df.loc[cur, 'close']
            dur_h = step
            max_gain = (high - entry_p) / entry_p
            cur_gain = (close - entry_p) / entry_p
            min_gain = (low - entry_p) / entry_p
            
            # Stop loss
            if min_gain <= -0.297:
                pnl = -0.297
                close_p = entry_p * (1.0 - 0.297 / lev)
                exit_reason = 'stop_loss'
                break
            
            # HYPE specific quick TP: +4.5% leveraged (0.015 unleveraged) after 6h
            if 'HYPE' in p:
                if step >= 6 and max_gain >= 0.015:
                    pnl = 0.015
                    close_p = entry_p * (1.0 + 0.015)
                    exit_reason = 'hype_quick_tp'
                    break
                if 48 <= dur_h < 72 and cur_gain <= -0.040:
                    pnl = -0.040
                    close_p = close
                    exit_reason = 'hype_stale_loss'
                    break
                if dur_h >= 72:
                    pnl = cur_gain
                    close_p = close
                    exit_reason = 'hype_time_cutoff'
                    break
            else:
                # Standard ROI ladder
                if step <= 7 and max_gain >= 0.526:
                    pnl = 0.526
                    close_p = entry_p * (1.0 + 0.526 / lev)
                    exit_reason = 'roi'
                    break
                elif 8 <= step <= 11 and max_gain >= 0.161:
                    pnl = 0.161
                    close_p = entry_p * (1.0 + 0.161 / lev)
                    exit_reason = 'roi'
                    break
                elif 12 <= step <= 19 and max_gain >= 0.090:
                    pnl = 0.090
                    close_p = entry_p * (1.0 + 0.090 / lev)
                    exit_reason = 'roi'
                    break
                elif step > 19 and cur_gain >= 0.011:
                    pnl = cur_gain
                    close_p = close
                    exit_reason = 'roi'
                    break
                # Stale loss pruning
                if 18 <= dur_h < 36 and cur_gain <= -0.015:
                    pnl = -0.015
                    close_p = close
                    exit_reason = 'time_decay_stale_loss'
                    break
                elif dur_h >= 36 and cur_gain < -0.010:
                    pnl = cur_gain
                    close_p = close
                    exit_reason = 'time_decay_stale_loss'
                    break
                elif dur_h >= 48:
                    pnl = cur_gain
                    close_p = close
                    exit_reason = 'time_decay_48h_cutoff'
                    break
                
        exit_time = df.loc[min(idx + dur_h, len(df)-1), 'date']
        ai_candidate_signals.append({
            'pair': pair_map[p],
            'open_date': df.loc[idx, 'date'],
            'close_date': exit_time,
            'profit_ratio': pnl * lev,
            'open_rate': round(float(entry_p), 4),
            'close_rate': round(float(close_p), 4),
            'duration_h': dur_h,
            'leverage': lev,
            'exit_reason': exit_reason,
            'is_squeeze': bool(df.loc[idx, 'is_squeeze']),
            'is_short': False,
            'enter_tag': 'ai_adaptive_squeeze_maker',
            'is_v11': False
        })

print(f"Total AI candidate signals generated: {len(ai_candidate_signals)}")

# Save intermediate candidate signals for inspect
with open("scratch/ai_candidates_8pairs.json", "w") as f:
    json.dump([
        {**s, 'open_date': str(s['open_date']), 'close_date': str(s['close_date'])}
        for s in ai_candidate_signals
    ], f, indent=2)

print("Saved ai_candidates_8pairs.json successfully.")
