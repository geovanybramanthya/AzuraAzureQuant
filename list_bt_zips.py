import zipfile, json
from pathlib import Path

zips = sorted(Path('user_data/backtest_results').glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
print('Found backtest zips:')
for zpath in zips:
    try:
        with zipfile.ZipFile(zpath, 'r') as z:
            for name in z.namelist():
                if name.endswith('.json') and not name.endswith('_config.json'):
                    content = z.read(name).decode('utf-8')
                    # fix unescaped windows path if any
                    data = json.loads(content)
                    strat = list(data.get('strategy', {}).keys())[0]
                    trades = data['strategy'][strat]['trades']
                    summary = data['strategy'][strat]['summary']
                    print(f'Zip: {zpath.name} | Strat: {strat} | Trades: {len(trades)} | Profit: {summary.get( profit_total_pct)}%')
    except Exception as e:
        print(f'Zip: {zpath.name} error: {e}')
