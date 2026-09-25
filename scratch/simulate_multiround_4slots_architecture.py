import json
import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path
import sys

# Load cached data for 8 pairs
data_dir = Path("user_data/data/bybit/futures")
pairs = ['BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'LINK', 'PAXG', 'HYPE']

pair_candles = {}
pair_15m = {}
pair_funding = {}

print("Loading data for 8 pairs...", flush=True)

for p in pairs:
    fn_1h = data_dir / f"{p}_USDT_USDT-1h-futures.feather"
    fn_4h = data_dir / f"{p}_USDT_USDT-4h-futures.feather"
    fn_15m = data_dir / f"{p}_USDT_USDT-15m-futures.feather"
    fn_fund = data_dir / f"{p}_USDT_USDT-1h-funding_rate.feather"

    if not (fn_1h.exists() and fn_4h.exists()):
        continue

    df_1h = pd.read_feather(fn_1h)
    df_4h = pd.read_feather(fn_4h)

    # 4H Macro Indicators
    df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
    df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)
    df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
    df_4h['macro_bull_4h'] = (df_4h['close'] > df_4h['ema_50']) & (df_4h['ema_50'] > df_4h['ema_200'])
    df_4h['macro_bear_4h'] = (df_4h['close'] < df_4h['ema_50']) & (df_4h['ema_50'] < df_4h['ema_200'])

    df = pd.merge_asof(
        df_1h.sort_values('date'),
        df_4h[['date', 'macro_bull_4h', 'macro_bear_4h', 'adx']].rename(columns={'adx': 'adx_4h'}).sort_values('date'),
        on='date',
        direction='backward'
    )

    df['ema_9'] = ta.EMA(df['close'], timeperiod=9)
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['ema_200'] = ta.EMA(df['close'], timeperiod=200)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df, timeperiod=14)
    df['vol_mean_20'] = df['volume'].rolling(20).mean()

    df['rolling_high_48'] = df['high'].shift(1).rolling(48).max()
    df['rolling_low_48'] = df['low'].shift(1).rolling(48).min()
    df['rolling_high_24'] = df['high'].shift(1).rolling(24).max()
    df['rolling_low_24'] = df['low'].shift(1).rolling(24).min()
    df['rolling_low_12'] = df['low'].shift(1).rolling(12).min()
    df['rolling_high_12'] = df['high'].shift(1).rolling(12).max()

    df['range_span_48'] = (df['rolling_high_48'] - df['rolling_low_48']) / df['rolling_low_48']

    boll = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=20, stds=2.0)
    df['bb_lower'] = boll['lower']
    df['bb_mid'] = boll['mid']
    df['bb_upper'] = boll['upper']
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    df['bb_width_q30'] = df['bb_width'].rolling(50, min_periods=20).quantile(0.30)
    df['bb_width_q20'] = df['bb_width'].rolling(50, min_periods=20).quantile(0.20)

    kelt15 = qtpylib.keltner_channel(df, window=20, atrs=1.5)
    df['kc_upper_15'] = kelt15['upper']
    df['kc_lower_15'] = kelt15['lower']

    df['ttm_squeeze'] = (df['bb_lower'] > df['kc_lower_15']) & (df['bb_upper'] < df['kc_upper_15'])
    df['squeeze_streak'] = df['ttm_squeeze'].groupby((~df['ttm_squeeze']).cumsum()).cumcount()

    df['rising_floor_12_24'] = df['rolling_low_12'] >= df['rolling_low_24'] * 0.998
    df['falling_ceiling_12_24'] = df['rolling_high_12'] <= df['rolling_high_24'] * 1.002

    if fn_fund.exists():
        df_fund = pd.read_feather(fn_fund)
        df_fund = df_fund[['date', 'open']].rename(columns={'open': 'funding_rate'})
        df = pd.merge_asof(df.sort_values('date'), df_fund.sort_values('date'), on='date', direction='backward')
    else:
        df['funding_rate'] = 0.0001

    if fn_15m.exists():
        df_15 = pd.read_feather(fn_15m)
        df_15['rsi_15m'] = ta.RSI(df_15['close'], timeperiod=14)
        df_15['ema_9_15m'] = ta.EMA(df_15['close'], timeperiod=9)
        df_15['ema_21_15m'] = ta.EMA(df_15['close'], timeperiod=21)
        df = pd.merge_asof(df.sort_values('date'), df_15[['date', 'rsi_15m', 'ema_9_15m', 'ema_21_15m']].sort_values('date'), on='date', direction='backward')
    else:
        df['rsi_15m'] = 50.0
        df['ema_9_15m'] = df['close']
        df['ema_21_15m'] = df['close']

    pair_candles[p] = df

