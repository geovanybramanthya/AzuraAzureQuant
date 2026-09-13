from diff_trades import get_trades

t_dual = get_trades('user_data/backtest_results/backtest-result-2026-09-10_10-23-40.zip')

durations_winners = []
for t in t_dual:
    if t['profit_ratio'] > 0:
        durations_winners.append(t['trade_duration'])

print(f"Total winning trades in ApexDualAlpha: {len(durations_winners)}")
print(f"Winners lasting > 18 hours (1080 min): {sum(d > 1080 for d in durations_winners)} ({sum(d > 1080 for d in durations_winners)/len(durations_winners)*100:.1f}%)")
print(f"Winners lasting > 24 hours (1440 min): {sum(d > 1440 for d in durations_winners)} ({sum(d > 1440 for d in durations_winners)/len(durations_winners)*100:.1f}%)")
