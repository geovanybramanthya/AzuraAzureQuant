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

    pair_candles[p] = df
    if fn_15m.exists():
        pair_15m[p] = pd.read_feather(fn_15m)
    if fn_fund.exists():
        pair_funding[p] = pd.read_feather(fn_fund)

print("Data loaded successfully.")

# Baseline portfolio trades
bench_file = Path("user_data/backtest_results/benchmark_model_c_true_optimized.json")
with open(bench_file, "r", encoding="utf-8") as f:
    bench_data = json.load(f)
baseline_trades = [dict(t, is_stop_engine=False) for t in bench_data["trades"]]

def generate_naive_breakout_trades(
    max_range_span=0.045,
    min_squeeze_streak=2,
    proximity_pct=0.015,
    vol_shock_mult=2.2,
    delta_atr=0.15,
    tp1_r=1.2,
    tp2_r=3.0,
    be_buffer_r=0.15,
    sl_pct=0.015,
    rapid_invalidation_h=3,
    rapid_invalidation_spot=-0.005,
    max_hold_h=24,
    order_validity_hours=8,
    direction='bidirectional',
    slippage_entry=0.0015,
    slippage_exit=0.0015,
    taker_fee=0.0006
):
    trades = []
    for p in pairs:
        if p not in pair_candles: continue
        df = pair_candles[p]
        lev = 7.0 if p == 'BTC' else 3.0

        range_pos = (df['close'] - df['rolling_low_48']) / (df['rolling_high_48'] - df['rolling_low_48'])
        dist_to_res = (df['rolling_high_48'] - df['close']) / df['close']
        dist_to_sup = (df['close'] - df['rolling_low_48']) / df['close']

        long_setup = (
            (df['macro_bull_4h'] == True) &
            (df['adx_4h'] >= 22.0) &
            (df['range_span_48'] <= max_range_span) &
            ((df['squeeze_streak'] >= min_squeeze_streak) | (df['bb_width'] <= df['bb_width_q20'])) &
            (range_pos >= 0.65) &
            (dist_to_res <= proximity_pct) &
            (dist_to_res >= 0.001) &
            (df['rising_floor_12_24'] == True) &
            (df['rsi'] >= 52.0) & (df['rsi'] <= 68.0) &
            (df['close'] > df['ema_9'])
        )

        short_setup = (
            (df['macro_bear_4h'] == True) &
            (df['adx_4h'] >= 22.0) &
            (df['range_span_48'] <= max_range_span) &
            ((df['squeeze_streak'] >= min_squeeze_streak) | (df['bb_width'] <= df['bb_width_q20'])) &
            (range_pos <= 0.35) &
            (dist_to_sup <= proximity_pct) &
            (dist_to_sup >= 0.001) &
            (df['falling_ceiling_12_24'] == True) &
            (df['rsi'] >= 32.0) & (df['rsi'] <= 48.0) &
            (df['close'] < df['ema_9'])
        )

        n_rows = len(df)
        last_filled_idx = -999

        for idx in range(100, n_rows - max_hold_h - 2):
            if idx <= last_filled_idx: continue
            candle = df.iloc[idx]
            res_48 = candle['rolling_high_48']
            sup_48 = candle['rolling_low_48']
            atr_val = candle['atr']

            if pd.isna(res_48) or pd.isna(sup_48) or pd.isna(atr_val) or atr_val <= 0: continue

            is_long = bool(long_setup.iloc[idx]) and (direction in ['bidirectional', 'long_only'])
            is_short = bool(short_setup.iloc[idx]) and (direction in ['bidirectional', 'short_only'])
            if not (is_long or is_short): continue

            side = 'long' if is_long else 'short'
            trigger_p = res_48 + delta_atr * atr_val if side == 'long' else sup_48 - delta_atr * atr_val

            filled = False
            fill_idx = -1
            fill_candle = None

            for v_step in range(1, order_validity_hours + 1):
                f_idx = idx + v_step
                if f_idx >= n_rows: break
                check_c = df.iloc[f_idx]

                if side == 'long':
                    if check_c['high'] >= trigger_p:
                        if vol_shock_mult > 1.0 and check_c['volume'] < check_c['vol_mean_20'] * vol_shock_mult:
                            continue
                        filled = True
                        fill_idx = f_idx
                        fill_candle = check_c
                        break
                else:
                    if check_c['low'] <= trigger_p:
                        if vol_shock_mult > 1.0 and check_c['volume'] < check_c['vol_mean_20'] * vol_shock_mult:
                            continue
                        filled = True
                        fill_idx = f_idx
                        fill_candle = check_c
                        break

            if not filled: continue

            entry_p = trigger_p * (1.0 + slippage_entry) if side == 'long' else trigger_p * (1.0 - slippage_entry)
            risk = entry_p * sl_pct
            stop_loss = entry_p - risk if side == 'long' else entry_p + risk
            tp1 = entry_p + tp1_r * risk if side == 'long' else entry_p - tp1_r * risk
            tp2 = entry_p + tp2_r * risk if side == 'long' else entry_p - tp2_r * risk

            entry_date = fill_candle['date']
            tp1_hit = False
            pnl_tp1 = 0.0
            pnl_tp2 = 0.0
            exit_reason = f'breakout_{max_hold_h}h_timeout'
            exit_date = None
            bars_held = 0

            # Check intra-bar action on fill candle
            intra_bar_sl_hit = False
            if side == 'long':
                if fill_candle['low'] <= stop_loss:
                    intra_bar_sl_hit = True
            else:
                if fill_candle['high'] >= stop_loss:
                    intra_bar_sl_hit = True

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
                        exit_reason = 'breakout_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['low'] <= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (stop_loss - entry_p) / entry_p - slippage_exit
                            exit_reason = 'breakout_be_after_tp1'
                        else:
                            pnl_tp1 = (stop_loss - entry_p) / entry_p - slippage_exit
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'breakout_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (c_bar['close'] - entry_p) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] < res_48 and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 - slippage_exit)
                            pnl_tp1 = (exit_p - entry_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'breakout_trap_invalidation_{p_step}h'
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
                        exit_reason = 'breakout_tp2_runner'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    if c_bar['high'] >= stop_loss:
                        if tp1_hit:
                            pnl_tp2 = (entry_p - stop_loss) / entry_p - slippage_exit
                            exit_reason = 'breakout_be_after_tp1'
                        else:
                            pnl_tp1 = (entry_p - stop_loss) / entry_p - slippage_exit
                            pnl_tp2 = pnl_tp1
                            exit_reason = 'breakout_hard_sl'
                        exit_date = c_bar['date']
                        last_filled_idx = cur_idx
                        break

                    cur_spot = (entry_p - c_bar['close']) / entry_p
                    if p_step <= rapid_invalidation_h and not tp1_hit:
                        if c_bar['close'] > sup_48 and cur_spot <= rapid_invalidation_spot:
                            exit_p = c_bar['close'] * (1.0 + slippage_exit)
                            pnl_tp1 = (entry_p - exit_p) / entry_p
                            pnl_tp2 = pnl_tp1
                            exit_reason = f'breakout_trap_invalidation_{p_step}h'
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
            gross_spot_pnl = tot_spot_pnl
            friction_spot = (taker_fee * 2.0)
            net_spot_pnl = gross_spot_pnl - friction_spot
            net_leveraged_pnl = net_spot_pnl * lev

            # Failure taxonomy classification
            loss_category = "NOT_A_LOSS"
            if net_leveraged_pnl <= 0:
                if gross_spot_pnl > 0 and net_spot_pnl <= 0:
                    loss_category = "Adverse Selection (Fee/Slippage Drag)"
                elif intra_bar_sl_hit or (bars_held <= 1 and exit_reason == 'breakout_hard_sl'):
                    loss_category = "Intra-bar Stopout (Whipsaw)"
                elif 'trap_invalidation' in exit_reason or (bars_held <= 3 and max_favorable_excursion < 0.005):
                    loss_category = "Liquidity Sweep / Fakeout Wick"
                elif 'timeout' in exit_reason or bars_held >= 12:
                    loss_category = "Premature Exit / Squeeze Re-entry (Chop Bleed)"
                else:
                    loss_category = "Liquidity Sweep / Fakeout Wick"

            trades.append({
                'pair': f"{p}/USDT:USDT",
                'side': side,
                'is_short': (side == 'short'),
                'open_date': str(entry_date)[:19],
                'close_date': str(exit_date or fill_candle['date'])[:19],
                'profit_ratio': round(float(net_leveraged_pnl), 6),
                'spot_pnl': round(float(net_spot_pnl), 6),
                'gross_spot_pnl': round(float(gross_spot_pnl), 6),
                'friction_spot': round(float(friction_spot), 6),
                'open_rate': round(float(entry_p), 4),
                'exit_reason': exit_reason,
                'enter_tag': f'ai_breakout_stop_{side}',
                'leverage': lev,
                'is_ai': True,
                'is_stop_engine': True,
                'bars_held': bars_held,
                'mfe': round(float(max_favorable_excursion), 4),
                'mae': round(float(max_adverse_excursion), 4),
                'loss_category': loss_category
            })

    return trades

# Portfolio Simulator with max_slots parameter
w_scale = 0.95896084
l_scale = 0.70047743
dd_brake_thresh = 0.15520307
dd_brake_scale = 0.88817569

def simulate_portfolio(stop_trades, stake_scale=0.50, max_slots=4):
    all_trades = baseline_trades + stop_trades
    df_all = pd.DataFrame(all_trades)
    df_all['open_date'] = pd.to_datetime(df_all['open_date'], utc=True)
    df_all['close_date'] = pd.to_datetime(df_all['close_date'], utc=True)
    df_all = df_all.sort_values('open_date').reset_index(drop=True)

    for i, t in df_all.iterrows():
        overlaps = df_all[(df_all['open_date'] < t['close_date']) & (df_all['close_date'] > t['open_date']) & (df_all.index < i)]
        same_side = overlaps[overlaps['is_short'] == t['is_short']]
        df_all.loc[i, 'active_same_side_at_entry'] = len(same_side)
        df_all.loc[i, 'active_total_at_entry'] = len(overlaps)

    wallet = 1000.0
    peak = 1000.0
    max_dd = 0.0
    gross_w = 0.0
    gross_l = 0.0
    executed = []

    for idx in range(len(df_all)):
        r = df_all.iloc[idx]
        if r['active_total_at_entry'] >= max_slots:
            continue

        cur_dd = (peak - wallet) / peak if peak > 0 else 0
        brake = dd_brake_scale if cur_dd >= dd_brake_thresh else 1.0

        # With 4 slots, base stake denominator is max_slots (4.0) or 3.0?
        # User specified: "3 Core slots + 1 Dedicated Engine slot" = 4 slots
        base_stake = (wallet / float(max_slots)) * 0.95 * brake
        if r['active_same_side_at_entry'] >= 2:
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

# Run naive breakout generation
naive_trades = generate_naive_breakout_trades()
df_naive = pd.DataFrame(naive_trades)

print("\n" + "="*80)
print("NAIVE STOP-MARKET BREAKOUT ENGINE AUDIT")
print("="*80)

# Check all pairs vs altcoins only
for name, tr_set in [
    ("All 8 Pairs (Original Naive)", naive_trades),
    ("Altcoins Only (Ex-BTC)", [t for t in naive_trades if 'BTC' not in t['pair']]),
    ("BTC Only", [t for t in naive_trades if 'BTC' in t['pair']])
]:
    df_s = pd.DataFrame(tr_set)
    wins = df_s[df_s['profit_ratio'] > 0]
    losses = df_s[df_s['profit_ratio'] <= 0]
    wr = len(wins) / len(df_s) * 100.0 if len(df_s) > 0 else 0
    gw = wins['profit_ratio'].sum()
    gl = abs(losses['profit_ratio'].sum())
    pf = gw / gl if gl > 0 else 99.0
    print(f"\n{name}: Total: {len(df_s)}, Wins: {len(wins)}, Losses: {len(losses)}, WR: {wr:.2f}%, PF: {pf:.2f}")

    if name == "Altcoins Only (Ex-BTC)":
        print("\n--- FORENSIC LOSS BREAKDOWN (Altcoins Only) ---")
        l_df = df_s[df_s['profit_ratio'] <= 0]
        cat_counts = l_df['loss_category'].value_counts()
        for cat, cnt in cat_counts.items():
            pct = cnt / len(l_df) * 100.0
            print(f"  {cat:45s}: {cnt:3d} losses ({pct:5.1f}%)")

        print("\n--- LOSS TAXONOMY BY PAIR ---")
        p_cross = pd.crosstab(l_df['pair'], l_df['loss_category'], margins=True)
        print(p_cross.to_string())

        print("\n--- LOSS TAXONOMY BY EXIT REASON ---")
        e_cross = pd.crosstab(l_df['exit_reason'], l_df['loss_category'], margins=True)
        print(e_cross.to_string())

        print("\n--- SAMPLE LOSING TRADES FORENSICS ---")
        cols = ['pair', 'side', 'open_date', 'bars_held', 'mfe', 'mae', 'profit_ratio', 'exit_reason', 'loss_category']
        print(l_df[cols].head(15).to_string())
