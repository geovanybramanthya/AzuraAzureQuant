import sys
from pathlib import Path
import json
import pandas as pd
import numpy as np

sys.path.append('scratch')
from simulate_full_dual_layer import exec_comp3, wallet_comp3, dd_comp3

trades_df = pd.DataFrame(exec_comp3)
trades_df['win'] = trades_df['profit_ratio'] > 0
wins = trades_df[trades_df['win']]
losses = trades_df[~trades_df['win']]

wr = len(wins) / len(trades_df) * 100
gross_profit = wins['profit_abs'].sum()
gross_loss = abs(losses['profit_abs'].sum())
pf = gross_profit / gross_loss if gross_loss > 0 else 0

print("=== 3-SLOT MODEL C AUDIT METRICS ===")
print(f"Total Trades: {len(trades_df)}")
print(f"Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {wr:.2f}%")
print(f"Initial Balance: $1,000.00 | Final Balance: ${wallet_comp3:,.2f} | Net Return: +{((wallet_comp3 - 1000)/1000)*100:,.2f}%")
print(f"Gross Profit: ${gross_profit:,.2f} | Gross Loss: ${gross_loss:,.2f} | Profit Factor: {pf:.2f}")
print(f"Max Drawdown: {dd_comp3*100:.2f}%")

l1 = trades_df[~trades_df['is_ai']]
l2 = trades_df[trades_df['is_ai']]
l1_wins = l1[l1['win']]
l2_wins = l2[l2['win']]
print(f"Layer 1 (Core): {len(l1)} trades | WR: {len(l1_wins)/len(l1)*100:.2f}% | PnL: ${l1['profit_abs'].sum():,.2f}")
print(f"Layer 2 (AI):   {len(l2)} trades | WR: {len(l2_wins)/len(l2)*100:.2f}% | PnL: ${l2['profit_abs'].sum():,.2f}")

print("\nPair Breakdown:")
for p, grp in trades_df.groupby('pair'):
    p_wr = (len(grp[grp['win']]) / len(grp)) * 100
    pnl = grp['profit_abs'].sum()
    print(f"  {p:16s}: {len(grp):3d} trades | WR: {p_wr:5.1f}% | Net PnL: ${pnl:10,.2f}")

print("\nExit Reason Breakdown:")
for er, grp in trades_df.groupby('exit_reason'):
    er_wr = (len(grp[grp['win']]) / len(grp)) * 100
    pnl = grp['profit_abs'].sum()
    print(f"  {er:24s}: {len(grp):3d} trades | WR: {er_wr:5.1f}% | Net PnL: ${pnl:10,.2f}")
