import json, zipfile
from pathlib import Path

backtest_dir = Path('user_data/backtest_results')
zips = sorted(backtest_dir.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
latest_zip = zips[0]

with zipfile.ZipFile(latest_zip, 'r') as z:
    trade_files = [f for f in z.namelist() if f.endswith('.json') and not f.endswith('.meta.json')]
    with z.open(trade_files[0]) as f:
        data = json.load(f)

strat = list(data['strategy'].keys())[0]
results = data['strategy'][strat]['results_per_pair']
print('\n' + '='*75)
print(f'PAIR BREAKDOWN FOR {strat}:')
print('='*75)
for r in results:
    key = r.get('key', '')
    trades = r.get('trades', 0)
    profit_pct = r.get('profit_mean_pct', 0)
    profit_tot = r.get('profit_total_abs', 0)
    wins = r.get('wins', 0)
    losses = r.get('losses', 0)
    win_pct = (wins / trades * 100) if trades > 0 else 0
    print(f'{key:<16} | Trades: {trades:<4} | Wins: {wins:<3} | Losses: {losses:<3} | Winrate: {win_pct:>5.1f}% | Tot Profit: {profit_tot:>8.2f} USDT')
print('='*75)
