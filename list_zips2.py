import zipfile, json
from pathlib import Path

zips = sorted(Path('user_data/backtest_results').glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
for zpath in zips:
    with zipfile.ZipFile(zpath, 'r') as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json'):
                try:
                    data = json.loads(z.read(name).decode('utf-8'))
                    strat = list(data.get('strategy', {}).keys())[0]
                    strat_data = data['strategy'][strat]
                    t_list = strat_data.get('trades', [])
                    print('ZIP:', zpath.name, '| Strat:', strat, '| Trades:', len(t_list), '| Total Profit:', strat_data.get('profit_total_pct'))
                except Exception as e:
                    pass
