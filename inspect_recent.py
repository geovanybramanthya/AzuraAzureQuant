import zipfile
import json
from pathlib import Path

zips = sorted(Path('user_data/backtest_results').glob('*.zip'))[-8:]
for zpath in zips:
    with zipfile.ZipFile(zpath, 'r') as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json'):
                content = z.read(name).decode('utf-8-sig', errors='ignore')
                try:
                    data = json.loads(content)
                    strat = list(data.get('strategy', {}).keys())[0]
                    strat_data = data['strategy'][strat]
                    trades = strat_data.get('trades', [])
                    wins = strat_data.get('wins', 0)
                    winrate = wins / len(trades) if trades else 0
                    profit_pct = strat_data.get('profit_total_pct', 0)
                    profit_abs = strat_data.get('profit_total_abs', 0)
                    drawdown = strat_data.get('max_drawdown_account', 0)
                    print(f"{zpath.name} | Strat: {strat} | Trades: {len(trades)} | Winrate: {winrate:.1%} | Profit: {profit_pct:.1f}% (${profit_abs:.2f}) | MaxDD: {drawdown:.1%}")
                except Exception as e:
                    print(f"{zpath.name} error: {e}")
