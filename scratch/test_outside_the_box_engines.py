import json
import pandas as pd
import numpy as np
import talib.abstract as ta
from technical import qtpylib
from pathlib import Path
import sys

# Load data for 8 pairs
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

    # 1H Technical Indicators
    df['ema_9'] = ta.EMA(df['close'], timeperiod=9)
    df['ema_21'] = ta.EMA(df['close'], timeperiod=21)
    df['ema_50'] = ta.EMA(df['close'], timeperiod=50)
    df['ema_200'] = ta.EMA(df['close'], timeperiod=200)
    df['rsi'] = ta.RSI(df['close'], timeperiod=14)
    df['atr'] = ta.ATR(df, timeperiod=14)
    df['vol_mean_20'] = df['volume'].rolling(20).mean()

    # Dynamic Rolling Resistance and Support
    df['rolling_high_48'] = df['high'].shift(1).rolling(48).max()
    df['rolling_low_48'] = df['low'].shift(1).rolling(48).min()
    df['rolling_high_24'] = df['high'].shift(1).rolling(24).max()
    df['rolling_low_24'] = df['low'].shift(1).rolling(24).min()
    df['rolling_low_12'] = df['low'].shift(1).rolling(12).min()
    df['rolling_high_12'] = df['high'].shift(1).rolling(12).max()

    # Range tightness ratio: (High48 - Low48) / Low48
    df['range_span_48'] = (df['rolling_high_48'] - df['rolling_low_48']) / df['rolling_low_48']

    # Bollinger Bands
    boll = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=20, stds=2.0)
    df['bb_lower'] = boll['lower']
    df['bb_mid'] = boll['mid']
    df['bb_upper'] = boll['upper']
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    df['bb_width_q30'] = df['bb_width'].rolling(50, min_periods=20).quantile(0.30)
    df['bb_width_q20'] = df['bb_width'].rolling(50, min_periods=20).quantile(0.20)

    # Keltner Channels
    kelt15 = qtpylib.keltner_channel(df, window=20, atrs=1.5)
    df['kc_upper_15'] = kelt15['upper']
    df['kc_lower_15'] = kelt15['lower']

    # Carter TTM Squeeze
    df['ttm_squeeze'] = (df['bb_lower'] > df['kc_lower_15']) & (df['bb_upper'] < df['kc_upper_15'])
    df['squeeze_streak'] = df['ttm_squeeze'].groupby((~df['ttm_squeeze']).cumsum()).cumcount()

    # Ascending Base / Descending Crest
    df['rising_floor_12_24'] = df['rolling_low_12'] >= df['rolling_low_24'] * 0.998
    df['falling_ceiling_12_24'] = df['rolling_high_12'] <= df['rolling_high_24'] * 1.002

    # Add Funding Rate
    if fn_fund.exists():
        df_fund = pd.read_feather(fn_fund)
        df_fund = df_fund[['date', 'open']].rename(columns={'open': 'funding_rate'})
        df = pd.merge_asof(df.sort_values('date'), df_fund.sort_values('date'), on='date', direction='backward')
    else:
        df['funding_rate'] = 0.0001

    # Add 15m Indicators if available
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

print("Loaded all pair candles with 1H, 4H, 15M, and Funding Rates.", flush=True)

# Load Baseline trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)
baseline_trades = [dict(t, is_stop_engine=False) for t in bench_data["trades"]]
print(f"Loaded {len(baseline_trades)} baseline trades.", flush=True)

# Ultra-fast Portfolio Simulator for 4 concurrent slots
w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def simulate_portfolio_4slots(candidate_trades, stake_scale=0.50, max_slots=4):
    all_trades = baseline_trades + candidate_trades
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
    
    # Active trades tracker: list of (close_date_dt, is_short)
    active_positions = []

    for idx, r in df_all.iterrows():
        o_date = r['open_date_dt']
        
        # Purge closed trades
        active_positions = [pos for pos in active_positions if pos['close_date_dt'] > o_date]
        
        active_total = len(active_positions)
        if active_total >= max_slots:
            continue

        same_side = sum(1 for pos in active_positions if pos['is_short'] == r['is_short'])

        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0

        base_stake = (wallet / float(max_slots)) * 0.95 * brake
        if same_side >= 2:
            base_stake *= 0.70

        stake = base_stake * stake_scale if r.get('is_stop_engine', False) else base_stake

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
            'is_short': r['is_short']
        })

    df_exec = pd.DataFrame(executed)
    total_trades = len(df_exec)
    wins = (df_exec['profit_ratio'] > 0).sum()
    losses = (df_exec['profit_ratio'] <= 0).sum()
    winrate = (wins / total_trades) * 100.0 if total_trades > 0 else 0
    pf = gross_w / gross_l if gross_l > 0 else 99.0

    return {
        'total_trades': total_trades,
        'engine_executed': int(df_exec['is_stop_engine'].sum()),
        'winrate_pct': round(winrate, 2),
        'final_wallet': round(wallet, 2),
        'max_drawdown_pct': round(max_dd * 100.0, 2),
        'profit_factor': round(pf, 2)
    }

