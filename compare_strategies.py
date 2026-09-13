import zipfile, json

def inspect_zip(zpath):
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.endswith('.json') and not name.endswith('_config.json') and not ('_Apex' in name):
                data = json.loads(z.read(name).decode('utf-8-sig'))
                strat = list(data['strategy'].keys())[0]
                sdata = data['strategy'][strat]
                trades = sdata['trades']
                win_trades = [t for t in trades if t['profit_ratio'] > 0]
                loss_trades = [t for t in trades if t['profit_ratio'] <= 0]
                
                avg_win = sum(t['profit_ratio'] for t in win_trades) / len(win_trades) if win_trades else 0
                avg_loss = sum(t['profit_ratio'] for t in loss_trades) / len(loss_trades) if loss_trades else 0
                
                avg_win_abs = sum(t['profit_abs'] for t in win_trades) / len(win_trades) if win_trades else 0
                avg_loss_abs = sum(t['profit_abs'] for t in loss_trades) / len(loss_trades) if loss_trades else 0
                
                print(f"=== {strat} ({zpath}) ===")
                print(f"Trades: {len(trades)} | Wins: {len(win_trades)} | Losses: {len(loss_trades)}")
                print(f"Winrate: {len(win_trades)/len(trades):.2%}")
                print(f"Profit USDT: ${sdata.get('profit_total_abs', 0):.2f} | Profit %: {sdata.get('profit_total_pct', 0):.2f}%")
                print(f"Avg Win: {avg_win:.2%} (${avg_win_abs:.2f}) | Avg Loss: {avg_loss:.2%} (${avg_loss_abs:.2f})")
                print(f"Profit Factor: {abs(sum(t['profit_abs'] for t in win_trades) / (sum(t['profit_abs'] for t in loss_trades) or 1)):.2f}")
                
                # Check exit reasons
                by_exit = {}
                for t in trades:
                    ex = t.get('exit_reason', 'unknown')
                    if ex not in by_exit:
                        by_exit[ex] = {'count': 0, 'wins': 0, 'losses': 0, 'profit_abs': 0.0}
                    by_exit[ex]['count'] += 1
                    if t['profit_ratio'] > 0:
                        by_exit[ex]['wins'] += 1
                    else:
                        by_exit[ex]['losses'] += 1
                    by_exit[ex]['profit_abs'] += t['profit_abs']
                print("Exit breakdown:")
                for ex, d in by_exit.items():
                    print(f"  {ex:25s}: {d['count']:3d} trades (W:{d['wins']} L:{d['losses']}) | ${d['profit_abs']:8.2f}")

inspect_zip('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')
inspect_zip('user_data/backtest_results/backtest-result-2026-09-10_10-25-38.zip')
