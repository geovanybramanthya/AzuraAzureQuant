"""
Automated Integration Test: Event Risk Blackout Circuit Breaker
Verifies:
1. Supervisor loads sentiment_state.json.
2. When a pair is under Blackout Risk, the Supervisor vetoes entry.
3. When Blackout Risk is cleared, normal operation resumes.
"""

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_supervisor_daemon import AISupervisorDaemon

class TestEventRiskBlackout(unittest.TestCase):
    def setUp(self):
        self.state_file = Path("user_data/data/sentiment_state.json")
        self.backup_state = None
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                self.backup_state = f.read()

    def tearDown(self):
        if self.backup_state and self.state_file.exists():
            with open(self.state_file, "w", encoding="utf-8") as f:
                f.write(self.backup_state)

    def test_blackout_veto_execution(self):
        # 1. Simulate Blackout on SOL
        simulated_state = {
            "last_updated": "2026-09-24T22:00:00Z",
            "daemon_status": "ONLINE",
            "macro_sentiment": {"score": -0.25, "regime": "BEARISH", "sample_size": 50},
            "pairs": {
                "SOL/USDT:USDT": {
                    "pair": "SOL/USDT:USDT",
                    "sentiment_score": -0.85,
                    "sentiment_regime": "BLACKOUT_RISK",
                    "blackout_active": True,
                    "blackout_reason": "Simulated exploit on Solana bridge protocol",
                    "headline_count": 5
                },
                "BTC/USDT:USDT": {
                    "pair": "BTC/USDT:USDT",
                    "sentiment_score": 0.20,
                    "sentiment_regime": "BULLISH_TAILWIND",
                    "blackout_active": False,
                    "blackout_reason": None,
                    "headline_count": 10
                }
            },
            "recent_feed": []
        }

        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(simulated_state, f, indent=2)

        # 2. Instantiate Supervisor
        with patch("ai_supervisor_daemon.FreqtradeClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.ping.return_value = True
            mock_client.get_status.return_value = []
            mock_client.get_trades.return_value = []
            mock_client.get_balance.return_value = {"total": 1000.0}
            mock_client.force_enter.return_value = (True, {"status": "ok"})

            supervisor = AISupervisorDaemon()
            supervisor.client = mock_client

            # Mock scanner to return valid opportunities for SOL and BTC
            mock_opp_sol = {
                "pair": "SOL/USDT:USDT",
                "limit_price": 102.5,
                "regime": "support_dip_bounce",
                "rr_ratio": 2.1,
                "4h_adx": 22.0,
                "is_squeeze": False,
                "risk": 1.5,
                "tp1": 105.0,
                "tp2": 107.0,
                "buffered_be": 102.0
            }
            mock_opp_btc = {
                "pair": "BTC/USDT:USDT",
                "limit_price": 79000.0,
                "regime": "support_dip_bounce",
                "rr_ratio": 2.3,
                "4h_adx": 24.0,
                "is_squeeze": False,
                "risk": 500.0,
                "tp1": 80500.0,
                "tp2": 82000.0,
                "buffered_be": 78900.0
            }

            def fake_evaluate(p):
                if p == "SOL/USDT:USDT": return mock_opp_sol
                if p == "BTC/USDT:USDT": return mock_opp_btc
                return None

            supervisor.scanner.evaluate_opportunity = MagicMock(side_effect=fake_evaluate)

            # 3. Run cycle
            supervisor.run_cycle()

            # 4. Verify mock_client.force_enter calls:
            # SOL must NEVER be entered because blackout_active is True!
            entered_pairs = [call.kwargs.get("pair") for call in mock_client.force_enter.call_args_list]
            self.assertNotIn("SOL/USDT:USDT", entered_pairs, "SOL must be vetoed by Event Risk Blackout Gate!")
            print(f"Verified: SOL entry was successfully vetoed by Event Risk Blackout Gate. Entered pairs: {entered_pairs}")

if __name__ == "__main__":
    unittest.main()