# Baseline trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)
baseline_trades = [dict(t, is_stop_engine=False) for t in bench_data["trades"]]

print(f"Data ready for 8 pairs. Baseline has {len(baseline_trades)} trades.", flush=True)

# Generate Multi-Gate News Trades
def generate_news_trades():
    trades = []
    for p in pairs:
        df = pair_candles[p]
        lev = 7.0 if p == 'BTC' else 3.0
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']

        cond_long = (
            (df['volume'] >= df['vol_mean_20'] * 2.2) &
            ((df['close'] - df['open']) >= 1.4 * df['atr']) &
            (df['macro_bull_4h'] == True) &
            ((df['squeeze_streak'] >= 1) | (df['adx_4h'] < 26.0)) &
            (df['rsi'] >= 50.0) & (df['rsi'] <= 65.0) &
            (dist_to_res >= 0.015) &
            (df['close'] > df['ema_9'])
        )

        n_rows = len(df)
        for idx in df.index[cond_long]:
            if idx + 13 >= n_rows: continue
            candle = df.iloc[idx]
            limit_bid = candle['close'] - 0.20 * (candle['high'] - candle['low'])
            limit_bid = min(limit_bid, candle['close'] * 0.9985)

            next_c = df.iloc[idx + 1]
            if next_c['low'] > limit_bid: continue

            entry_p = limit_bid
            risk = entry_p * 0.015
            stop_loss = entry_p - risk
            tp1 = entry_p + 1.0 * risk
            tp2 = entry_p + 2.0 * risk

            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = 'news_12h_cutoff'
            exit_date = None

            for step in range(1, 13):
                cur_i = idx + 1 + step
                if cur_i >= n_rows: break
                c = df.iloc[cur_i]

                if not tp1_hit and c['high'] >= tp1:
                    tp1_hit = True
                    pnl_tp1 = (tp1 - entry_p) / entry_p
                    stop_loss = entry_p + 0.15 * risk

                if tp1_hit and c['high'] >= tp2:
                    pnl_tp2 = (tp2 - entry_p) / entry_p
                    exit_reason = 'news_tp2_runner'
                    exit_date = c['date']
                    break

                if c['low'] <= stop_loss:
                    if tp1_hit:
                        pnl_tp2 = (stop_loss - entry_p) / entry_p
                        exit_reason = 'news_be_stopped'
                    else:
                        pnl_tp1 = (stop_loss - entry_p) / entry_p
                        pnl_tp2 = pnl_tp1
                        exit_reason = 'news_hard_sl'
                    exit_date = c['date']
                    break

                cur_spot = (c['close'] - entry_p) / entry_p
                if step >= 6 and cur_spot <= -0.008 and not tp1_hit:
                    pnl_tp1 = cur_spot
                    pnl_tp2 = cur_spot
                    exit_reason = 'news_rapid_invalidation_6h'
                    exit_date = c['date']
                    break
            else:
                if cur_i < n_rows:
                    c = df.iloc[cur_i]
                    cur_spot = (c['close'] - entry_p) / entry_p
                    if not tp1_hit: pnl_tp1 = cur_spot; pnl_tp2 = cur_spot
                    else: pnl_tp2 = cur_spot
                    exit_date = c['date']

            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            net_leveraged = (tot_spot_pnl - 0.0006) * lev

            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': 'long',
                'is_short': False,
                'open_date': str(next_c['date'])[:19],
                'close_date': str(exit_date or next_c['date'])[:19],
                'profit_ratio': round(float(net_leveraged), 6),
                'open_rate': round(float(entry_p), 4),
                'exit_reason': exit_reason,
                'enter_tag': 'news_catalyst_long',
                'leverage': lev,
                'is_ai': True,
                'is_stop_engine': True
            })
    return trades

news_trades = generate_news_trades()
df_news = pd.DataFrame(news_trades)
print(f"Generated {len(df_news)} Multi-Gate News trades | Standalone WR: {(df_news['profit_ratio']>0).mean()*100:.1f}%", flush=True)

