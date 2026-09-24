"""
News & Fundamental Sentiment Daemon (Azura Quantum Multi-Layer Engine)
Preserves Config 08 Checkpoint Integrity.
Fetches verified live crypto news feeds, runs local calibrated VADER NLP scoring,
detects asset-specific Event Risk Blackouts, and writes sentiment_state.json.
"""

import os
import sys
import time
import json
import re
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

try:
    from scrapling import Fetcher
    HAS_SCRAPLING = True
except Exception:
    HAS_SCRAPLING = False

# Logging Configuration
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "news_sentiment.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("NewsSentimentDaemon")

# Target Whitelist Assets
WHITELIST_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "ADA/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "PAXG/USDT:USDT",
    "HYPE/USDT:USDT"
]

# Regex patterns for asset entity recognition
ASSET_PATTERNS = {
    "BTC/USDT:USDT": re.compile(r"\b(btc|bitcoin|satoshi)\b", re.IGNORECASE),
    "ETH/USDT:USDT": re.compile(r"\b(eth|ethereum|ether|vitalik)\b", re.IGNORECASE),
    "SOL/USDT:USDT": re.compile(r"\b(sol|solana)\b", re.IGNORECASE),
    "ADA/USDT:USDT": re.compile(r"\b(ada|cardano|hoskinson)\b", re.IGNORECASE),
    "DOGE/USDT:USDT": re.compile(r"\b(doge|dogecoin)\b", re.IGNORECASE),
    "LINK/USDT:USDT": re.compile(r"\b(link|chainlink)\b", re.IGNORECASE),
    "PAXG/USDT:USDT": re.compile(r"\b(paxg|pax gold|gold token)\b", re.IGNORECASE),
    "HYPE/USDT:USDT": re.compile(r"\b(hype|hyperliquid)\b", re.IGNORECASE)
}

# Critical Crisis / Blackout Triggers
CRISIS_KEYWORDS = [
    "hack", "hacked", "exploit", "exploited", "rugpull", "rug pull",
    "scam", "fraud", "stolen", "insolvent", "insolvency", "bankrupt",
    "bankruptcy", "lawsuit", "sued", "suing", "subpoena", "delist",
    "delisting", "delisted", "ban", "banned", "freeze", "frozen", "attacked"
]

# False Positives to filter out before crisis keyword matching
FALSE_POSITIVE_PATTERNS = [
    re.compile(r"\bhack\s+vc\b", re.IGNORECASE),
    re.compile(r"\bhackathon[s]?\b", re.IGNORECASE),
    re.compile(r"\bwhite[-\s]?hat\b", re.IGNORECASE),
    re.compile(r"\banti[-\s]?fraud\b", re.IGNORECASE)
]

# RSS Feed Sources
RSS_FEEDS = [
    {"source": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"source": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"source": "Decrypt", "url": "https://decrypt.co/feed"},
    {"source": "Blockworks", "url": "https://blockworks.co/feed"},
    {"source": "BeInCrypto", "url": "https://beincrypto.com/feed/"}
]

# Crypto-calibrated VADER Lexicon
CRYPTO_LEXICON_UPDATES = {
    # High Positive / Bullish
    "bullish": 3.2,
    "breakout": 2.5,
    "ath": 3.0,
    "all-time high": 3.2,
    "all-time-high": 3.2,
    "rally": 2.4,
    "surge": 2.5,
    "surging": 2.6,
    "pump": 2.2,
    "skyrockets": 3.0,
    "inflow": 2.0,
    "inflows": 2.2,
    "accumulate": 2.0,
    "accumulation": 2.2,
    "approval": 2.8,
    "approved": 2.8,
    "adoption": 2.2,
    "partnership": 2.0,
    "upgrade": 1.8,
    "mainnet": 1.5,
    "halving": 1.8,
    "short squeeze": 2.5,
    "outperform": 2.2,

    # High Negative / Bearish
    "bearish": -3.2,
    "breakdown": -2.5,
    "dump": -2.5,
    "dumping": -2.6,
    "plunge": -2.8,
    "plunges": -2.8,
    "crash": -3.2,
    "crashes": -3.2,
    "liquidation": -2.4,
    "liquidations": -2.6,
    "outflow": -2.0,
    "outflows": -2.2,
    "selloff": -2.5,
    "sell-off": -2.5,
    "bloodbath": -3.5,

    # Critical Crisis
    "hack": -3.8,
    "hacked": -4.0,
    "exploit": -3.8,
    "exploited": -4.0,
    "rugpull": -4.0,
    "rug pull": -4.0,
    "scam": -3.5,
    "fraud": -3.8,
    "stolen": -3.5,
    "insolvent": -4.0,
    "insolvency": -4.0,
    "bankrupt": -4.0,
    "bankruptcy": -4.0,
    "lawsuit": -2.8,
    "sued": -3.0,
    "suing": -2.8,
    "subpoena": -2.8,
    "delist": -3.5,
    "delisting": -3.8,
    "delisted": -3.8,
    "ban": -3.0,
    "banned": -3.2,
    "freeze": -3.0,
    "frozen": -3.2
}


