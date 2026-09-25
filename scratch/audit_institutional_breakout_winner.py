import json
import pandas as pd
import numpy as np
from pathlib import Path

# Load candles and baseline
import sys
sys.path.append('.')
from scratch.test_institutional_breakout_stops import (
    pair_candles, baseline_trades, generate_institutional_breakout_stops, simulate_portfolio
)

print("Running deep audit of the top Institutional Breakout Stop Engine configuration...")

# Config 1: High Conviction Ultra-Safe (Span <= 4.5%, Vol >= 2.6x, TP2 = 3.0R)
trades_c1 = generate_institutional_breakout_stops(
    max_range_span=0.045,
    min_squeeze_streak=2,
    proximity_pct=0.015,
    vol_shock_mult=2.6,
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
)

# Config 2: Maximum Capital Efficiency (Span <= 6.0%, Vol >= 2.2x, TP2 = 3.0R)
trades_c2 = generate_institutional_breakout_stops(
    max_range_span=0.060,
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
)

def audit_trade_set(name, trades):
    df = pd.DataFrame(trades)
    print(f"\n{'='*70}")
    print(f"AUDIT FOR {name} (Total Trades: {len(df)})")
    print(f"{'='*70}")

    df['open_date'] = pd.to_datetime(df['open_date'])
    df['close_date'] = pd.to_datetime(df['close_date'])
    df['duration_h'] = (df['close_date'] - df['open_date']).dt.total_seconds() / 3600.0

    wins = df[df['profit_ratio'] > 0]
    losses = df[df['profit_ratio'] <= 0]
    wr = len(wins) / len(df) * 100.0
    gw = wins['profit_ratio'].sum()
    gl = abs(losses['profit_ratio'].sum())
    pf = gw / gl if gl > 0 else 99.0

    avg_win = wins['profit_ratio'].mean() * 100.0
    avg_loss = losses['profit_ratio'].mean() * 100.0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    print(f"Performance Metrics (after 0.12% Taker Fee + 0.30% Slippage Penalty):")
    print(f"  Win Rate: {wr:.2f}% ({len(wins)} W / {len(losses)} L)")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Avg Win: +{avg_win:.2f}% | Avg Loss: {avg_loss:.2f}% | Payoff Ratio: {payoff:.2f}:1")
    print(f"  Avg Trade Duration: {df['duration_h'].mean():.1f} hours (Median: {df['duration_h'].median():.1f}h)")

    print(f"\nExit Reasons Breakdown:")
    for reason, cnt in df['exit_reason'].value_counts().items():
        pct = cnt / len(df) * 100
        print(f"  {reason:30s}: {cnt:3d} trades ({pct:5.1f}%)")

    print(f"\nPair Breakdown:")
    pair_grp = df.groupby('pair').agg(
        trades=('profit_ratio', 'count'),
        winrate=('profit_ratio', lambda x: round((x > 0).mean() * 100, 1)),
        tot_pnl=('profit_ratio', lambda x: round(x.sum() * 100, 1))
    )
    print(pair_grp.to_string())

    print(f"\nSide Breakdown (Long vs Short):")
    side_grp = df.groupby('side').agg(
        trades=('profit_ratio', 'count'),
        winrate=('profit_ratio', lambda x: round((x > 0).mean() * 100, 1)),
        tot_pnl=('profit_ratio', lambda x: round(x.sum() * 100, 1))
    )
    print(side_grp.to_string())

    # Portfolio simulation
    p_res = simulate_portfolio(trades, stake_scale=0.50)
    print(f"\nPortfolio Integration Results (3 Slots, 0.50x Stake Scale):")
    print(f"  Combined Portfolio Trades: {p_res['total_trades']} (Engine: {p_res['engine_executed']})")
    print(f"  Combined Win Rate: {p_res['winrate_pct']}%")
    print(f"  Combined Max Drawdown: {p_res['max_drawdown_pct']}% (Baseline: 20.56% -> REDUCTION: {20.56 - p_res['max_drawdown_pct']:.2f}%!)")
    print(f"  Final Wallet Balance: ${p_res['final_wallet']:,.2f}")
    print(f"  Profit Factor: {p_res['profit_factor']:.2f}")

    # Stress testing slippage and fees
    print(f"\n--- MICROSTRUCTURE STRESS TEST (Slippage & Taker Fee Sensitivity) ---")
    for slip in [0.0010, 0.0015, 0.0020, 0.0030]:
        for fee in [0.0006, 0.0008, 0.0010]:
            # Adjust profit_ratio for new slippage and fee
            slip_diff = (slip - 0.0015) * 2.0 # entry + exit
            fee_diff = (fee - 0.0006) * 2.0
            total_penalty = slip_diff + fee_diff

            stressed_trades = []
            for t in trades:
                st = dict(t)
                lev = st['leverage']
                st['profit_ratio'] -= (total_penalty * lev)
                stressed_trades.append(st)

            df_st = pd.DataFrame(stressed_trades)
            st_wr = (df_st['profit_ratio'] > 0).mean() * 100
            st_gw = df_st[df_st['profit_ratio'] > 0]['profit_ratio'].sum()
            st_gl = abs(df_st[df_st['profit_ratio'] <= 0]['profit_ratio'].sum())
            st_pf = st_gw / st_gl if st_gl > 0 else 99.0
            st_p_res = simulate_portfolio(stressed_trades, stake_scale=0.50)
            print(f"Slippage: {slip*100:.2f}% | Fee: {fee*100:.2f}%/leg -> Standalone WR: {st_wr:5.1f}%, PF: {st_pf:4.2f} || Port MaxDD: {st_p_res['max_drawdown_pct']:5.2f}%, Wallet: ${st_p_res['final_wallet']:10,.2f}")

    return {
        'name': name,
        'trades': trades,
        'portfolio_res': p_res
    }

res_c1 = audit_trade_set("Config 1: Ultra-Safe Institutional (Span <= 4.5%, Vol >= 2.6x)", trades_c1)
res_c2 = audit_trade_set("Config 2: High-Yield Institutional (Span <= 6.0%, Vol >= 2.2x)", trades_c2)

# Save audit results
audit_data = {
    'config_1': {
        'total_trades': len(trades_c1),
        'portfolio': res_c1['portfolio_res']
    },
    'config_2': {
        'total_trades': len(trades_c2),
        'portfolio': res_c2['portfolio_res']
    }
}
with open("scratch/institutional_breakout_audit_detailed.json", "w") as f:
    json.dump(audit_data, f, indent=2)

print("\nAudit completed and saved to scratch/institutional_breakout_audit_detailed.json.")
