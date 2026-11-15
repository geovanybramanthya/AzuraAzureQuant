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
import calendar
import email.utils
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple, Union

try:
    import dateutil.parser
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

WIB_TZ = timezone(timedelta(hours=7))


def format_wib_timestamp(dt_utc: datetime) -> Tuple[str, str]:
    """
    Standardizes UTC datetime into:
    - published_at: ISO-8601 UTC string (e.g. '2026-09-25T13:00:00+00:00')
    - published_wib: Formatted string in local WIB:
      - 'HH:MM WIB' if published on current day (WIB)
      - 'DD/MM HH:MM' if published earlier this year
      - 'DD/MM/YYYY HH:MM' if published in previous years
    """
    dt_wib = dt_utc.astimezone(WIB_TZ)
    now_wib = datetime.now(timezone.utc).astimezone(WIB_TZ)

    if dt_utc.microsecond == 0:
        iso_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    else:
        iso_str = dt_utc.isoformat()
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"

    if dt_wib.date() == now_wib.date():
        wib_str = dt_wib.strftime("%H:%M WIB")
    elif dt_wib.year == now_wib.year:
        wib_str = dt_wib.strftime("%d/%m %H:%M")
    else:
        wib_str = dt_wib.strftime("%d/%m/%Y %H:%M")

    return iso_str, wib_str


