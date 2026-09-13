import zipfile, json
from pathlib import Path
from datetime import datetime, timezone

zip_283 = Path('user_data/backtest_results/backtest-result-2026-08-31_11-11-59.zip')
with zipfile.ZipFile(zip_283, 'r') as z:
    for name in z.namelist():
        if name.endswith('.json') and not name.endswith('_config.json'):
            data = json.loads(z.read(name).decode('utf-8'))
            strat = list(data['strategy'].keys())[0]
            strat_data = data['strategy'][strat]
            trades = strat_data.get('trades', [])
            print(f'Strategy: {strat}, Total trades: {len(trades)}')
            print('First trade:', trades[0]['open_date'], 'Last trade:', trades[-1]['close_date'])
