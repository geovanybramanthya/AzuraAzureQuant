import sys
sys.path.append('.')
import json
import pandas as pd
from scratch.find_true_pareto_frontier import evaluate_config
from scratch.explore_true_institutional_engine import simulate_portfolio_accurate

print("Simulating Full 4-Slot Portfolio on Top True Pareto Configurations...", flush=True)

configs = [
    {
        'name': 'Config A (Balanced High-Frequency): 166 Trades, 69.88% WR',
        'params': {'vol_mult': 1.7, 'thrust_atr': 1.0, 'headroom_pct': 0.010, 'tp1_r': 0.8, 'pairs_long': ['ADA', 'ETH', 'HYPE', 'SOL'], 'pairs_short': ['HYPE', 'ETH']}
    },
    {
        'name': 'Config B (Institutional High-Conviction): 143 Trades, 71.33% WR',
        'params': {'vol_mult': 1.7, 'thrust_atr': 1.0, 'headroom_pct': 0.014, 'tp1_r': 0.8, 'pairs_long': ['ADA', 'ETH', 'HYPE', 'SOL'], 'pairs_short': ['HYPE', 'ETH']}
    },
    {
        'name': 'Config C (Elite Precision): 120 Trades, 72.50% WR',
        'params': {'vol_mult': 1.5, 'thrust_atr': 1.3, 'headroom_pct': 0.014, 'tp1_r': 0.8, 'pairs_long': ['ADA', 'BTC', 'ETH', 'HYPE', 'SOL'], 'pairs_short': ['BTC', 'HYPE', 'ETH']}
    },
    {
        'name': 'Config D (Ultra-Precision): 88 Trades, 73.86% WR',
        'params': {'vol_mult': 1.7, 'thrust_atr': 1.3, 'headroom_pct': 0.014, 'tp1_r': 0.8, 'pairs_long': ['ADA', 'ETH', 'HYPE', 'SOL'], 'pairs_short': ['HYPE', 'ETH']}
    }
]

for cfg in configs:
    tr = evaluate_config(**cfg['params'])
    df_t = pd.DataFrame(tr)
    wins = (df_t['profit_ratio'] > 0).sum()
    wr = wins / len(df_t) * 100
    gw = df_t[df_t['profit_ratio'] > 0]['profit_ratio'].sum()
    gl = abs(df_t[df_t['profit_ratio'] <= 0]['profit_ratio'].sum())
    pf = gw / gl if gl > 0 else 99.0
    pnl = df_t['profit_ratio'].sum() * 100

    p_res = simulate_portfolio_accurate(tr, stake_scale_engine=0.50)
    t_day = p_res['total_trades'] / 988.0

    print(f"\n--- {cfg['name']} ---")
    print(f"  Standalone Unique Trades: {len(df_t)} | Win Rate: {wr:.2f}% ({wins}W / {len(df_t)-wins}L) | PF: {pf:.2f} | Net PnL: {pnl:+.1f}%")
    print(f"  Portfolio (4 Slots): {p_res['total_trades']} trades ({t_day:.2f}/day, Eng Executed: {p_res['engine_executed']})")
    print(f"  Portfolio Win Rate: {p_res['winrate_pct']:.2f}% | Max DD: {p_res['max_drawdown_pct']:.2f}% | PF: {p_res['profit_factor']:.2f}")
    print(f"  Terminal Wallet: ${p_res['final_wallet']:,.2f} (Baseline: $271,204.55 -> Delta: +${p_res['final_wallet']-271204.55:,.2f})")

    print("\n  Pair Breakdown:")
    p_grp = df_t.groupby('pair').agg(
        trades=('profit_ratio', 'count'),
        winrate=('profit_ratio', lambda x: round((x > 0).mean() * 100, 1)),
        pf=('profit_ratio', lambda x: round(x[x > 0].sum() / abs(x[x <= 0].sum()) if abs(x[x <= 0].sum()) > 0 else 99, 2)),
        tot_pnl=('profit_ratio', lambda x: round(x.sum() * 100, 1))
    )
    print(p_grp.to_string())