def parse_feed_timestamp(entry: Any = None, fallback_raw: Optional[Union[str, int, float]] = None) -> Tuple[str, str]:
    """
    Parses an RSS entry, FeedParserDict, dict, or raw date string/epoch into:
    - published_at: ISO-8601 UTC string (e.g. '2026-09-25T13:00:00+00:00')
    - published_wib: Local WIB string (e.g. '20:00 WIB' or 'DD/MM HH:MM')
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Try struct_time from entry (supports FeedParserDict, dict, or object)
    st = None
    if entry is not None:
        if isinstance(entry, dict):
            st = entry.get("published_parsed") or entry.get("updated_parsed")
        else:
            st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)

    if st:
        try:
            ts = calendar.timegm(st)
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            return format_wib_timestamp(dt_utc)
        except Exception:
            pass

    # 2. Extract raw date string or numeric timestamp from entry or fallback
    raw = None
    if entry is not None:
        if isinstance(entry, dict):
            raw = (
                entry.get("published")
                or entry.get("updated")
                or entry.get("pubDate")
                or entry.get("created")
                or entry.get("date")
                or entry.get("published_raw")
            )
        else:
            raw = (
                getattr(entry, "published", None)
                or getattr(entry, "updated", None)
                or getattr(entry, "pubDate", None)
                or getattr(entry, "created", None)
                or getattr(entry, "date", None)
                or getattr(entry, "published_raw", None)
            )

    if not raw and fallback_raw is not None:
        raw = fallback_raw

    if raw is not None:
        # Numeric epoch timestamp (seconds or milliseconds)
        if isinstance(raw, (int, float)):
            try:
                ts = raw / 1000.0 if raw > 1e11 else float(raw)
                dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
                return format_wib_timestamp(dt_utc)
            except Exception:
                pass
        elif isinstance(raw, str):
            clean_raw = raw.strip()
            # If string is purely numeric digits (epoch seconds or millis)
            if clean_raw.replace(".", "", 1).isdigit() and len(clean_raw) >= 9:
                try:
                    val = float(clean_raw)
                    ts = val / 1000.0 if val > 1e11 else val
                    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
                    return format_wib_timestamp(dt_utc)
                except Exception:
                    pass

            if HAS_DATEUTIL:
                try:
                    dt = dateutil.parser.parse(clean_raw)
                    if dt.tzinfo is None:
                        dt_utc = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt_utc = dt.astimezone(timezone.utc)
                    return format_wib_timestamp(dt_utc)
                except Exception:
                    pass

            try:
                dt = email.utils.parsedate_to_datetime(clean_raw)
                if dt.tzinfo is None:
                    dt_utc = dt.replace(tzinfo=timezone.utc)
                else:
                    dt_utc = dt.astimezone(timezone.utc)
                return format_wib_timestamp(dt_utc)
            except Exception:
                pass

            try:
                dt = datetime.fromisoformat(clean_raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt_utc = dt.replace(tzinfo=timezone.utc)
                else:
                    dt_utc = dt.astimezone(timezone.utc)
                return format_wib_timestamp(dt_utc)
            except Exception:
                pass

    # 3. Fallback to current UTC time
    return format_wib_timestamp(now_utc)

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

# Minimum number of headlines required before a per-pair sentiment score is
# allowed to become non-neutral (and thus influence strategy vetoes).
# Below this count the pair score is forced neutral (0.0): with only 1-2
# headlines a single article can pin an extreme score (observed live:
# LINK stuck at -0.71, and HYPE at +0.1779 from a single headline).
MIN_PAIR_HEADLINE_COUNT = 3

# Documented macro crash threshold: -0.45 (PRD / strategy contract:
# global cancellation only when macro_crash_alert is active or
# macro_score <= -0.45). Both macro_blackout_active and macro_crash_alert
# use this single constant so the crash alert cannot silently trigger
# earlier than documented (it previously used -0.35, see aggregate code).
MACRO_CRASH_ALERT_THRESHOLD = -0.45

# Regex patterns for asset entity recognition
# Audit 2026-10-21 (all pairs):
# - BTC/ETH/SOL/ADA/DOGE/PAXG: bare tickers/names are asset-specific in
#   crypto RSS feeds (bitcoin, ethereum, solana, cardano, dogecoin, pax gold)
#   and are kept as-is. Residual risk noted for short names (sol, ada) but
#   no ordinary-English collision was observed for them in these feeds.
# - LINK: FIXED. Bare "link" is an ordinary English word ("link in bio",
#   "link below", hyperlinks) and polluted LINK's score. Now only a cashtag
#   ($LINK), a pair mention (LINK/USDT), or the token name Chainlink counts.
# - HYPE: FIXED, same flaw as LINK. Bare "hype" is an ordinary English word
#   ("AI hype", "market hype"). Now only $HYPE, HYPE/USDT, or Hyperliquid
#   counts.
ASSET_PATTERNS = {
    "BTC/USDT:USDT": re.compile(r"\b(btc|bitcoin|satoshi)\b", re.IGNORECASE),
    "ETH/USDT:USDT": re.compile(r"\b(eth|ethereum|ether|vitalik)\b", re.IGNORECASE),
    "SOL/USDT:USDT": re.compile(r"\b(sol|solana)\b", re.IGNORECASE),
    "ADA/USDT:USDT": re.compile(r"\b(ada|cardano|hoskinson)\b", re.IGNORECASE),
    "DOGE/USDT:USDT": re.compile(r"\b(doge|dogecoin)\b", re.IGNORECASE),
    "LINK/USDT:USDT": re.compile(r"(\$link\b|\blink\s*/\s*usdt\b|chainlink)", re.IGNORECASE),
    "PAXG/USDT:USDT": re.compile(r"\b(paxg|pax gold|gold token)\b", re.IGNORECASE),
    "HYPE/USDT:USDT": re.compile(r"(\$hype\b|\bhype\s*/\s*usdt\b|hyperliquid)", re.IGNORECASE)
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
    re.compile(r"\banti[-\s]?fraud\b", re.IGNORECASE),
    re.compile(r"\b(?:months?|weeks?|years?|days?)\s+after\s+(?:the\s+)?[\$\w\s]*?(?:hack|exploit|attack|collapse)\b", re.IGNORECASE),
    re.compile(r"\bpast\s+(?:hack|exploit|attack)\b", re.IGNORECASE),
    re.compile(r"\bhistorical\s+(?:hack|exploit|attack)\b", re.IGNORECASE)
]


def get_article_age_hours(item: Dict[str, Any], now_utc: datetime) -> float:
    """Calculates article age in hours from publication timestamp."""
    ts_str = item.get("published_at") or item.get("fetched_at")
    if not ts_str:
        return 999.0
    try:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        if HAS_DATEUTIL:
            dt = dateutil.parser.parse(ts_str)
        else:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now_utc - dt).total_seconds() / 3600.0)
    except Exception:
        return 999.0

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
        # INSTR: previous blackout states (transition logging) + last archived hour.
        self._prev_blackouts: Dict[str, Any] = {}
        self._last_archive_hour = None
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
                            # Discard legacy poisoned entries that have no true publication timestamp
                            if not item.get("published_raw") and item.get("published_at") == item.get("fetched_at"):
                                continue

                            # Backfill publication timestamps if missing from older daemon versions
                            if "published_at" not in item:
                                pub_at, pub_wib = parse_feed_timestamp(None, fallback_raw=item.get("published_raw") or item.get("fetched_at"))
                                item["published_at"] = pub_at
                                item["published_wib"] = pub_wib

                            # Discard articles older than 36h to prevent stale memory buildup
                            now_utc = datetime.now(timezone.utc)
                            if get_article_age_hours(item, now_utc) > 36.0:
                                continue

                            # Re-verify blackout flag using updated false-positive patterns
                            if item.get("blackout"):
                                pairs = item.get("pairs") or []
                                compound = float(item.get("compound_score", 0.0) or 0.0)
                                updated_blackouts = self.check_pair_blackouts(item.get("title", ""), item.get("summary", ""), pairs, compound)
                                item["blackout"] = len(updated_blackouts) > 0
                                item["blackout_pairs"] = list(updated_blackouts.keys())
                                item["blackout_reason"] = list(updated_blackouts.values())[0] if item["blackout"] else None

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
                    link = getattr(entry, "link", "") or getattr(entry, "id", "") or getattr(entry, "guid", "")
                    pub_time = (
                        getattr(entry, "published", "")
                        or getattr(entry, "updated", "")
                        or getattr(entry, "pubDate", "")
                        or getattr(entry, "created", "")
                        or getattr(entry, "date", "")
                    )
                    pub_iso, pub_wib = parse_feed_timestamp(entry, fallback_raw=pub_time)

                    articles.append({
                        "source": source,
                        "title": title,
                        "summary": clean_summary,
                        "link": link,
                        "published_at": pub_iso,
                        "published_wib": pub_wib,
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
        # Gate aligned with the docstring above (<= -0.35): previously the
        # code used -0.30, so blackouts effectively triggered earlier than
        # documented.
        if compound_score > -0.35 or not pairs:
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

        # Resolve publication timestamp accurately
        pub_at = article.get("published_at")
        pub_wib = article.get("published_wib")
        if not pub_at:
            pub_at, pub_wib = parse_feed_timestamp(None, fallback_raw=article.get("published_raw") or article.get("fetched_at"))

        return {
            "title": article["title"],
            "source": article["source"],
            "link": article["link"],
            "published_at": pub_at,
            "published_wib": pub_wib,
            "published_raw": article.get("published_raw", ""),
            "fetched_at": article.get("fetched_at", datetime.now(timezone.utc).isoformat()),
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

        # Sort by true publication timestamp descending
        def get_article_sort_ts(item: Dict[str, Any]) -> float:
            ts_str = item.get("published_at") or item.get("fetched_at")
            if not ts_str:
                return 0.0
            try:
                if HAS_DATEUTIL:
                    dt = dateutil.parser.parse(ts_str)
                else:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                return 0.0

        # Scope C: Prune articles older than 36h from rolling cache
        self.article_cache = {k: v for k, v in self.article_cache.items() if get_article_age_hours(v, now) <= 36.0}
        all_articles = list(self.article_cache.values())
        all_articles.sort(key=get_article_sort_ts, reverse=True)

        # Keep cache capped at 300
        if len(self.article_cache) > 300:
            kept = all_articles[:300]
            self.article_cache = {item.get("link") or item.get("title"): item for item in kept}
            all_articles = kept

        # Global macro sentiment with 18h half-life exponential time-decay
        macro_weighted = []
        macro_weights = []
        for item in all_articles[:100]:
            age_h = get_article_age_hours(item, now)
            w = 0.5 ** (age_h / 18.0) if age_h >= 0 else 1.0
            macro_weighted.append(item.get("compound_score", 0.0) * w)
            macro_weights.append(w)
        macro_score = round(sum(macro_weighted) / sum(macro_weights), 4) if macro_weights and sum(macro_weights) > 0 else 0.0

        if macro_score >= 0.12:
            macro_regime = "BULLISH"
        elif macro_score <= -0.12:
            macro_regime = "BEARISH"
        else:
            macro_regime = "NEUTRAL"

        # Scope C: Only articles within the last 24h count towards macro counts & crash alerts
        recent_articles = [it for it in all_articles if get_article_age_hours(it, now) <= 24.0]
        scores = [item.get("compound_score", 0.0) for item in (recent_articles[:100] if recent_articles else all_articles[:100])]
        bullish_cnt = sum(1 for s in scores if s >= 0.15)
        bearish_cnt = sum(1 for s in scores if s <= -0.15)
        neutral_cnt = len(scores) - bullish_cnt - bearish_cnt

        # Per-pair state
        pair_state: Dict[str, Any] = {}
        for pair in WHITELIST_PAIRS:
            pair_articles = [item for item in all_articles if pair in (item.get("pairs") or [])]
            
            # Compute age-decayed weighted sentiment score (18h half-life)
            weighted_scores = []
            weights = []
            active_blackouts = []

            for item in pair_articles:
                age_h = get_article_age_hours(item, now)
                score = float(item.get("compound_score", 0.0) or 0.0)
                # Exponential decay weight with 18h half-life
                weight = 0.5 ** (age_h / 18.0) if age_h >= 0 else 1.0
                weighted_scores.append(score * weight)
                weights.append(weight)

                # Scope C: Fix Stale False-Positive Blackout Decay:
                # ONLY articles published within the last 18 hours can trigger an active crisis blackout!
                # Articles older than 18 hours decay and are excluded from active crisis detection,
                # ensuring historical discussions or stale mentions do not permanently paralyze pairs like ETH/LINK.
                if age_h <= 18.0 and item.get("blackout") and pair in (item.get("blackout_pairs") or []):
                    active_blackouts.append(item)

            avg_score = round(sum(weighted_scores) / sum(weights), 4) if weights and sum(weights) > 0 else (
                round(sum([it.get("compound_score", 0.0) for it in pair_articles]) / len(pair_articles), 4) if pair_articles else 0.0
            )

            # Minimum sample guard (see MIN_PAIR_HEADLINE_COUNT): with fewer
            # than MIN_PAIR_HEADLINE_COUNT headlines the score is not
            # meaningful, so force it neutral (0.0) so it cannot influence
            # vetoes. Event-risk blackout detection below is separate and
            # is NOT disabled by this guard.
            if len(pair_articles) < MIN_PAIR_HEADLINE_COUNT:
                avg_score = 0.0

            blackout_active = len(active_blackouts) > 0
            blackout_reason = active_blackouts[0]["blackout_reason"] if blackout_active else None

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
                "is_event_risk_blackout": blackout_active,
                "blackout_reason": blackout_reason,
                "latest_headline": latest_item["title"] if latest_item else "No recent asset-specific headlines",
                "latest_headline_score": latest_item["compound_score"] if latest_item else 0.0,
                "latest_headline_source": latest_item["source"] if latest_item else "-"
            }

        # Macro Crash and Blackout Alert Telemetry (PRD Pilar 1 & Pilar 3)
        # Perbaikan Bug Global Blackout Veto: Macro blackout hanya aktif jika sentimen makro benar-benar kolaps secara sistemik
        macro_blackout_active = (macro_score <= MACRO_CRASH_ALERT_THRESHOLD) or (bearish_cnt > bullish_cnt * 3 and len(scores) >= 15)
        # Crash alert aligned to the documented -0.45 (MACRO_CRASH_ALERT_THRESHOLD):
        # previously this line used -0.35 with a lower sample floor (>= 10) - a
        # two-level alert mismatch - while the strategy treats macro_crash_alert
        # as the -0.45 global-cancellation trigger, so cancellations effectively
        # fired at -0.35. Score threshold and sample floor now match blackout.
        macro_crash_alert = (macro_score <= MACRO_CRASH_ALERT_THRESHOLD) or (bearish_cnt > bullish_cnt * 3 and len(scores) >= 15)

        # INSTR (observability only): log blackout transitions only; no score/blackout math changed.
        _prev = getattr(self, "_prev_blackouts", None)
        if _prev is None:
            _prev = {}
            self._prev_blackouts = _prev
        for _pair, _pstate in pair_state.items():
            _cur = bool(_pstate["blackout_active"])
            _old = _prev.get(_pair)
            if _old is not None and _old != _cur:
                logger.info(f"[INSTR-BLACKOUT] pair={_pair} blackout {_old}->{_cur} reason={_pstate.get('blackout_reason')} at={now.isoformat()}")
            _prev[_pair] = _cur
        for _key, _cur in (("MACRO_BLACKOUT", macro_blackout_active), ("MACRO_CRASH", macro_crash_alert)):
            _old = _prev.get(_key)
            if _old is not None and _old != _cur:
                logger.info(f"[INSTR-BLACKOUT] pair={_key} blackout {_old}->{_cur} reason=macro at={now.isoformat()}")
            _prev[_key] = _cur
        state_payload = {
            "last_updated": now.isoformat(),
            "daemon_status": "ONLINE",
            "total_articles_cached": len(all_articles),
            "macro_sentiment": {
                "score": macro_score,
                "regime": macro_regime,
                "macro_blackout_active": macro_blackout_active,
                "is_macro_blackout_active": macro_blackout_active,
                "macro_crash_alert": macro_crash_alert,
                "is_macro_crash_alert": macro_crash_alert,
                "sample_size": len(scores),
                "bullish_count": bullish_cnt,
                "bearish_count": bearish_cnt,
                "neutral_count": neutral_cnt
            },
            "macro_blackout_active": macro_blackout_active,
            "is_macro_blackout_active": macro_blackout_active,
            "macro_crash_alert": macro_crash_alert,
            "is_macro_crash_alert": macro_crash_alert,
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
            # INSTR (observability only): hourly archive copy of the state file.
            # Never fails the main save: the whole block is wrapped in try/except.
            try:
                _now_utc = datetime.now(timezone.utc)
                _hour_key = _now_utc.strftime("%Y%m%d-%H")
                if getattr(self, "_last_archive_hour", None) != _hour_key:
                    _archive_dir = os.path.join(os.path.dirname(self.state_file), "sentiment_archive")
                    os.makedirs(_archive_dir, exist_ok=True)
                    _archive_path = os.path.join(_archive_dir, f"sentiment_state_{_now_utc.strftime('%Y%m%d-%H%M')}.json")
                    with open(_archive_path, "w", encoding="utf-8") as _af:
                        json.dump(state_payload, _af, indent=2, ensure_ascii=False)
                    self._last_archive_hour = _hour_key
                    # Prune archives older than 30 days.
                    _cutoff = _now_utc - timedelta(days=30)
                    for _fname in os.listdir(_archive_dir):
                        if _fname.startswith("sentiment_state_") and _fname.endswith(".json"):
                            _fpath = os.path.join(_archive_dir, _fname)
                            try:
                                if datetime.fromtimestamp(os.path.getmtime(_fpath), tz=timezone.utc) < _cutoff:
                                    os.remove(_fpath)
                            except OSError:
                                pass
            except Exception as _arch_e:
                logger.warning(f"[INSTR-ARCHIVE] archive failed (main save unaffected): {_arch_e}")
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
                else:
                    # Update timestamps if existing cached item was missing true publication date
                    cached = self.article_cache[item_key]
                    if not cached.get("published_at") or cached.get("published_at") == cached.get("fetched_at"):
                        if raw.get("published_at"):
                            cached["published_at"] = raw["published_at"]
                            cached["published_wib"] = raw["published_wib"]
                            if raw.get("published_raw"):
                                cached["published_raw"] = raw["published_raw"]

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