# Generate Calibrated High-Conviction Breakout Trades
def generate_calibrated_breakouts(
    min_vol_mult=2.2,
    adaptive_span=True,
    exclude_paxg=True,
    exclude_btc=True,
    delta_atr=0.10,
    tp1_r=1.2,
    tp2_r=3.0,
    be_buffer_r=0.15,
    sl_pct=0.015,
    rapid_invalidation_h=3,
    rapid_invalidation_spot=-0.005,
    max_hold_h=24,
    use_15m_filter=True,
    use_funding_filter=True,
    direction='bidirectional',
    btc_leverage=3.0,
    taker_fee=0.0006,
    slippage=0.0015
):
    span_thresholds = {
        'BTC': 0.050, 'ETH': 0.055, 'SOL': 0.065, 'ADA': 0.070,
        'DOGE': 0.070, 'LINK': 0.065, 'PAXG': 0.035, 'HYPE': 0.085
    }

    trades = []
    for p in pairs:
        if exclude_paxg and p == 'PAXG': continue
        if exclude_btc and p == 'BTC': continue
        if p not in pair_candles: continue

        df = pair_candles[p]
        lev = btc_leverage if p == 'BTC' else 3.0
        max_span = span_thresholds.get(p, 0.065) if adaptive_span else 0.045

        range_pos = (df['close'] - df['rolling_low_48']) / (df['rolling_high_48'] - df['rolling_low_48'])
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
        dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']

        long_setup = (
            (df['macro_bull_4h'] == True) &
            (df['adx_4h'] >= 20.0) &
            (df['range_span_48'] <= max_span) &
            ((df['squeeze_streak'] >= 2) | (df['bb_width'] <= df['bb_width_q30'])) &
            (range_pos >= 0.65) &
            (dist_to_res <= 0.020) & (dist_to_res >= 0.001) &
            (df['rising_floor_12_24'] == True) &
            (df['rsi'] >= 52.0) & (df['rsi'] <= 68.0) &
            (df['close'] > df['ema_9']) &
            (df['close'] > df['ema_21'])
        )

        short_setup = (
            (df['macro_bear_4h'] == True) &
            (df['adx_4h'] >= 20.0) &
            (df['range_span_48'] <= max_span) &
            ((df['squeeze_streak'] >= 2) | (df['bb_width'] <= df['bb_width_q30'])) &
            (range_pos <= 0.35) &
            (dist_to_sup <= 0.020) & (dist_to_sup >= 0.001) &
            (df['falling_ceiling_12_24'] == True) &
            (df['rsi'] >= 32.0) & (df['rsi'] <= 48.0) &
            (df['close'] < df['ema_9']) &
            (df['close'] < df['ema_21'])
        )

        if use_funding_filter:
            long_setup = long_setup & (df['funding_rate'] <= 0.0003)
            short_setup = short_setup & (df['funding_rate'] >= -0.0003)

        if use_15m_filter:
            long_setup = long_setup & (df['rsi_15m'] <= 75.0) & (df['rsi_15m'] >= 45.0)
            short_setup = short_setup & (df['rsi_15m'] >= 25.0) & (df['rsi_15m'] <= 55.0)

        candle_mask = (long_setup | short_setup)
        candidate_indices = df.index[candle_mask].tolist()

        n_rows = len(df)
        last_filled_idx = -999

        for idx in candidate_indices:
            if idx <= last_filled_idx: continue
            if idx >= n_rows - max_hold_h - 2: continue

            candle = df.iloc[idx]
            res_48 = candle['rolling_high_48']
            sup_48 = candle['rolling_low_48']
            atr_val = candle['atr']

            is_long = bool(long_setup.iloc[idx]) and (direction in ['bidirectional', 'long_only'])
            is_short = bool(short_setup.iloc[idx]) and (direction in ['bidirectional', 'short_only'])
            if not (is_long or is_short): continue

            side = 'long' if is_long else 'short'
            trigger_p = res_48 + delta_atr * atr_val if side == 'long' else sup_48 - delta_atr * atr_val

            filled = False
            fill_idx = -1
            fill_candle = None

            for v_step in range(1, 9):
                f_idx = idx + v_step
                if f_idx >= n_rows: break
                check_c = df.iloc[f_idx]

                if side == 'long':
                    if check_c['high'] >= trigger_p:
                        if check_c['volume'] >= check_c['vol_mean_20'] * min_vol_mult:
                            filled = True; fill_idx = f_idx; fill_candle = check_c; break
                else:
                    if check_c['low'] <= trigger_p:
                        if check_c['volume'] >= check_c['vol_mean_20'] * min_vol_mult:
                            filled = True; fill_idx = f_idx; fill_candle = check_c; break

            if not filled: continue

            entry_p = trigger_p * (1.0 + slippage) if side == 'long' else trigger_p * (1.0 - slippage)
            risk = entry_p * sl_pct
            stop_loss = entry_p - risk if side == 'long' else entry_p + risk
            tp1 = entry_p + tp1_r * risk if side == 'long' else entry_p - tp1_r * risk
            tp2 = entry_p + tp2_r * risk if side == 'long' else entry_p - tp2_r * risk

            entry_date = fill_candle['date']
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'calibrated_{max_hold_h}h_timeout'
            exit_date = None
            bars_held = 0

            max_favorable_excursion = 0.0
            max_adverse_excursion = 0.0

            for p_step in range(1, max_hold_h + 1):
                cur_idx = fill_idx + p_step
                if cur_idx >= n_rows: break
                c_bar = df.iloc[cur_idx]
                bars_held = p_step

                if side == 'long':
                    favorable = (c_bar['high'] - entry_p) / entry_p
                    adverse = (entry_p - c_bar['low']) / entry_p
                    if favorable > max_favorable_excursion: max_favorable_excursion = favorable
                    if adverse > max_adverse_excursion: max_adverse_excursion = adverse

                    if not tp1_hit and c_bar['high'] >= tp1:
                        tp1_hit = True
                        pnl_tp1 = (tp1 - entry_p) / entry_p
                        stop_loss = entry_p + be_buffer_r * risk

                    if tp1_hit and c_bar['high'] >= tp2:
                        pnl_tp2 = (tp2 - entry_p) / entry_p
                        exit_reason = 'calibrated_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['low'] <= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (stop_loss - entry_p) / entry_p - slippage
                            exit_reason = 'calibrated_be_after_tp1'
                        else:
                            pnl_tp1 = (stop_loss - entry_p) / entry_p - slippage
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'calibrated_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (c_bar['close'] - entry_p) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] < res_48 and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 - slippage)
                            pnl_tp1 = (exit_p - entry_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'calibrated_trap_invalidation_{p_step}h'
                            exit_date = c_bar['date']
                            last_filled_idx = cur_idx
                            break
                else:
                    favorable = (entry_p - c_bar['low']) / entry_p
                    adverse = (c_bar['high'] - entry_p) / entry_p
                    if favorable > max_favorable_excursion: max_favorable_excursion = favorable
                    if adverse > max_adverse_excursion: max_adverse_excursion = adverse

                    if not tp1_hit and c_bar['low'] <= tp1:
                        tp1_hit = True
                        pnl_tp1 = (entry_p - tp1) / entry_p
                        stop_loss = entry_p - be_buffer_r * risk

                    if tp1_hit and c_bar['low'] <= tp2:
                        pnl_tp2 = (entry_p - tp2) / entry_p
                        exit_reason = 'calibrated_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['high'] >= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (entry_p - stop_loss) / entry_p - slippage
                            exit_reason = 'calibrated_be_after_tp1'
                        else:
                            pnl_tp1 = (entry_p - stop_loss) / entry_p - slippage
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'calibrated_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (entry_p - c_bar['close']) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] > sup_48 and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 + slippage)
                            pnl_tp1 = (entry_p - exit_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'calibrated_trap_invalidation_{p_step}h'
                            exit_date = c_bar['date']
                            last_filled_idx = cur_idx
                            break
            else:
                if cur_idx < n_rows:
                    c_bar = df.iloc[cur_idx]
                    exit_p = c_bar['close'] * (1.0 - slippage) if side == 'long' else c_bar['close'] * (1.0 + slippage)
                    spot_p = (exit_p - entry_p) / entry_p if side == 'long' else (entry_p - exit_p) / entry_p
                    if not tp1_hit: pnl_tp1 = spot_p; pnl_tp2 = spot_p
                    else: pnl_tp2 = spot_p
                    exit_date = c_bar['date']
                    last_filled_idx = cur_idx

            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            net_spot_pnl = tot_spot_pnl - (taker_fee * 2.0)
            net_leveraged_pnl = net_spot_pnl * lev

            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': side,
                'is_short': (side == 'short'),
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or fill_candle['date'])[:19],
                'profit_ratio': round(float(net_leveraged_pnl), 6),
                'open_rate': round(float(entry_p), 4),
                'exit_reason': exit_reason,
                'enter_tag': f'ai_calibrated_breakout_{side}',
                'leverage': lev,
                'is_ai': True,
                'is_stop_engine': True
            })

    return trades

