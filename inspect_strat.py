import zipfile, json
from pathlib import Path

zips = sorted(Path('user_data/backtest_results').glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
for zpath in zips[:5]:
    try:
        with zipfile.ZipFile(zpath, 'r') as z:
            for name in z.namelist():
                if name.endswith('.json') and not name.endswith('_config.json'):
                    data = json.loads(z.read(name).decode('utf-8'))
                    strat = list(data.get('strategy', {}).keys())[0]
                    strat_data = data['strategy'][strat]
                    print(f'Zip: {zpath.name} | Strat: {strat} | Trades: {len(strat_data.get( trades, []))} | Keys: {list(strat_data.keys())}')
    except Exception as e:
        print(f'Zip: {zpath.name} err: {e}')
