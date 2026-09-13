import zipfile, json

def get_trades(zpath):
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json') and not ('_Apex' in name):
                data = json.loads(z.read(name).decode('utf-8-sig'))
                strat = list(data['strategy'].keys())[0]
                return sorted(data['strategy'][strat]['trades'], key=lambda x: x['open_date'])

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
t_tri = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-26-26.zip')

bal_dual = 25.0
bal_tri = 25.0

print("Monthly PnL comparison:")
months = {}
for t in t_dual:
    m = t['open_date'][:7]
    if m not in months: months[m] = {'dual': 0.0, 'tri': 0.0}
    months[m]['dual'] += t['profit_abs']

for t in t_tri:
    m = t['open_date'][:7]
    if m not in months: months[m] = {'dual': 0.0, 'tri': 0.0}
    months[m]['tri'] += t['profit_abs']

cum_d = 25.0
cum_t = 25.0
for m in sorted(months.keys()):
    d_pnl = months[m]['dual']
    t_pnl = months[m]['tri']
    cum_d += d_pnl
    cum_t += t_pnl
    print(f"{m} | Dual PnL: ${d_pnl:7.2f} (Bal: ${cum_d:6.2f}) | Tri PnL: ${t_pnl:7.2f} (Bal: ${cum_t:6.2f})")