# 4-Slot Apex Quantum Simulator (3 Core + 1 Dedicated Engine Slot)
w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def simulate_apex_quantum_4slots(extra_trades, stake_scale_engine=0.50):
    all_trades = baseline_trades + extra_trades
    df_all = pd.DataFrame(all_trades)
    df_all['open_date_dt'] = pd.to_datetime(df_all['open_date'], utc=True)
    df_all['close_date_dt'] = pd.to_datetime(df_all['close_date'], utc=True)
    df_all = df_all.sort_values('open_date_dt').reset_index(drop=True)

    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    executed = []
    
    active_positions = []

    for idx, r in df_all.iterrows():
        o_date = r['open_date_dt']
        is_engine_trade = r.get('is_stop_engine', False)
        
        # Purge closed trades
        active_positions = [pos for pos in active_positions if pos['close_date_dt'] > o_date]
        
        # Separate slot check:
        # Core slots: max 3
        # Dedicated engine slot: max 1
        # Total slots: max 4
        core_count = sum(1 for pos in active_positions if not pos['is_engine'])
        engine_count = sum(1 for pos in active_positions if pos['is_engine'])
        
        if is_engine_trade:
            if engine_count >= 1 or len(active_positions) >= 4:
                continue
        else:
            if core_count >= 3 or len(active_positions) >= 4:
                continue

        same_side = sum(1 for pos in active_positions if pos['is_short'] == r['is_short'])

        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0

        base_stake = (wallet / 3.0) * 0.95 * brake
        if same_side >= 2:
            base_stake *= 0.70

        stake = base_stake * stake_scale_engine if is_engine_trade else base_stake

        pr = r['profit_ratio']
        if pr > 0:
            pnl = stake * pr * w_scale
            gross_w += pnl
        else:
            pnl = stake * pr * l_scale
            gross_l += abs(pnl)

        wallet += pnl
        if wallet > peak: peak = wallet
        dd = (peak - wallet) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

        r_dict = dict(r)
        r_dict['profit_abs'] = pnl
        executed.append(r_dict)
        
        active_positions.append({
            'close_date_dt': r['close_date_dt'],
            'is_short': r['is_short'],
            'is_engine': is_engine_trade
        })

    df_exec = pd.DataFrame(executed)
    total_trades = len(df_exec)
    wins = (df_exec['profit_ratio'] > 0).sum()
    losses = (df_exec['profit_ratio'] <= 0).sum()
    winrate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    pf = gross_w / gross_l if gross_l > 0 else 99.0

    eng_exec = df_exec[df_exec['is_stop_engine'] == True] if 'is_stop_engine' in df_exec.columns else pd.DataFrame()
    eng_wins = (eng_exec['profit_ratio'] > 0).sum() if len(eng_exec) > 0 else 0
    eng_wr = (eng_wins / len(eng_exec) * 100.0) if len(eng_exec) > 0 else 0

    return {
        'total_trades': total_trades,
        'engine_executed': len(eng_exec),
        'engine_winrate': round(eng_wr, 2),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(pf, 2)
    }