# ==============================================================================
# FAST VECTOR-OPTIMIZED BREAKOUT-AND-RETEST LIMIT MAKER ENGINE
# ==============================================================================
def generate_retest_limit_maker_trades(
    adaptive_span=True,
    maker_fee=0.0002,
    taker_fee_exit=0.0006,
    slippage_entry=0.0000,
    slippage_exit=0.0010,
    min_squeeze_streak=1,
    vol_shock_mult=1.8,
    retest_offset_pct=0.002,
    retest_validity_hours=6,
    tp1_r=1.2,
    tp2_r=2.8,
    be_buffer_r=0.15,
    sl_pct=0.015,
    rapid_invalidation_h=4,
    rapid_invalidation_spot=-0.006,
    max_hold_h=24,
    exclude_paxg=True,
    use_funding_filter=True,
    max_funding_long=0.0003,
    min_funding_short=-0.0003,
    use_15m_filter=True,
    direction='bidirectional',
    btc_leverage=3.0,
    engine_type='retest_maker' # 'naive_stop' or 'retest_maker'
):
    trades = []
    
    span_thresholds = {
        'BTC': 0.050,
        'ETH': 0.055,
        'SOL': 0.065,
        'ADA': 0.070,
        'DOGE': 0.070,
        'LINK': 0.065,
        'PAXG': 0.035,
        'HYPE': 0.085
    }

    for p in pairs:
        if exclude_paxg and p == 'PAXG':
            continue
        if p not in pair_candles:
            continue

        df = pair_candles[p]
        lev = btc_leverage if p == 'BTC' else 3.0
        max_span = span_thresholds.get(p, 0.055) if adaptive_span else 0.045

        if engine_type == 'naive_stop':
            range_pos = (df['close'] - df['rolling_low_48']) / (df['rolling_high_48'] - df['rolling_low_48'])
            dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
            dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']

            long_setup = (
                (df['macro_bull_4h'] == True) &
                (df['adx_4h'] >= 22.0) &
                (df['range_span_48'] <= max_span) &
                ((df['squeeze_streak'] >= 2) | (df['bb_width'] <= df['bb_width_q20'])) &
                (range_pos >= 0.65) &
                (dist_to_res <= 0.015) & (dist_to_res >= 0.001) &
                (df['rising_floor_12_24'] == True) &
                (df['rsi'] >= 52.0) & (df['rsi'] <= 68.0) &
                (df['close'] > df['ema_9'])
            )

            short_setup = (
                (df['macro_bear_4h'] == True) &
                (df['adx_4h'] >= 22.0) &
                (df['range_span_48'] <= max_span) &
                ((df['squeeze_streak'] >= 2) | (df['bb_width'] <= df['bb_width_q20'])) &
                (range_pos <= 0.35) &
                (dist_to_sup <= 0.015) & (dist_to_sup >= 0.001) &
                (df['falling_ceiling_12_24'] == True) &
                (df['rsi'] >= 32.0) & (df['rsi'] <= 48.0) &
                (df['close'] < df['ema_9'])
            )
        else: # 'retest_maker'
            long_setup = (
                (df['close'] > df['rolling_high_48']) &
                (df['close'] > df['open']) &
                ((df['close'] - df['open']) >= 0.20 * df['atr']) &
                (df['range_span_48'] <= max_span) &
                (df['macro_bull_4h'] == True) &
                (df['adx_4h'] >= 20.0) &
                ((df['squeeze_streak'] >= min_squeeze_streak) | (df['bb_width'] <= df['bb_width_q30'])) &
                (df['volume'] >= df['vol_mean_20'] * vol_shock_mult)
            )

            short_setup = (
                (df['close'] < df['rolling_low_48']) &
                (df['close'] < df['open']) &
                ((df['open'] - df['close']) >= 0.20 * df['atr']) &
                (df['range_span_48'] <= max_span) &
                (df['macro_bear_4h'] == True) &
                (df['adx_4h'] >= 20.0) &
                ((df['squeeze_streak'] >= min_squeeze_streak) | (df['bb_width'] <= df['bb_width_q30'])) &
                (df['volume'] >= df['vol_mean_20'] * vol_shock_mult)
            )

            if use_funding_filter:
                long_setup = long_setup & (df['funding_rate'] <= max_funding_long)
                short_setup = short_setup & (df['funding_rate'] >= min_funding_short)

            if use_15m_filter:
                long_setup = long_setup & (df['rsi_15m'] <= 78.0) & (df['rsi_15m'] >= 42.0)
                short_setup = short_setup & (df['rsi_15m'] >= 22.0) & (df['rsi_15m'] <= 58.0)

        candle_mask = (long_setup | short_setup)
        candidate_indices = df.index[candle_mask].tolist()

        n_rows = len(df)
        last_filled_idx = -999

        for idx in candidate_indices:
            if idx <= last_filled_idx: continue
            if idx >= n_rows - max_hold_h - 2: continue

            candle = df.iloc[idx]
            res_level = candle['rolling_high_48']
            sup_level = candle['rolling_low_48']
            atr_val = candle['atr']

            is_long = bool(long_setup.iloc[idx]) and (direction in ['bidirectional', 'long_only'])
            is_short = bool(short_setup.iloc[idx]) and (direction in ['bidirectional', 'short_only'])
            if not (is_long or is_short): continue

            side = 'long' if is_long else 'short'

            filled = False
            fill_idx = -1
            fill_candle = None

            if engine_type == 'naive_stop':
                delta_atr = 0.15
                trigger_p = res_level + delta_atr * atr_val if side == 'long' else sup_level - delta_atr * atr_val
                for v_step in range(1, 9):
                    f_idx = idx + v_step
                    if f_idx >= n_rows: break
                    check_c = df.iloc[f_idx]
                    if side == 'long' and check_c['high'] >= trigger_p:
                        if check_c['volume'] >= check_c['vol_mean_20'] * 2.2:
                            filled = True; fill_idx = f_idx; fill_candle = check_c; break
                    elif side == 'short' and check_c['low'] <= trigger_p:
                        if check_c['volume'] >= check_c['vol_mean_20'] * 2.2:
                            filled = True; fill_idx = f_idx; fill_candle = check_c; break
                entry_p = trigger_p * (1.0 + 0.0015) if side == 'long' else trigger_p * (1.0 - 0.0015)
                cur_maker_fee = 0.0006
            else: # retest_maker
                if side == 'long':
                    limit_bid = res_level * (1.0 + retest_offset_pct)
                    limit_bid = min(limit_bid, candle['close'] * 0.999)
                else:
                    limit_bid = sup_level * (1.0 - retest_offset_pct)
                    limit_bid = max(limit_bid, candle['close'] * 1.001)

                for v_step in range(1, retest_validity_hours + 1):
                    f_idx = idx + v_step
                    if f_idx >= n_rows: break
                    check_c = df.iloc[f_idx]
                    if side == 'long' and check_c['low'] <= limit_bid:
                        filled = True; fill_idx = f_idx; fill_candle = check_c; break
                    elif side == 'short' and check_c['high'] >= limit_bid:
                        filled = True; fill_idx = f_idx; fill_candle = check_c; break
                entry_p = limit_bid
                cur_maker_fee = maker_fee

            if not filled: continue

            risk = entry_p * sl_pct
            stop_loss = entry_p - risk if side == 'long' else entry_p + risk
            tp1 = entry_p + tp1_r * risk if side == 'long' else entry_p - tp1_r * risk
            tp2 = entry_p + tp2_r * risk if side == 'long' else entry_p - tp2_r * risk

            entry_date = fill_candle['date']
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'retest_{max_hold_h}h_timeout'
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
                        exit_reason = 'retest_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['low'] <= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (stop_loss - entry_p) / entry_p - slippage_exit
                            exit_reason = 'retest_be_after_tp1'
                        else:
                            pnl_tp1 = (stop_loss - entry_p) / entry_p - slippage_exit
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'retest_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (c_bar['close'] - entry_p) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] < res_level and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 - slippage_exit)
                            pnl_tp1 = (exit_p - entry_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'retest_trap_invalidation_{p_step}h'
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
                        exit_reason = 'retest_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['high'] >= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (entry_p - stop_loss) / entry_p - slippage_exit
                            exit_reason = 'retest_be_after_tp1'
                        else:
                            pnl_tp1 = (entry_p - stop_loss) / entry_p - slippage_exit
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'retest_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (entry_p - c_bar['close']) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] > sup_level and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 + slippage_exit)
                            pnl_tp1 = (entry_p - exit_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'retest_trap_invalidation_{p_step}h'
                            exit_date = c_bar['date']
                            last_filled_idx = cur_idx
                            break
            else:
                if cur_idx < n_rows:
                    c_bar = df.iloc[cur_idx]
                    exit_p = c_bar['close'] * (1.0 - slippage_exit) if side == 'long' else c_bar['close'] * (1.0 + slippage_exit)
                    spot_p = (exit_p - entry_p) / entry_p if side == 'long' else (entry_p - exit_p) / entry_p
                    if not tp1_hit: pnl_tp1 = spot_p; pnl_tp2 = spot_p
                    else: pnl_tp2 = spot_p
                    exit_date = c_bar['date']
                    last_filled_idx = cur_idx

            tot_spot_pnl = 0.5 * pnl_tp1 + 0.5 * pnl_tp2
            friction_spot = cur_maker_fee + taker_fee_exit
            net_spot_pnl = tot_spot_pnl - friction_spot
            net_leveraged_pnl = net_spot_pnl * lev

            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': side,
                'is_short': (side == 'short'),
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or fill_candle['date'])[:19],
                'profit_ratio': round(float(net_leveraged_pnl), 6),
                'spot_pnl': round(float(net_spot_pnl), 6),
                'gross_spot_pnl': round(float(tot_spot_pnl), 6),
                'friction_spot': round(float(friction_spot), 6),
                'open_rate': round(float(entry_p), 4),
                'exit_reason': exit_reason,
                'enter_tag': f'ai_{engine_type}_{side}',
                'leverage': lev,
                'is_ai': True,
                'is_stop_engine': True,
                'bars_held': bars_held,
                'mfe': round(float(max_favorable_excursion), 4),
                'mae': round(float(max_adverse_excursion), 4)
            })

    return trades

