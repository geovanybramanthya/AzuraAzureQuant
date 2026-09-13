import json, zipfile
from pathlib import Path

backtest_dir = Path('user_data/backtest_results')
zips = sorted(backtest_dir.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
with zipfile.ZipFile(zips[0], 'r') as z:
    for name in z.namelist():
        if name.endswith('.json') and not name.endswith('_config.json'):
            with z.open(name) as f:
                data = json.load(f)
                break

trades = data['strategy']['PortfolioAlpha_Futures_Strategy']['trades']
long_trades = [t for t in trades if not t.get('is_short', False)]
short_trades = [t for t in trades if t.get('is_short', False)]

print(f'Total Trades Backtest: {len(trades)}')
print(f'Long Trades: {len(long_trades)}')
print(f'Short Trades: {len(short_trades)}')