# Baseline
base_res = simulate_apex_quantum_4slots([])
print(f"\n--- BASELINE CHECKPOINT (3 Slots Core Model C) ---", flush=True)
print(f"  Total Trades: {base_res['total_trades']} ({base_res['total_trades']/988:.2f}/day)")
print(f"  Win Rate: {base_res['winrate_pct']}% | Max DD: {base_res['max_drawdown_pct']}% | Profit Factor: {base_res['profit_factor']}")
print(f"  Terminal Wallet: ${base_res['final_wallet']:,.2f}", flush=True)

# Trials
trials = [
    {
        'name': 'Trial 1: Baseline + News Catalyst Only (4th Slot)',
        'trades': news_trades
    },
    {
        'name': 'Trial 2: Baseline + Calibrated Breakout (Vol>=2.4x, Adaptive Span, Alts Only, 4th Slot)',
        'trades': generate_calibrated_breakouts(min_vol_mult=2.4, adaptive_span=True, exclude_paxg=True, exclude_btc=True, tp2_r=3.0)
    },
    {
        'name': 'Trial 3: Baseline + High-Frequency Breakout (Vol>=2.0x, Adaptive Span, Alts Only, 4th Slot)',
        'trades': generate_calibrated_breakouts(min_vol_mult=2.0, adaptive_span=True, exclude_paxg=True, exclude_btc=True, tp2_r=2.8)
    },
    {
        'name': 'Trial 4: Baseline + Ultra-Quality Breakout (Vol>=2.8x, Adaptive Span, Alts Only, 4th Slot)',
        'trades': generate_calibrated_breakouts(min_vol_mult=2.8, adaptive_span=True, exclude_paxg=True, exclude_btc=True, tp2_r=3.2)
    },
    {
        'name': 'Trial 5: Synergy Architecture (News Catalyst + Calibrated Breakout Vol>=2.2x in 4th Slot)',
        'trades': news_trades + generate_calibrated_breakouts(min_vol_mult=2.2, adaptive_span=True, exclude_paxg=True, exclude_btc=True, tp2_r=3.0)
    },
    {
        'name': 'Trial 6: Synergy High-Velocity (News Catalyst + Breakout Vol>=1.8x in 4th Slot)',
        'trades': news_trades + generate_calibrated_breakouts(min_vol_mult=1.8, adaptive_span=True, exclude_paxg=True, exclude_btc=True, tp2_r=2.8)
    }
]

