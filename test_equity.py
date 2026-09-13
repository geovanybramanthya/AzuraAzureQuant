import zipfile, json
from pathlib import Path
from datetime import datetime

zip_283 = Path('user_data/backtest_results/backtest-result-2026-08-31_11-11-59.zip')
trades = []
with zipfile.ZipFile(zip_283, 'r') as z:
    for name in z.namelist():
        if name.endswith('.json') and not name.endswith('_config.json'):
            raw = z.read(name).decode('utf-8-sig')
            data = json.loads(raw)
            strat = list(data['strategy'].keys())[0]
            trades = data['strategy'][strat]['trades']
            break

print(f'Total trades loaded: {len(trades)}')
# Sort by close_date
trades = sorted(trades, key=lambda x: x['close_date'])

balance = 25.0
equity_all = []
for t in trades:
    p_abs = t.get('profit_abs', 0.0)
    balance += p_abs
    equity_all.append({
        'date': t['close_date'][:16],
        'equity': round(balance, 2),
        'pair': t['pair'],
        'profit': round(p_abs, 2),
        'is_short': t['is_short']
    })

print(f'Starting: .00 -> Ending:  ({len(equity_all)} points)')
print('Last 5 points:', equity_all[-5:])
