import json, zipfile
from pathlib import Path
import numpy as np

backtest_dir = Path('user_data/backtest_results')
zips = sorted(backtest_dir.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
with zipfile.ZipFile(zips[0], 'r') as z:
    for name in z.namelist():
        if name.endswith('.json') and not name.endswith('_config.json'):
            with z.open(name) as f:
                data = json.load(f)
                break

strat_name = 'ApexDualAlpha_Futures_Strategy'
trades = data['strategy'][strat_name]['trades']
profits = [t['profit_ratio'] for t in trades]
print(f'Loaded {len(profits)} trades from ApexDualAlpha. Running 10,000 Monte Carlo permutations...')

n_sims = 10000
final_balances = []
max_drawdowns = []

for _ in range(n_sims):
    shuffled = np.random.choice(profits, size=len(profits), replace=True)
    balance = 25.0
    peak = balance
    max_dd = 0.0
    for p in shuffled:
        stake = (balance * 0.95) / 2.0
        pnl = stake * p * 3.0
        balance += pnl
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if balance <= 1.0:
            break
    final_balances.append(balance)
    max_drawdowns.append(max_dd)

final_balances = np.array(final_balances)
max_drawdowns = np.array(max_drawdowns)

print("================ MONTE CARLO ANALYSIS RESULTS (10,000 RUNS) ================")
print("Total Trades Shuffled:", len(profits))
print("1. Probability of Ruin (Saldo <= $5):", f"{np.mean(final_balances <= 5.0)*100:.2f}%")
print("2. Median Expected Balance:", f"${np.median(final_balances):.2f}", f"(Pertumbuhan +{(np.median(final_balances)/25.0 - 1)*100:.1f}%)")
print("3. 5th Percentile (Kasus 5% Terburuk):", f"${np.percentile(final_balances, 5):.2f}")
print("4. 95th Percentile (Kasus 5% Terbaik):", f"${np.percentile(final_balances, 95):.2f}")
print("5. Median Max Drawdown:", f"{np.median(max_drawdowns)*100:.2f}%")
print("6. 95% Confidence Value at Risk (VaR):", f"{np.percentile(max_drawdowns, 95)*100:.2f}%")
print("============================================================================")
