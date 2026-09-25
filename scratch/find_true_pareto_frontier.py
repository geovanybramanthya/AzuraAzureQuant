import sys
sys.path.append('.')
import json
import pandas as pd
import numpy as np
import talib.abstract as ta
from pathlib import Path

# Load cached data
from scratch.explore_true_institutional_engine import pair_candles, baseline_trades, simulate_portfolio_accurate

print("Mapping True Pareto Frontier for Unique Standalone Trades...", flush=True)

# Function to simulate standalone trades with exact deduplication and bar-accurate entry
def evaluate_config(
    vol_mult=1.8,
    thrust_atr=1.2,
    headroom_pct=0.010,
    pullback_pct=0.20,
    tp1_r=1.0,
    tp2_r=2.2,
    sl_pct=0.015,
    max_hold_h=16,
    inval_h=4,
    inval_spot=-0.006,
    pairs_long=['ADA', 'ETH', 'HYPE', 'SOL', 'BTC', 'DOGE', 'LINK'],
    pairs_short=['BTC', 'HYPE', 'ETH'],
    require_15m=False,
    max_funding=0.0005
):
    trades = []
    for p, df in pair_candles.items():
        if p == 'PAXG': continue
        can_l = p in pairs_long
        can_s = p in pairs_short
        if not (can_l or can_s): continue

        lev = 7.0 if p == 'BTC' else 3.0
        n_rows = len(df)

        cond_long = (
            (df['volume'] >= df['vol_mean_20'] * vol_mult) &
            ((df['close'] - df['open']) >= thrust_atr * df['atr']) &
            (df['macro_bull_4h'] == True) &
            ((df['squeeze_streak'] >= 1) | (df['adx_4h'] < 28.0)) &
            (df['rsi'] >= 46.0) & (df['rsi'] <= 68.0) &
            (((df['rolling_high_48'] - df['close']) / df['close']) >= headroom_pct) &
            (df['close'] > df['ema_9']) &
            (df['funding_rate'] <= max_funding)
        ) if can_l else pd.Series(False, index=df.index)

        cond_short = (
            (df['volume'] >= df['vol_mean_20'] * vol_mult) &
            ((df['open'] - df['close']) >= thrust_atr * df['atr']) &
            (df['macro_bear_4h'] == True) &
            ((df['squeeze_streak'] >= 1) | (df['adx_4h'] < 28.0)) &
            (df['rsi'] >= 32.0) & (df['rsi'] <= 52.0) &
            (((df['close'] - df['rolling_low_48']) / df['close']) >= headroom_pct) &
            (df['close'] < df['ema_9'])
        ) if can_s else pd.Series(False, index=df.index)

        if require_15m:
            cond_long = cond_long & (df['rsi_15m'] >= 50.0)
            cond_short = cond_short & (df['rsi_15m'] <= 50.0)

        mask = (cond_long | cond_short)
        indices = df.index[mask].tolist()
        last_filled = -999

        for idx in indices:
            if idx <= last_filled or idx + max_hold_h + 2 >= n_rows: continue
            candle = df.iloc[idx]
            is_long = bool(cond_long.iloc[idx])
            is_short = bool(cond_short.iloc[idx])
            if not (is_long or is_short): continue
            side = 'long' if is_long else 'short'
            c_range = candle['high'] - candle['low']

            if side == 'long':
                limit_bid = candle['close'] - pullback_pct * c_range
                limit_bid = min(limit_bid, candle['close'] * 0.9985)
            else:
                limit_bid = candle['close'] + pullback_pct * c_range
                limit_bid = max(limit_bid, candle['close'] * 1.0015)

            next_c = df.iloc[idx + 1]
            filled = False
            if side == 'long' and next_c['low'] <= limit_bid: filled = True
            elif side == 'short' and next_c['high'] >= limit_bid: filled = True
            if not filled: continue

            entry_p = limit_bid
            risk = entry_p * sl_pct
            stop_loss = entry_p - risk if side == 'long' else entry_p + risk
            tp1 = entry_p + tp1_r * risk if side == 'long' else entry_p - tp1_r * risk
            tp2 = entry_p + tp2_r * risk if side == 'long' else entry_p - tp2_r * risk

            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'timeout_{max_hold_h}h'
            exit_date = None

            # Intra-bar check on next_c
            entry_c_stopped = False
            if side == 'long':
                if next_c['low'] <= stop_loss:
                    pnl_tp1 = (stop_loss - entry_p) / entry_p
                    pnl_tp2 = pnl_tp1
                    exit_reason = 'entry_c_hard_sl'
                    exit_date = next_c['date']
                    last_filled = idx + 1
                    entry_c_stopped = True
                elif next_c['high'] >= tp1:
                    tp1_hit = True
                    pnl_tp1 = (tp1 - entry_p) / entry_p
                    stop_loss = entry_p + 0.15 * risk
            else:
                if next_c['high'] >= stop_loss:
                    pnl_tp1 = (entry_p - stop_loss) / entry_p
                    pnl_tp2 = pnl_tp1
                    exit_reason = 'entry_c_hard_sl'
                    exit_date = next_c['date']
                    last_filled = idx + 1
                    entry_c_stopped = True
                elif next_c['low'] <= tp1:
                    tp1_hit = True
                    pnl_tp1 = (entry_p - tp1) / entry_p
                    stop_loss = entry_p - 0.15 * risk

            if not entry_c_stopped:
                for step in range(1, max_hold_h + 1):
                    cur_i = idx + 1 + step
                    if cur_i >= n_rows: break
                    c = df.iloc[cur_i]

                    if side == 'long':
                        if not tp1_hit and c['high'] >= tp1:
                            tp1_hit = True
                            pnl_tp1 = (tp1 - entry_p) / entry_p
                            stop_loss = entry_p + 0.15 * risk
                        if tp1_hit and c['high'] >= tp2:
                            pnl_tp2 = (tp2 - entry_p) / entry_p
                            exit_reason = 'tp2_runner'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                        if c['low'] <= stop_loss:
                            pnl_tp2 = (stop_loss - entry_p) / entry_p if tp1_hit else (stop_loss - entry_p) / entry_p
                            pnl_tp1 = pnl_tp1 if tp1_hit else pnl_tp2
                            exit_reason = 'be_after_tp1' if tp1_hit else 'hard_sl'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                        cur_spot = (c['close'] - entry_p) / entry_p
                        if step >= inval_h and not tp1_hit and cur_spot <= inval_spot:
                            pnl_tp1 = cur_spot; pnl_tp2 = cur_spot
                            exit_reason = f'inval_{step}h'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                    else:
                        if not tp1_hit and c['low'] <= tp1:
                            tp1_hit = True
                            pnl_tp1 = (entry_p - tp1) / entry_p
                            stop_loss = entry_p - 0.15 * risk
                        if tp1_hit and c['low'] <= tp2:
                            pnl_tp2 = (entry_p - tp2) / entry_p
                            exit_reason = 'tp2_runner'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                        if c['high'] >= stop_loss:
                            pnl_tp2 = (entry_p - stop_loss) / entry_p if tp1_hit else (entry_p - stop_loss) / entry_p
                            pnl_tp1 = pnl_tp1 if tp1_hit else pnl_tp2
                            exit_reason = 'be_after_tp1' if tp1_hit else 'hard_sl'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                        cur_spot = (entry_p - c['close']) / entry_p
                        if step >= inval_h and not tp1_hit and cur_spot <= inval_spot:
                            pnl_tp1 = cur_spot; pnl_tp2 = cur_spot
                            exit_reason = f'inval_{step}h'
                            exit_date = c['date']
                            last_filled = cur_i
                            break
                else:
                    if cur_i < n_rows:
                        c = df.iloc[cur_i]
                        cur_spot = (c['close'] - entry_p) / entry_p if side == 'long' else (entry_p - c['close']) / entry_p
                        if not tp1_hit: pnl_tp1 = cur_spot; pnl_tp2 = cur_spot
                        else: pnl_tp2 = cur_spot
                        exit_date = c['date']
                        last_filled = cur_i

            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            net_spot = tot_spot_pnl - 0.0006 * 2.0
            net_leveraged = net_spot * lev

            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': side,
                'is_short': (side == 'short'),
                'open_date': str(next_c['date'])[:19],
                'close_date': str(exit_date or next_c['date'])[:19],
                'profit_ratio': round(float(net_leveraged), 6),
                'spot_pnl': round(float(net_spot), 6),
                'exit_reason': exit_reason,
                'enter_tag': f'inst_disp_{side}',
                'leverage': lev,
                'is_ai': True,
                'is_stop_engine': True
            })
    # Strictly deduplicate
    df_res = pd.DataFrame(trades).drop_duplicates(subset=['pair', 'open_date'])
    return df_res.to_dict('records')

