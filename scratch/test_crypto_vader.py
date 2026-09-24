from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

analyzer = SentimentIntensityAnalyzer()

# Crypto-specific lexicon calibration
crypto_lexicon = {
    # High Positive / Bullish
    'bullish': 3.0,
    'breakout': 2.5,
    'ath': 3.0,
    'all-time high': 3.2,
    'all-time-high': 3.2,
    'rally': 2.4,
    'surge': 2.5,
    'surging': 2.6,
    'pump': 2.2,
    'skyrockets': 3.0,
    'inflow': 2.0,
    'inflows': 2.2,
    'accumulate': 2.0,
    'accumulation': 2.2,
    'approval': 2.8,
    'approved': 2.8,
    'adoption': 2.2,
    'partnership': 2.0,
    'upgrade': 1.8,
    'mainnet': 1.5,
    'halving': 1.8,
    'short squeeze': 2.5,
    'outperform': 2.2,

    # High Negative / Bearish
    'bearish': -3.0,
    'breakdown': -2.5,
    'dump': -2.5,
    'dumping': -2.6,
    'plunge': -2.8,
    'plunges': -2.8,
    'crash': -3.2,
    'crashes': -3.2,
    'liquidation': -2.4,
    'liquidations': -2.6,
    'outflow': -2.0,
    'outflows': -2.2,
    'selloff': -2.5,
    'sell-off': -2.5,
    'bloodbath': -3.5,
    
    # Critical Blackout / Crisis Triggers
    'hack': -3.8,
    'hacked': -4.0,
    'exploit': -3.8,
    'exploited': -4.0,
    'rugpull': -4.0,
    'rug pull': -4.0,
    'scam': -3.5,
    'fraud': -3.8,
    'stolen': -3.5,
    'insolvent': -4.0,
    'insolvency': -4.0,
    'bankrupt': -4.0,
    'bankruptcy': -4.0,
    'lawsuit': -2.8,
    'sued': -3.0,
    'suing': -2.8,
    'subpoena': -2.8,
    'delist': -3.5,
    'delisting': -3.8,
    'delisted': -3.8,
    'ban': -3.0,
    'banned': -3.2,
    'freeze': -3.0,
    'frozen': -3.2
}

analyzer.lexicon.update(crypto_lexicon)

test_headlines = [
    "Bitcoin surges past $80,000 as institutional ETF inflows hit new record high",
    "Solana network suffers massive $50M smart contract exploit, funds stolen",
    "Ethereum breaks out above key resistance with strong validator accumulation",
    "SEC sues crypto platform over alleged illegal unregistered securities offering",
    "Chainlink announces major SWIFT cross-border settlement partnership upgrade",
    "Crypto market crashes as $800M in leveraged long liquidations cascade",
    "Hyperliquid volume skyrockets to new all-time high amid perpetual trading boom",
    "Dogecoin slumps 8% in market-wide weekend selloff as outflows accelerate"
]

print("=== VADER CRYPTO SENTIMENT TEST ===")
for h in test_headlines:
    scores = analyzer.polarity_scores(h)
    comp = scores['compound']
    tag = "BULLISH" if comp >= 0.25 else ("BEARISH" if comp <= -0.25 else "NEUTRAL")
    if any(k in h.lower() for k in ['hack', 'exploit', 'stolen', 'insolvent', 'delist', 'sued']):
        if comp < -0.4:
            tag = "CRITICAL_BLACKOUT"
    print(f"[{comp:+.3f}] [{tag:17}] {h}")
