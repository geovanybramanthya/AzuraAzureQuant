import zipfile, json
from pathlib import Path

zips = sorted(Path("user_data/backtest_results").glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
for z in zips:
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            if n.endswith(".json") and not n.endswith("_config.json"):
                d = json.loads(zf.read(n).decode("utf-8"))
                for strat_name, strat_data in d.get("strategy", {}).items():
                    if "OptionB" in strat_name:
                        print("Found in zip:", z.name)
                        trades = strat_data.get("trades", [])
                        wins = [t for t in trades if t["profit_abs"] > 0]
                        losses = [t for t in trades if t["profit_abs"] <= 0]
                        avg_win = sum(t["profit_abs"] for t in wins)/len(wins) if wins else 0
                        avg_loss = sum(t["profit_abs"] for t in losses)/len(losses) if losses else 0
                        print(f"Strategy: {strat_name}")
                        print(f"Total Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)}")
                        print(f"Winrate: {len(wins)/len(trades)*100:.1f}%")
                        print(f"Avg Win: ${avg_win:.2f} USDT | Avg Loss: ${avg_loss:.2f} USDT")
                        
                        exit_reasons = {}
                        for t in trades:
                            r = t.get("exit_reason", "other")
                            p = t.get("profit_abs", 0)
                            if r not in exit_reasons:
                                exit_reasons[r] = {"count": 0, "wins": 0, "losses": 0, "pnl": 0}
                            exit_reasons[r]["count"] += 1
                            exit_reasons[r]["pnl"] += p
                            if p > 0:
                                exit_reasons[r]["wins"] += 1
                            else:
                                exit_reasons[r]["losses"] += 1
                        
                        print("\nExit Reasons Breakdown:")
                        for r, data in sorted(exit_reasons.items(), key=lambda x: x[1]["count"], reverse=True):
                            print(f"  {r:25s}: {data['count']:3d} trades ({data['wins']:3d} wins, {data['losses']:3d} losses) | PnL: ${data['pnl']:8.2f} USDT")
                        exit(0)
