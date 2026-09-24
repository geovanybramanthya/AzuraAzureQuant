"""
Unit Test Suite for DerivativesFeedCollector
"""
import unittest
import json
import time
from pathlib import Path
import sys

# Import DerivativesFeedCollector
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "user_data" / "modules"))
from derivatives_feed_collector import DerivativesFeedCollector, WHITELIST_PAIRS

class TestDerivativesCollector(unittest.TestCase):
    def setUp(self):
        self.test_state_file = Path("scratch/test_derivatives_state.json")
        if self.test_state_file.exists():
            self.test_state_file.unlink()
        self.collector = DerivativesFeedCollector(state_file_path=str(self.test_state_file))

    def tearDown(self):
        if self.test_state_file.exists():
            self.test_state_file.unlink()

    def test_single_cycle_execution(self):
        """Uji eksekusi 1 siklus langsung ke Bybit public endpoint."""
        state = self.collector.run_cycle()
        self.assertIsNotNone(state)
        self.assertIn("macro_derivatives", state)
        self.assertIn("pairs", state)
        self.assertEqual(len(state["pairs"]), len(WHITELIST_PAIRS))
        
        # Cek data BTC
        btc = state["pairs"].get("BTC/USDT:USDT")
        self.assertIsNotNone(btc)
        self.assertGreater(btc["mark_price"], 10000.0)
        self.assertGreater(btc["open_interest_usd"], 1000000.0)
        self.assertIn("funding_rate_8h_pct", btc)
        self.assertIn("derivatives_regime", btc)
        
        # Cek penyimpanan berkas
        self.assertTrue(self.test_state_file.exists())
        with open(self.test_state_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
            self.assertEqual(disk_data["collector_status"], "ONLINE")

    def test_squeeze_classification_logic(self):
        """Uji klasifikasi sinyal Short Squeeze dan Long Flush dengan data simulasi."""
        now_ts = time.time()
        
        # Kasus 1: Long Overheated + OI melonjak (Long Flush Warning)
        ticker_overheated = {
            'close': 85000.0,
            'info': {
                'markPrice': '85000.0',
                'indexPrice': '84950.0',
                'fundingRate': '0.00040', # +0.040% per 8h
                'openInterest': '60000',
                'openInterestValue': '5100000000',
                'turnover24h': '6000000000',
                'price24hPcnt': '0.05',
                'bid1Size': '10',
                'ask1Size': '10'
            }
        }
        # Masukkan riwayat 1 jam lalu dengan OI lebih rendah
        self.collector.history["BTC/USDT:USDT"] = [
            {'ts': now_ts - 3500, 'price': 84000.0, 'oi_usd': 4900000000.0, 'funding': 0.00020}
        ]
        res1 = self.collector.analyze_pair("BTC/USDT:USDT", ticker_overheated, now_ts)
        self.assertEqual(res1["derivatives_regime"], "LONG_OVERHEATED")
        self.assertEqual(res1["squeeze_signal"], "LONG_FLUSH_WARNING")
        self.assertEqual(res1["leverage_risk"], "HIGH")

        # Kasus 2: Negative Funding + OI melonjak (Short Squeeze Fuel)
        ticker_negative = {
            'close': 90.0,
            'info': {
                'markPrice': '90.0',
                'indexPrice': '90.1',
                'fundingRate': '-0.00010', # -0.010% per 8h (Short bayar Long)
                'openInterest': '3000000',
                'openInterestValue': '270000000',
                'turnover24h': '300000000',
                'price24hPcnt': '-0.02',
                'bid1Size': '50',
                'ask1Size': '20'
            }
        }
        self.collector.history["HYPE/USDT:USDT"] = [
            {'ts': now_ts - 3500, 'price': 92.0, 'oi_usd': 260000000.0, 'funding': -0.00005}
        ]
        res2 = self.collector.analyze_pair("HYPE/USDT:USDT", ticker_negative, now_ts)
        self.assertEqual(res2["derivatives_regime"], "SHORT_SQUEEZE_FUEL")
        self.assertEqual(res2["squeeze_signal"], "SHORT_SQUEEZE_ALERT")

if __name__ == "__main__":
    unittest.main()
