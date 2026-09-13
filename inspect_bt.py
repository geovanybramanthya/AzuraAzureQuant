import zipfile, json
from pathlib import Path
zips = sorted(Path('user_data/backtest_results').glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
if zips:
    with zipfile.ZipFile(zips[0], 'r') as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json'):
                with z.open(name) as f:
                    data = json.load(f)
                    strat = list(data.get('strategy', {}).keys())[0]
                    trades = data['strategy'][strat]['trades']
                    print('Total trades:', len(trades))
                    print('Sample trade keys:', list(trades[0].keys()))
                    print('First trade date:', trades[0].get('open_date'), 'Last trade date:', trades[-1].get('close_date'))
                    print('Sample profit:', trades[0].get('profit_abs'), trades[0].get('profit_ratio'), trades[0].get('close_profit'))
