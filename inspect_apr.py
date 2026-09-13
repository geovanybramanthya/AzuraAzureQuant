from diff_trades import get_trades

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
t_tri = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-26-26.zip')

d_apr = [t for t in t_dual if t['open_date'].startswith('2024-04')]
t_apr = [t for t in t_tri if t['open_date'].startswith('2024-04')]

print(f"Dual Apr trades: {len(d_apr)}")
for t in d_apr:
    print(f"D: {t['open_date']} {t['pair']:15s} short:{str(t['is_short']):5s} {t['profit_ratio']:.4f} (${t['profit_abs']:6.2f}) | {t['exit_reason']}")

print(f"\nTri Apr trades: {len(t_apr)}")
for t in t_apr:
    print(f"T: {t['open_date']} {t['pair']:15s} short:{str(t['is_short']):5s} {t['profit_ratio']:.4f} (${t['profit_abs']:6.2f}) | {t['exit_reason']}")