audit_summary = []

for t in trials:
    name = t['name']
    tr_list = t['trades']
    df_t = pd.DataFrame(tr_list)

    wins = (df_t['profit_ratio'] > 0).sum() if len(df_t) > 0 else 0
    wr = (wins / len(df_t) * 100.0) if len(df_t) > 0 else 0
    gw = df_t[df_t['profit_ratio'] > 0]['profit_ratio'].sum() if len(df_t) > 0 else 0
    gl = abs(df_t[df_t['profit_ratio'] <= 0]['profit_ratio'].sum()) if len(df_t) > 0 else 0
    pf = gw / gl if gl > 0 else 99.0

    p_res = simulate_apex_quantum_4slots(tr_list, stake_scale_engine=0.50)
    t_day = p_res['total_trades'] / 988.0

    print(f"\n--- {name} ---", flush=True)
    print(f"  Standalone Engine: {len(df_t)} trades | WR: {wr:.2f}% ({wins}W/{len(df_t)-wins}L) | PF: {pf:.2f} | Net PnL: {df_t['profit_ratio'].sum()*100:+.1f}%", flush=True)
    print(f"  Portfolio (4 Slots): {p_res['total_trades']} trades ({t_day:.2f}/day, Eng Executed: {p_res['engine_executed']})", flush=True)
    print(f"  Portfolio WR: {p_res['winrate_pct']}% | Max DD: {p_res['max_drawdown_pct']}% | PF: {p_res['profit_factor']}", flush=True)
    print(f"  Terminal Wallet: ${p_res['final_wallet']:,.2f} (Delta: +${p_res['final_wallet'] - base_res['final_wallet']:,.2f})", flush=True)

    audit_summary.append({
        'name': name,
        'standalone_trades': len(df_t),
        'standalone_wr': round(wr, 2),
        'standalone_pf': round(pf, 2),
        'port_trades': p_res['total_trades'],
        'trades_per_day': round(t_day, 3),
        'port_engine_exec': p_res['engine_executed'],
        'port_wr': p_res['winrate_pct'],
        'port_max_dd': p_res['max_drawdown_pct'],
        'port_wallet': p_res['final_wallet'],
        'port_pf': p_res['profit_factor']
    })

with open("scratch/multiround_4slots_audit_summary.json", "w") as f:
    json.dump(audit_summary, f, indent=2)

print("\nAudit complete! Saved to scratch/multiround_4slots_audit_summary.json.", flush=True)
