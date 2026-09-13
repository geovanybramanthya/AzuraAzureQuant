import zipfile, json
from pathlib import Path

zips = sorted(Path('user_data/backtest_results').glob('*.zip'))
latest = zips[-1]
print('Analyzing backtest:', latest.name)

with zipfile.ZipFile(latest, 'r') as z:
    for name in z.namelist():
        if name.endswith('.json') and not name.endswith('_config.json'):
            data = json.loads(z.read(name).decode('utf-8-sig'))
            strat = list(data['strategy'].keys())[0]
            trades = data['strategy'][strat]['trades']

            by_tag = {}
            for t in trades:
                tag = t.get('enter_tag', 'unknown')
                if tag not in by_tag:
                    by_tag[tag] = {'count': 0, 'wins': 0, 'profit_abs': 0.0, 'profit_pct': 0.0}
                by_tag[tag]['count'] += 1
                if t.get('profit_ratio', 0) > 0:
                    by_tag[tag]['wins'] += 1
                by_tag[tag]['profit_abs'] += t.get('profit_abs', 0.0)
                by_tag[tag]['profit_pct'] += t.get('profit_ratio', 0.0)

            print('=== PERFORMANCE BY ENTRY TAG ===')
            for tag, d in by_tag.items():
                wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
                print(f"{tag:25s} | Trades: {d['count']:4d} | Winrate: {wr:5.1f}% | Profit: ${d['profit_abs']:8.2f}")

            by_pair = {}
            for t in trades:
                pair = t.get('pair', 'unknown')
                if pair not in by_pair:
                    by_pair[pair] = {'count': 0, 'wins': 0, 'profit_abs': 0.0}
                by_pair[pair]['count'] += 1
                if t.get('profit_ratio', 0) > 0:
                    by_pair[pair]['wins'] += 1
                by_pair[pair]['profit_abs'] += t.get('profit_abs', 0.0)

            print('\n=== PERFORMANCE BY PAIR ===')
            for pair, d in by_pair.items():
                wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
                print(f"{pair:20s} | Trades: {d['count']:4d} | Winrate: {wr:5.1f}% | Profit: ${d['profit_abs']:8.2f}")