print("\n" + "="*80, flush=True)
print("RUNNING MULTI-ROUND SYSTEMATIC EMPIRICAL EXPERIMENTS (4 CONCURRENT SLOTS)", flush=True)
print("="*80, flush=True)

experiments = [
    {
        'name': 'Experiment 0: Naive Stop-Market Breakout (All 8 Pairs, BTC 7x)',
        'params': {'engine_type': 'naive_stop', 'adaptive_span': False, 'exclude_paxg': False, 'use_funding_filter': False, 'use_15m_filter': False, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 1: Naive Stop-Market Breakout (Altcoins Only, Ex-BTC)',
        'params': {'engine_type': 'naive_stop', 'adaptive_span': False, 'exclude_paxg': False, 'use_funding_filter': False, 'use_15m_filter': False, 'btc_leverage': 0.0} # We filter out BTC below
    },
    {
        'name': 'Experiment 2: Paradigm Shift A (Retest Limit Maker Engine, All 8 Pairs)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': False, 'exclude_paxg': False, 'use_funding_filter': False, 'use_15m_filter': False, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 3: Paradigm Shift A + PAXG Exclusion (Mean-Reverting Gold Filtered)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': False, 'exclude_paxg': True, 'use_funding_filter': False, 'use_15m_filter': False, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 4: Paradigm Shift A + C (Volatility-Adaptive Range Span: HYPE 8.5%, Alts 6.5-7.0%)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': True, 'exclude_paxg': True, 'use_funding_filter': False, 'use_15m_filter': False, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 5: Paradigm Shift A + B + C (Adding Funding Microstructure Filter)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': True, 'exclude_paxg': True, 'use_funding_filter': True, 'use_15m_filter': False, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 6: Paradigm Shift A + B + C + D (Adding 15m Sub-Candle Confirmation)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': True, 'exclude_paxg': True, 'use_funding_filter': True, 'use_15m_filter': True, 'btc_leverage': 7.0}
    },
    {
        'name': 'Experiment 7: Paradigm Shift A+B+C+D + BTC Leverage Calibrated to 3.0x',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': True, 'exclude_paxg': True, 'use_funding_filter': True, 'use_15m_filter': True, 'btc_leverage': 3.0}
    },
    {
        'name': 'Experiment 8: Paradigm Shift A+B+C+D + Altcoins Only (BTC Excluded from Breakout)',
        'params': {'engine_type': 'retest_maker', 'adaptive_span': True, 'exclude_paxg': True, 'use_funding_filter': True, 'use_15m_filter': True, 'btc_leverage': 3.0}
    }
]