grid = []
# Test diverse pair selections
pair_configs = [
    ('All_Alts_Long_Top_Short', ['ADA', 'ETH', 'HYPE', 'SOL', 'DOGE', 'LINK'], ['BTC', 'HYPE', 'ETH']),
    ('Top5_Bidi', ['ADA', 'BTC', 'ETH', 'HYPE', 'SOL'], ['BTC', 'HYPE', 'ETH']),
    ('Alts_Only_Bidi', ['ADA', 'ETH', 'HYPE', 'SOL'], ['HYPE', 'ETH']),
    ('Long_Only_Alts', ['ADA', 'ETH', 'HYPE', 'SOL', 'DOGE', 'LINK'], []),
]

for p_name, p_long, p_short in pair_configs:
    for v in [1.5, 1.7, 1.9, 2.1]:
        for t in [1.0, 1.15, 1.30]:
            for h in [0.006, 0.010, 0.014]:
                for tp1 in [0.8, 1.0, 1.2]:
                    tr = evaluate_config(
                        vol_mult=v, thrust_atr=t, headroom_pct=h, tp1_r=tp1,
                        pairs_long=p_long, pairs_short=p_short
                    )
                    n = len(tr)
                    if n < 40: continue
                    wins = sum(1 for x in tr if x['profit_ratio'] > 0)
                    wr = wins / n * 100.0
                    gw = sum(x['profit_ratio'] for x in tr if x['profit_ratio'] > 0)
                    gl = abs(sum(x['profit_ratio'] for x in tr if x['profit_ratio'] <= 0))
                    pf = gw / gl if gl > 0 else 99.0
                    grid.append({
                        'pairs': p_name, 'v': v, 't': t, 'h': h, 'tp1': tp1,
                        'trades': n, 'wr': round(wr, 2), 'pf': round(pf, 2)
                    })

df_grid = pd.DataFrame(grid)
print(f"Total Grid Trials Completed: {len(df_grid)}", flush=True)

# Find configurations with highest win rates
print("\n=== TOP 10 HIGHEST WINRATE CONFIGURATIONS (Trades >= 80) ===")
print(df_grid[df_grid['trades'] >= 80].sort_values('wr', ascending=False).head(10).to_string(index=False), flush=True)

print("\n=== TOP 10 HIGHEST WINRATE CONFIGURATIONS (Trades >= 120) ===")
print(df_grid[df_grid['trades'] >= 120].sort_values('wr', ascending=False).head(10).to_string(index=False), flush=True)

print("\n=== TOP 10 HIGHEST WINRATE CONFIGURATIONS (Trades >= 150) ===")
print(df_grid[df_grid['trades'] >= 150].sort_values('wr', ascending=False).head(10).to_string(index=False), flush=True)
