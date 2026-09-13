from diff_trades import get_trades

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
t_tri = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-30-16.zip')

d_btc = [t for t in t_dual if 'BTC' in t['pair']]
t_btc = [t for t in t_tri if 'BTC' in t['pair']]

print(f"Dual BTC trades: {len(d_btc)} | PnL: ${sum(t['profit_abs'] for t in d_btc):.2f}")
print(f"Tri BTC trades : {len(t_btc)} | PnL: ${sum(t['profit_abs'] for t in t_btc):.2f}")

print("\nTri BTC losing trades:")
for t in t_btc:
    if t['profit_ratio'] < 0:
        p_ratio = t['profit_ratio']
        p_abs = t['profit_abs']
        print(f"{t['open_date']} short:{str(t['is_short']):5s} {p_ratio:.4f} (${p_abs:6.2f}) | {t['exit_reason']} | {t.get('enter_tag')}")
