from diff_trades import get_trades

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-27-32.zip') # task-192
t_tri = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-30-16.zip') # task-236

print(f"Task-192 (Dual max 3) Trades: {len(t_dual)} | Total Profit: ${sum(t['profit_abs'] for t in t_dual):.2f}")
print(f"Task-236 (Tri max 3)  Trades: {len(t_tri)} | Total Profit: ${sum(t['profit_abs'] for t in t_tri):.2f}")

# Find trades in Tri that are NOT in Dual (by open_date + pair)
dual_set = set((t['open_date'], t['pair'], t['is_short']) for t in t_dual)

tri_only = [t for t in t_tri if (t['open_date'], t['pair'], t['is_short']) not in dual_set]
dual_only = [t for t in t_dual if (t['open_date'], t['pair'], t['is_short']) not in set((t['open_date'], t['pair'], t['is_short']) for t in t_tri)]

print(f"\nTrades only in Tri: {len(tri_only)}")
wins_tri_only = sum(t['profit_ratio'] > 0 for t in tri_only)
pnl_tri_only = sum(t['profit_abs'] for t in tri_only)
print(f"Tri-only winrate: {wins_tri_only/len(tri_only)*100:.1f}% | PnL: ${pnl_tri_only:.2f}")

by_tag_tri_only = {}
for t in tri_only:
    tag = t.get('enter_tag', 'unknown')
    if tag not in by_tag_tri_only:
        by_tag_tri_only[tag] = {'count': 0, 'wins': 0, 'pnl': 0.0}
    by_tag_tri_only[tag]['count'] += 1
    if t['profit_ratio'] > 0:
        by_tag_tri_only[tag]['wins'] += 1
    by_tag_tri_only[tag]['pnl'] += t['profit_abs']

for tag, d in by_tag_tri_only.items():
    print(f"  {tag:25s}: {d['count']:3d} trades | Win%: {d['wins']/d['count']*100:5.1f}% | PnL: ${d['pnl']:7.2f}")

print(f"\nTrades only in Dual (displaced in Tri): {len(dual_only)}")
wins_dual_only = sum(t['profit_ratio'] > 0 for t in dual_only)
pnl_dual_only = sum(t['profit_abs'] for t in dual_only)
print(f"Dual-only winrate: {wins_dual_only/len(dual_only)*100:.1f}% | PnL: ${pnl_dual_only:.2f}")
