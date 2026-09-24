import urllib.request
import feedparser

urls = [
    'https://cointelegraph.com/rss',
    'https://decrypt.co/feed',
    'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'https://blockworks.co/feed',
    'https://beincrypto.com/feed/'
]

for url in urls:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            feed = feedparser.parse(data)
            first_title = feed.entries[0].title if feed.entries else "None"
            print(f"SUCCESS [{url}]: {len(feed.entries)} entries. Top: {first_title}")
    except Exception as e:
        print(f"FAILED [{url}]: {e}")
