import ccxt
import json

ex = ccxt.bybit({'options': {'defaultType': 'linear'}, 'timeout': 10000})
pairs = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'ADA/USDT:USDT',
    'DOGE/USDT:USDT',
    'LINK/USDT:USDT',
    'PAXG/USDT:USDT',
    'HYPE/USDT:USDT'
]

print("--- Testing fetch_funding_rates ---")
try:
    fr = ex.fetch_funding_rates(pairs)
    for p in pairs:
        info = fr.get(p, {})
        rate = info.get('fundingRate')
        dt = info.get('fundingDatetime')
        print(f"{p}: Funding Rate = {rate} | Next Funding = {dt}")
except Exception as e:
    print("fetch_funding_rates error:", e)

print("\n--- Testing fetch_open_interest ---")
for p in ['BTC/USDT:USDT', 'SOL/USDT:USDT', 'HYPE/USDT:USDT']:
    try:
        oi = ex.fetch_open_interest(p)
        print(f"{p}: Open Interest Value = ${float(oi.get('openInterestValue', 0) or 0):,.2f} | Amount = {oi.get('openInterestAmount')}")
    except Exception as e:
        print(f"fetch_open_interest {p} error:", e)

print("\n--- Testing Bybit v5 Market Ticker for 24h Turnover & Predicted Funding ---")
try:
    tickers = ex.fetch_tickers(pairs)
    for p in pairs:
        t = tickers.get(p, {})
        raw_info = t.get('info', {})
        print(f"{p}: markPrice={t.get('close')} | fundingRate={raw_info.get('fundingRate')} | openInterest={raw_info.get('openInterest')} | turnover24h=${float(raw_info.get('turnover24h', 0) or 0):,.2f}")
except Exception as e:
    print("fetch_tickers error:", e)
