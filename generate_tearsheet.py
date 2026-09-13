import json, zipfile
from pathlib import Path
import pandas as pd
import quantstats as qs

backtest_dir = Path('user_data/backtest_results')
zips = sorted(backtest_dir.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
latest_zip = zips[0]

with zipfile.ZipFile(latest_zip, 'r') as z:
    trade_files = [f for f in z.namelist() if f.endswith('.json') and not f.endswith('.meta.json')]
    with z.open(trade_files[0]) as f:
        data = json.load(f)

strat = list(data['strategy'].keys())[0]
results = data['strategy'][strat]['results_per_pair']
print('\n' + '='*80)
print(f'GOLDEN 5-PAIR BASKET PERFORMANCE FOR {strat}:')
print('='*80)
for r in results:
    key = r.get('key', '')
    trades = r.get('trades', 0)
    profit_pct = r.get('profit_mean_pct', 0)
    profit_tot = r.get('profit_total_abs', 0)
    wins = r.get('wins', 0)
    losses = r.get('losses', 0)
    win_pct = (wins / trades * 100) if trades > 0 else 0
    print(f'{key:<16} | Trades: {trades:<4} | Wins: {wins:<3} | Losses: {losses:<3} | Winrate: {win_pct:>5.1f}% | Tot Profit: {profit_tot:>8.2f} USDT')
print('='*80)

trades = data['strategy'][strat]['trades']
df_trades = pd.DataFrame(trades)
df_trades['close_date'] = pd.to_datetime(df_trades['close_date'])
df_trades = df_trades.sort_values('close_date')
df_trades['profit_ratio'] = df_trades['profit_ratio'].astype(float)
daily_returns = df_trades.set_index('close_date')['profit_ratio'].resample('D').sum().fillna(0.0)

report_path = Path('user_data/quantstats_tearsheet.html')
qs.reports.html(daily_returns, output=str(report_path), title='Azura Golden 5-Pair Portfolio Alpha - QuantStats Tearsheet')
print(f'\nQuantStats Tearsheet successfully updated at: {report_path.resolve()}')
