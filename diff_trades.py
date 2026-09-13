import zipfile, json

def get_trades(zpath):
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json') and not ('_Apex' in name):
                data = json.loads(z.read(name).decode('utf-8-sig'))
                strat = list(data['strategy'].keys())[0]
                return data['strategy'][strat]['trades']

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
t_tri = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-26-26.zip')

print(f"Dual Trades: {len(t_dual)}, Tri Trades: {len(t_tri)}")

# Compare first 20 trades
for i in range(min(15, len(t_dual))):
    d = t_dual[i]
    tr = t_tri[i]
    print(f"Dual: {d['open_date']} {d['pair']} {d['is_short']} profit: {d['profit_ratio']:.4f} (${d['profit_abs']:.2f}) | {d['exit_reason']}")
    print(f" Tri: {tr['open_date']} {tr['pair']} {tr['is_short']} profit: {tr['profit_ratio']:.4f} (${tr['profit_abs']:.2f}) | {tr['exit_reason']}")
    print("---")
