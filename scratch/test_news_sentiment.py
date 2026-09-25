"""
Automated Test Suite for News & Fundamental Sentiment Daemon
Tests:
1. Entity extraction across all 8 whitelisted pairs.
2. Calibrated VADER crypto polarity scoring.
3. False positive filtering (e.g. Hack VC, Hackathon).
4. Simulated crisis event and blackout triggering.
5. State persistence and JSON schema conformance.
"""

import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from user_data.modules.news_sentiment_daemon import NewsSentimentDaemon, WHITELIST_PAIRS

class TestNewsSentimentDaemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daemon = NewsSentimentDaemon()

    def test_01_whitelist_pairs_coverage(self):
        """Verify all 8 whitelisted pairs are configured."""
        self.assertEqual(len(WHITELIST_PAIRS), 8)
        self.assertIn("BTC/USDT:USDT", WHITELIST_PAIRS)
        self.assertIn("HYPE/USDT:USDT", WHITELIST_PAIRS)
        self.assertIn("PAXG/USDT:USDT", WHITELIST_PAIRS)

    def test_02_entity_extraction(self):
        """Verify regex correctly extracts pairs from headlines without false matches."""
        # BTC
        pairs = self.daemon.extract_pairs("Bitcoin rallies above 80k as satoshi wallets remain quiet")
        self.assertIn("BTC/USDT:USDT", pairs)

        # ETH
        pairs = self.daemon.extract_pairs("Vitalik Buterin proposes new Ethereum gas optimization")
        self.assertIn("ETH/USDT:USDT", pairs)

        # SOL
        pairs = self.daemon.extract_pairs("Solana DeFi volume surges to new record")
        self.assertIn("SOL/USDT:USDT", pairs)

        # HYPE
        pairs = self.daemon.extract_pairs("Hyperliquid perpetual open interest hits new high")
        self.assertIn("HYPE/USDT:USDT", pairs)

        # Negative test (ensure 'sol' in 'solid' does not match SOL)
        pairs = self.daemon.extract_pairs("This is a solid crypto project with great fundamentals")
        self.assertNotIn("SOL/USDT:USDT", pairs)

        # Negative test (ensure 'ada' in 'adapt' does not match ADA)
        pairs = self.daemon.extract_pairs("Brokers adapt to new regulatory landscape")
        self.assertNotIn("ADA/USDT:USDT", pairs)

    def test_03_calibrated_vader_sentiment(self):
        """Verify bullish and bearish crypto terms score correctly."""
        bullish_art = {
            "title": "Bitcoin surges in massive breakout as ETF inflows explode",
            "summary": "Institutional investors aggressively accumulate BTC at support",
            "source": "Test",
            "link": "http://test/1",
            "fetched_at": "2026-09-24T00:00:00Z"
        }
        res_bull = self.daemon.analyze_article(bullish_art)
        self.assertGreaterEqual(res_bull["compound_score"], 0.40)
        self.assertEqual(res_bull["regime"], "BULLISH")

        bearish_art = {
            "title": "Crypto bloodbath as massive long liquidations trigger market crash",
            "summary": "Heavy outflows and panic selling cause severe price breakdown",
            "source": "Test",
            "link": "http://test/2",
            "fetched_at": "2026-09-24T00:00:00Z"
        }
        res_bear = self.daemon.analyze_article(bearish_art)
        self.assertLessEqual(res_bear["compound_score"], -0.40)
        self.assertEqual(res_bear["regime"], "BEARISH")

    def test_04_false_positive_filtering(self):
        """Verify Hack VC and Hackathons do not trigger false blackout."""
        art_hack_vc = {
            "title": "Solana startup raises $10M from Hack VC and partners",
            "summary": "The funding round was led by Hack VC to expand DeFi applications.",
            "source": "Test",
            "link": "http://test/3",
            "fetched_at": "2026-09-24T00:00:00Z"
        }
        res_vc = self.daemon.analyze_article(art_hack_vc)
        self.assertFalse(res_vc["blackout"])
        self.assertNotIn("SOL/USDT:USDT", res_vc["blackout_pairs"])

        art_hackathon = {
            "title": "Solana annual hackathon announces winners for 2026",
            "summary": "Hundreds of developers participated in the global hackathon.",
            "source": "Test",
            "link": "http://test/4",
            "fetched_at": "2026-09-24T00:00:00Z"
        }
        res_hackathon = self.daemon.analyze_article(art_hackathon)
        self.assertFalse(res_hackathon["blackout"])

    def test_05_critical_blackout_trigger(self):
        """Verify real security hack/exploit correctly triggers blackout for target pair."""
        crisis_art = {
            "title": "Chainlink cross-chain bridge exploited for $80M in catastrophic smart contract hack",
            "summary": "Emergency response teams halt LINK bridge as millions are stolen.",
            "source": "Test",
            "link": "http://test/5",
            "fetched_at": "2026-09-24T00:00:00Z"
        }
        res_crisis = self.daemon.analyze_article(crisis_art)
        self.assertTrue(res_crisis["blackout"])
        self.assertIn("LINK/USDT:USDT", res_crisis["blackout_pairs"])
        self.assertEqual(res_crisis["regime"], "CRITICAL_BLACKOUT")
        self.assertIsNotNone(res_crisis["blackout_reason"])

    def test_06_state_persistence_and_schema(self):
        """Verify sentiment_state.json contains all required schema keys."""
        state_file = self.daemon.state_file
        self.assertTrue(os.path.exists(state_file), f"State file {state_file} must exist")

        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        self.assertIn("last_updated", state)
        self.assertIn("daemon_status", state)
        self.assertIn("macro_sentiment", state)
        self.assertIn("pairs", state)
        self.assertIn("recent_feed", state)

        macro = state["macro_sentiment"]
        self.assertIn("score", macro)
        self.assertIn("regime", macro)
        self.assertIn("sample_size", macro)

        for pair in WHITELIST_PAIRS:
            self.assertIn(pair, state["pairs"])
            p_data = state["pairs"][pair]
            self.assertIn("sentiment_score", p_data)
            self.assertIn("sentiment_regime", p_data)
            self.assertIn("blackout_active", p_data)

    def test_07_publication_timestamp_parsing_and_sorting(self):
        """Verify publication timestamps parse accurately to WIB, preserve in articles, and sort correctly."""
        from user_data.modules.news_sentiment_daemon import parse_feed_timestamp
        import time

        # 1. Test RFC 822 format (e.g. CoinDesk / BeInCrypto: Fri, 25 Sep 2026 13:00:00 +0000 -> 20:00 WIB)
        iso1, wib1 = parse_feed_timestamp(fallback_raw="Fri, 25 Sep 2026 13:00:00 +0000")
        self.assertEqual(iso1, "2026-09-25T13:00:00+00:00")
        self.assertEqual(wib1, "20:00 WIB")

        # 2. Test ISO format (e.g. Blockworks: 2026-09-25T11:30:00.000Z -> 18:30 WIB)
        iso2, wib2 = parse_feed_timestamp(fallback_raw="2026-09-25T11:30:00.000Z")
        self.assertEqual(iso2, "2026-09-25T11:30:00+00:00")
        self.assertEqual(wib2, "18:30 WIB")

        # 3. Test feedparser struct_time
        class MockEntry:
            def __init__(self, st):
                self.published_parsed = st

        mock_st = time.struct_time((2026, 9, 25, 8, 15, 0, 4, 268, 0))
        iso3, wib3 = parse_feed_timestamp(MockEntry(mock_st))
        self.assertEqual(iso3, "2026-09-25T08:15:00+00:00")
        self.assertEqual(wib3, "15:15 WIB")

        # 4. Verify analyze_article preserves distinct publication timestamps
        art1 = {
            "title": "Bitcoin surges towards new local highs",
            "summary": "Massive institutional buying detected on Bybit and Binance",
            "source": "CoinDesk",
            "link": "https://test.com/art1",
            "published_raw": "Fri, 25 Sep 2026 13:00:00 +0000",
            "fetched_at": "2026-09-25T13:17:00+00:00"
        }
        art2 = {
            "title": "Ethereum upgrade advances towards mainnet rollout",
            "summary": "Developers announce testnet merge completion without issues",
            "source": "Decrypt",
            "link": "https://test.com/art2",
            "published_raw": "Fri, 25 Sep 2026 11:30:00 +0000",
            "fetched_at": "2026-09-25T13:17:00+00:00"
        }

        res1 = self.daemon.analyze_article(art1)
        res2 = self.daemon.analyze_article(art2)

        self.assertEqual(res1["published_at"], "2026-09-25T13:00:00+00:00")
        self.assertEqual(res1["published_wib"], "20:00 WIB")
        self.assertEqual(res2["published_at"], "2026-09-25T11:30:00+00:00")
        self.assertEqual(res2["published_wib"], "18:30 WIB")

        # Verify they do NOT have the same timestamp
        self.assertNotEqual(res1["published_wib"], res2["published_wib"])

        # 5. Verify aggregate_sentiment sorts by published_at descending (newest first)
        self.daemon.article_cache = {"art2": res2, "art1": res1}
        state = self.daemon.aggregate_sentiment()
        feed = state["recent_feed"]

        self.assertEqual(feed[0]["link"], "https://test.com/art1")  # 13:00 UTC (newer)
        self.assertEqual(feed[1]["link"], "https://test.com/art2")  # 11:30 UTC (older)

    def test_08_timestamp_robustness_edge_cases(self):
        """Verify edge cases: dict inputs, epoch timestamps, date transitions, and mixed ISO sorting."""
        from user_data.modules.news_sentiment_daemon import parse_feed_timestamp

        # 1. Dict entry input (verifies dict item lookup)
        dict_entry = {"published": "Fri, 25 Sep 2026 13:00:00 +0000"}
        iso_dict, wib_dict = parse_feed_timestamp(dict_entry)
        self.assertEqual(iso_dict, "2026-09-25T13:00:00+00:00")
        self.assertEqual(wib_dict, "20:00 WIB")

        # 2. Numeric epoch timestamp (seconds)
        # 1790341200 = 2026-09-25 13:00:00 UTC
        iso_epoch, wib_epoch = parse_feed_timestamp(fallback_raw=1790341200)
        self.assertEqual(iso_epoch, "2026-09-25T13:00:00+00:00")
        self.assertEqual(wib_epoch, "20:00 WIB")

        # 3. Numeric string epoch (milliseconds)
        # 1790341200000 = 2026-09-25 13:00:00 UTC
        iso_epoch_ms, wib_epoch_ms = parse_feed_timestamp(fallback_raw="1790341200000")
        self.assertEqual(iso_epoch_ms, "2026-09-25T13:00:00+00:00")
        self.assertEqual(wib_epoch_ms, "20:00 WIB")

        # 4. Past date in current year (should format as DD/MM HH:MM)
        iso_past, wib_past = parse_feed_timestamp(fallback_raw="Wed, 07 Jan 2026 14:00:00 +0000")
        self.assertEqual(iso_past, "2026-01-07T14:00:00+00:00")
        self.assertEqual(wib_past, "07/01 21:00")

        # 5. Past year date (should format as DD/MM/YYYY HH:MM)
        iso_prev_yr, wib_prev_yr = parse_feed_timestamp(fallback_raw="Wed, 07 Jan 2025 14:00:00 +0000")
        self.assertEqual(iso_prev_yr, "2025-01-07T14:00:00+00:00")
        self.assertEqual(wib_prev_yr, "07/01/2025 21:00")

        # 6. Sorting with mixed timezone representations ('Z' vs '+00:00')
        art_z = {
            "title": "Z format item",
            "source": "Blockworks",
            "link": "https://test.com/z",
            "compound_score": 0.5,
            "published_at": "2026-09-25T14:00:00Z",
            "fetched_at": "2026-09-25T14:05:00+00:00"
        }
        art_offset = {
            "title": "Offset format item",
            "source": "CoinDesk",
            "link": "https://test.com/offset",
            "compound_score": 0.3,
            "published_at": "2026-09-25T13:00:00+00:00",
            "fetched_at": "2026-09-25T14:05:00+00:00"
        }
        self.daemon.article_cache = {"z": art_z, "offset": art_offset}
        state = self.daemon.aggregate_sentiment()
        self.assertEqual(state["recent_feed"][0]["link"], "https://test.com/z")  # 14:00 UTC is newer than 13:00 UTC


if __name__ == "__main__":
    unittest.main()