audit_results = []

for exp in experiments:
    name = exp['name']
    p_dict = exp['params']
    
    tr = generate_retest_limit_maker_trades(**p_dict)
    if 'Altcoins Only' in name or 'Ex-BTC' in name:
        tr = [t for t in tr if 'BTC' not in t['pair']]

    df_tr = pd.DataFrame(tr)
    if len(df_tr) == 0:
        print(f"\n{name}: 0 trades generated.", flush=True)
        continue

    wins = df_tr[df_tr['profit_ratio'] > 0]
    losses = df_tr[df_tr['profit_ratio'] <= 0]
    wr = len(wins) / len(df_tr) * 100.0
    gw = wins['profit_ratio'].sum()
    gl = abs(losses['profit_ratio'].sum())
    pf = gw / gl if gl > 0 else 99.0
    tot_pnl = df_tr['profit_ratio'].sum() * 100.0

    port_res = simulate_portfolio_4slots(tr, stake_scale=0.50, max_slots=4)
    trades_per_day = port_res['total_trades'] / 988.0

    res_item = {
        'name': name,
        'standalone_trades': len(df_tr),
        'standalone_wins': len(wins),
        'standalone_losses': len(losses),
        'standalone_wr': round(wr, 2),
        'standalone_pf': round(pf, 2),
        'standalone_pnl': round(tot_pnl, 1),
        'port_trades': port_res['total_trades'],
        'port_engine_exec': port_res['engine_executed'],
        'trades_per_day': round(trades_per_day, 3),
        'port_wr': port_res['winrate_pct'],
        'port_max_dd': port_res['max_drawdown_pct'],
        'port_wallet': port_res['final_wallet'],
        'port_pf': port_res['profit_factor']
    }
    audit_results.append(res_item)

    print(f"\n--- {name} ---", flush=True)
    print(f"  Standalone: {len(df_tr)} trades | WR: {wr:.2f}% ({len(wins)}W/{len(losses)}L) | PF: {pf:.2f} | Net PnL: {tot_pnl:+.1f}%", flush=True)
    print(f"  Portfolio (4 Slots): {port_res['total_trades']} trades ({trades_per_day:.2f}/day, Eng: {port_res['engine_executed']}) | WR: {port_res['winrate_pct']:.2f}% | MaxDD: {port_res['max_drawdown_pct']:.2f}% | Wallet: ${port_res['final_wallet']:,.2f} | PF: {port_res['profit_factor']:.2f}", flush=True)

    if 'Experiment 7' in name or 'Experiment 8' in name or 'Experiment 6' in name:
        print("  Breakdown by Pair:", flush=True)
        p_grp = df_tr.groupby('pair').agg(
            trades=('profit_ratio', 'count'),
            winrate=('profit_ratio', lambda x: round((x > 0).mean() * 100, 1)),
            pf=('profit_ratio', lambda x: round(x[x > 0].sum() / abs(x[x <= 0].sum()) if abs(x[x <= 0].sum()) > 0 else 99, 2)),
            tot_pnl=('profit_ratio', lambda x: round(x.sum() * 100, 1))
        )
        print(p_grp.to_string(), flush=True)

with open("scratch/paradigm_shift_audit_results.json", "w") as f:
    json.dump(audit_results, f, indent=2)

print("\nSaved all results to scratch/paradigm_shift_audit_results.json successfully.", flush=True)
