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

if __name__ == "__main__":
    unittest.main()
