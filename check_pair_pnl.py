import zipfile, json

with zipfile.ZipFile('user_data/backtest_results/backtest-result-2026-09-10_10-14-30.zip') as z:
    for name in z.namelist():
        if name == 'backtest-result-2026-09-10_10-14-30.json':
            data = json.loads(z.read(name).decode('utf-8-sig'))
            strat = list(data['strategy'].keys())[0]
            trades = data['strategy'][strat]['trades']
            
            by_pair = {}
            for t in trades:
                p = t['pair']
                if p not in by_pair:
                    by_pair[p] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
                by_pair[p]['trades'] += 1
                if t['profit_ratio'] > 0:
                    by_pair[p]['wins'] += 1
                by_pair[p]['pnl'] += t['profit_abs']
                
            print("=== Performance by Pair in 10-14-30 (14 pairs) ===")
            for p, d in sorted(by_pair.items(), key=lambda x: x[1]['pnl'], reverse=True):
                wr = d['wins'] / d['trades'] * 100
                print(f"{p:20s} | Trades: {d['trades']:3d} | Winrate: {wr:5.1f}% | PnL: ${d['pnl']:7.2f}")
