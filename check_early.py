import zipfile, json
from datetime import datetime

def check_early_trades(zpath):
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json') and not ('_Apex' in name):
                data = json.loads(z.read(name).decode('utf-8-sig'))
                strat = list(data['strategy'].keys())[0]
                trades = data['strategy'][strat]['trades']
                early_trades = [t for t in trades if t['open_date'] < '2024-06-01']
                print(f"=== {strat} Early Trades (< 2024-06-01) ===")
                print(f"Count: {len(early_trades)}")
                by_tag = {}
                total_pnl = 0.0
                for t in early_trades:
                    tag = t.get('enter_tag', 'unknown')
                    if tag not in by_tag:
                        by_tag[tag] = {'count': 0, 'wins': 0, 'pnl': 0.0}
                    by_tag[tag]['count'] += 1
                    if t['profit_ratio'] > 0:
                        by_tag[tag]['wins'] += 1
                    by_tag[tag]['pnl'] += t['profit_abs']
                    total_pnl += t['profit_abs']
                print(f"Early Total PnL: ${total_pnl:.2f}")
                for tag, d in by_tag.items():
                    wr = d['wins'] / d['count'] * 100
                    print(f"  {tag:25s}: {d['count']:3d} trades | Winrate: {wr:5.1f}% | PnL: ${d['pnl']:8.2f}")

check_early_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
check_early_trades('user_data/backtest_results/backtest-result-2026-09-10_10-25-38.zip')