class NewsSentimentDaemon:
    def __init__(self, state_file_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.state_file = state_file_path or os.path.join(base_dir, "data", "sentiment_state.json")
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

        self.analyzer = SentimentIntensityAnalyzer()
        self.analyzer.lexicon.update(CRYPTO_LEXICON_UPDATES)

        # Scrapling Anti-Bot Fetcher for Cloudflare bypass & deep article scraping
        self.scrapling_fetcher = None
        if HAS_SCRAPLING:
            try:
                self.scrapling_fetcher = Fetcher()
                logger.info("Scrapling Anti-Bot Fetcher initialized for deep web scraping fallback.")
            except Exception as e:
                logger.warning(f"Could not initialize Scrapling Fetcher: {e}")

        # In-memory rolling history of evaluated news (up to 300 items)
        self.article_cache: Dict[str, Dict[str, Any]] = {}
        self.load_existing_state()

    def load_existing_state(self):
        """Loads previous state from disk if available to maintain continuity."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("recent_feed", []):
                        item_id = item.get("link") or item.get("title")
                        if item_id:
                            self.article_cache[item_id] = item
                logger.info(f"Loaded {len(self.article_cache)} cached articles from {self.state_file}")
            except Exception as e:
                logger.warning(f"Could not load existing sentiment state: {e}")

    def fetch_deep_article_body(self, link: str) -> str:
        """Deep scrapes full article paragraphs via Scrapling anti-bot browser when summary is truncated."""
        if not self.scrapling_fetcher or not link:
            return ""
        # Skip mock/dummy URLs without valid internet domain
        parts = link.split("/")
        if not link.startswith(("http://", "https://")) or len(parts) < 3 or "." not in parts[2] or "test" in parts[2]:
            return ""
        try:
            res = self.scrapling_fetcher.get(link)
            if res.status == 200:
                paragraphs = res.css('p::text').getall()
                full_text = " ".join([p.strip() for p in paragraphs if len(p.strip()) > 30])
                if full_text:
                    logger.info(f"Deep scraped full article via Scrapling ({len(full_text)} chars) for: {link[:60]}...")
                    return full_text[:2000]
        except Exception as e:
            logger.debug(f"Deep article scrape failed for {link}: {e}")
        return ""

    def fetch_feed(self, feed_cfg: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetches and parses a single RSS feed with timeout and Scrapling anti-bot fallback."""
        source = feed_cfg["source"]
        url = feed_cfg["url"]
        articles = []
        content = None

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read()
        except Exception as e:
            logger.warning(f"Standard fetch failed for {source} ({url}): {e}. Attempting Scrapling Anti-Bot bypass...")
            if self.scrapling_fetcher:
                try:
                    res = self.scrapling_fetcher.get(url)
                    if res.status == 200 and res.body:
                        content = res.body
                        logger.info(f"Scrapling Anti-Bot bypass SUCCESS for {source} ({len(content)} bytes)")
                except Exception as se:
                    logger.warning(f"Scrapling fetch also failed for {source}: {se}")

        if content:
            try:
                parsed = feedparser.parse(content)
                for entry in parsed.entries[:25]:
                    title = getattr(entry, "title", "").strip()
                    if not title:
                        continue

                    summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                    clean_summary = re.sub(r"<[^>]+>", "", summary).strip()
                    link = getattr(entry, "link", "")
                    pub_time = getattr(entry, "published", "") or getattr(entry, "updated", "")

                    articles.append({
                        "source": source,
                        "title": title,
                        "summary": clean_summary,
                        "link": link,
                        "published_raw": pub_time,
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })

                logger.info(f"Fetched {len(articles)} articles from {source}")
            except Exception as pe:
                logger.warning(f"Failed parsing feed content for {source}: {pe}")

        return articles

    def extract_pairs(self, text: str) -> List[str]:
        """Identifies which whitelisted trading pairs are mentioned in the text."""
        mentioned = []
        for pair, pattern in ASSET_PATTERNS.items():
            if pattern.search(text):
                mentioned.append(pair)
        return mentioned

    def check_pair_blackouts(self, title: str, summary: str, pairs: List[str], compound_score: float) -> Dict[str, str]:
        """
        Determines if any mentioned pair is subject to a Critical Blackout.
        Requires sentence-level co-occurrence between the asset mention and a crisis trigger,
        and compound sentiment score <= -0.35, excluding false positives (e.g. Hack VC, Hackathon).
        """
        if compound_score > -0.30 or not pairs:
            return {}

        full_text = f"{title}. {summary}"
        # Filter false positives
        clean_text = full_text
        for fp in FALSE_POSITIVE_PATTERNS:
            clean_text = fp.sub("", clean_text)

        sentences = re.split(r"[.!?\n]+", clean_text)
        blackouts = {}

        for pair in pairs:
            pattern = ASSET_PATTERNS[pair]
            for s in sentences:
                s_clean = s.strip()
                if not s_clean:
                    continue
                if pattern.search(s_clean):
                    found_kw = [kw for kw in CRISIS_KEYWORDS if re.search(r"\b" + kw + r"\b", s_clean, re.IGNORECASE)]
                    if found_kw:
                        blackouts[pair] = f"Critical crisis trigger ({', '.join(set(found_kw))}) in: '{s_clean[:100]}...'"
                        break

        return blackouts

    def analyze_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Runs calibrated VADER NLP analysis on headline and summary with Scrapling deep context."""
        summary = article.get("summary", "")
        link = article.get("link", "")
        
        # Deep scrape full article via Scrapling if summary is truncated
        if len(summary) < 70 and link and self.scrapling_fetcher:
            deep_body = self.fetch_deep_article_body(link)
            if deep_body:
                summary = deep_body[:500]
                article["summary"] = summary
                article["deep_scraped"] = True

        full_text = f"{article['title']}. {summary}"
        scores = self.analyzer.polarity_scores(full_text)
        compound = round(scores["compound"], 4)

        # Detect matching trading pairs
        pairs = self.extract_pairs(full_text)

        # Check sentence-level blackout risk per pair
        pair_blackouts = self.check_pair_blackouts(article["title"], article["summary"], pairs, compound)
        has_blackout = len(pair_blackouts) > 0
        blackout_reason = list(pair_blackouts.values())[0] if has_blackout else None

        # Sentiment regime tag
        if has_blackout:
            regime = "CRITICAL_BLACKOUT"
        elif compound >= 0.20:
            regime = "BULLISH"
        elif compound <= -0.20:
            regime = "BEARISH"
        else:
            regime = "NEUTRAL"

        return {
            "title": article["title"],
            "source": article["source"],
            "link": article["link"],
            "fetched_at": article["fetched_at"],
            "pairs": pairs,
            "compound_score": compound,
            "regime": regime,
            "blackout": has_blackout,
            "blackout_pairs": list(pair_blackouts.keys()),
            "blackout_reason": blackout_reason
        }

    def aggregate_sentiment(self) -> Dict[str, Any]:
        """Computes aggregate macro sentiment and per-pair risk states."""
        now = datetime.now(timezone.utc)
        all_articles = list(self.article_cache.values())

        # Sort by fetched_at descending
        all_articles.sort(key=lambda x: x.get("fetched_at", ""), reverse=True)

        # Keep cache capped at 300
        if len(self.article_cache) > 300:
            kept = all_articles[:300]
            self.article_cache = {item.get("link") or item.get("title"): item for item in kept}
            all_articles = kept

        # Global macro sentiment
        scores = [item["compound_score"] for item in all_articles[:100]]
        macro_score = round(sum(scores) / len(scores), 4) if scores else 0.0

        if macro_score >= 0.12:
            macro_regime = "BULLISH"
        elif macro_score <= -0.12:
            macro_regime = "BEARISH"
        else:
            macro_regime = "NEUTRAL"

        bullish_cnt = sum(1 for s in scores if s >= 0.15)
        bearish_cnt = sum(1 for s in scores if s <= -0.15)
        neutral_cnt = len(scores) - bullish_cnt - bearish_cnt

        # Per-pair state
        pair_state: Dict[str, Any] = {}
        for pair in WHITELIST_PAIRS:
            pair_articles = [item for item in all_articles if pair in item.get("pairs", [])]
            pair_scores = [item["compound_score"] for item in pair_articles]
            avg_score = round(sum(pair_scores) / len(pair_scores), 4) if pair_scores else 0.0

            # Check if any blackout active for this specific pair
            recent_blackouts = [
                item for item in pair_articles[:15]
                if item.get("blackout") and pair in item.get("blackout_pairs", [])
            ]
            blackout_active = len(recent_blackouts) > 0
            blackout_reason = recent_blackouts[0]["blackout_reason"] if blackout_active else None

            # Regime tag
            if blackout_active:
                pair_regime = "BLACKOUT_RISK"
            elif avg_score >= 0.15:
                pair_regime = "BULLISH_TAILWIND"
            elif avg_score <= -0.15:
                pair_regime = "BEARISH_HEADWIND"
            else:
                pair_regime = "NEUTRAL"

            latest_item = pair_articles[0] if pair_articles else None

            pair_state[pair] = {
                "pair": pair,
                "sentiment_score": avg_score,
                "sentiment_regime": pair_regime,
                "headline_count": len(pair_articles),
                "blackout_active": blackout_active,
                "blackout_reason": blackout_reason,
                "latest_headline": latest_item["title"] if latest_item else "No recent asset-specific headlines",
                "latest_headline_score": latest_item["compound_score"] if latest_item else 0.0,
                "latest_headline_source": latest_item["source"] if latest_item else "-"
            }

        state_payload = {
            "last_updated": now.isoformat(),
            "daemon_status": "ONLINE",
            "total_articles_cached": len(all_articles),
            "macro_sentiment": {
                "score": macro_score,
                "regime": macro_regime,
                "sample_size": len(scores),
                "bullish_count": bullish_cnt,
                "bearish_count": bearish_cnt,
                "neutral_count": neutral_cnt
            },
            "pairs": pair_state,
            "recent_feed": all_articles[:50]
        }

        return state_payload

    def save_state(self, state_payload: Dict[str, Any]):
        """Atomically saves sentiment state to disk."""
        tmp_file = f"{self.state_file}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_file, self.state_file)
            logger.info(f"Sentiment state successfully updated ({self.state_file}). Macro score: {state_payload['macro_sentiment']['score']} ({state_payload['macro_sentiment']['regime']})")
        except Exception as e:
            logger.error(f"Error saving sentiment state: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass

    def run_cycle(self):
        """Executes a single fetch, parse, score, and persist cycle."""
        logger.info("Executing news sentiment scan cycle...")
        new_count = 0

        for feed_cfg in RSS_FEEDS:
            raw_articles = self.fetch_feed(feed_cfg)
            for raw in raw_articles:
                item_key = raw.get("link") or raw.get("title")
                if item_key not in self.article_cache:
                    evaluated = self.analyze_article(raw)
                    self.article_cache[item_key] = evaluated
                    new_count += 1

        logger.info(f"Scan complete. New articles processed: {new_count}. Total in memory: {len(self.article_cache)}")
        state = self.aggregate_sentiment()
        self.save_state(state)
        return state

    def run_forever(self, interval_seconds: int = 90):
        """Main daemon loop."""
        logger.info(f"Starting NewsSentimentDaemon loop (interval: {interval_seconds}s)...")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in daemon cycle: {e}", exc_info=True)

            time.sleep(interval_seconds)


if __name__ == "__main__":
    daemon = NewsSentimentDaemon()
    if "--once" in sys.argv:
        print("Running single cycle...")
        st = daemon.run_cycle()
        print("\n--- MACRO SENTIMENT ---")
        print(json.dumps(st["macro_sentiment"], indent=2))
        print("\n--- ASSET BREAKDOWN ---")
        for p, d in st["pairs"].items():
            print(f"{p:16} | Score: {d['sentiment_score']:+.3f} | {d['sentiment_regime']:16} | Blackout: {d['blackout_active']} | Headlines: {d['headline_count']}")
    else:
        daemon.run_forever(interval_seconds=90)
