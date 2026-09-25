"""
End-to-End Verification Suite for Multi-Gate News Catalyst Execution Framework
-----------------------------------------------------------------------------
Verifies:
1. News catalyst scanner detection and filtering across all 5 gates.
2. Restriction multi-gate enforcement (volume shock, RSI corridor, resistance headroom, derivatives veto).
3. Limit order price calibration and 0.60x stake amount calculation.
4. Rapid invalidation (6h, spot <= -0.8%) and stagnation cutoff (12h, spot < +0.5%) logic.
5. Dual-stage TP1/TP2 targets and buffered breakeven +0.15R.
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

# Ensure root directory is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_supervisor_daemon import MarketScanner, AISupervisorDaemon
from user_data.strategies.ApexDualAlpha_Omni_V12_LinkCalibrated import ApexDualAlpha_Omni_V12_LinkCalibrated


def create_synthetic_candle_data(
    base_price=50000.0,
    vol_shock_mult=2.6,
    thrust_atr_mult=1.6,
    headroom_pct=0.04,
    vol_compressed=True,
    macro_bull_4h=True
):
    """Generates synthetic 1H and 4H DataFrames with precisely calibrated indicators."""
    # 1. Generate 4H DataFrame (250 candles)
    n_4h = 250
    dates_4h = pd.date_range(end=datetime.now(timezone.utc), periods=n_4h, freq="4h")
    
    if macro_bull_4h:
        # 4H Macro alignment: C > EMA50 > EMA200
        np.random.seed(42)
        closes_4h = np.zeros(n_4h)
        closes_4h[:100] = np.linspace(70.0, 100.0, 100)
        noise = np.random.normal(0, 0.4, 150)
        closes_4h[100:] = 100.5 + noise
        if not vol_compressed:
            # High ADX (> 26) to test compression rejection
            closes_4h = np.linspace(50.0, 150.0, n_4h)
    else:
        # Bearish: close < ema_50 < ema_200
        closes_4h = np.linspace(150.0, 50.0, n_4h)
        
    df_4h = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_4h],
        "date": dates_4h,
        "open": closes_4h - 0.2,
        "high": closes_4h + 0.8,
        "low": closes_4h - 0.8,
        "close": closes_4h,
        "volume": np.full(n_4h, 1000.0)
    })

    # 2. Generate 1H DataFrame (80 candles)
    n_1h = 80
    dates_1h = pd.date_range(end=datetime.now(timezone.utc), periods=n_1h, freq="1h")
    
    deltas = np.full(n_1h, -25.0)
    deltas[::3] = 30.0

    # ATR of baseline candles is approx 100.0
    base_atr = 100.0
    thrust = base_atr * thrust_atr_mult
    deltas[-2] = thrust
    deltas[-1] = 5.0

    closes_1h = base_price + np.cumsum(deltas)
    opens_1h = closes_1h - deltas
    highs_1h = np.maximum(closes_1h, opens_1h) + 5.0
    lows_1h = np.minimum(closes_1h, opens_1h) - 5.0

    # 48h rolling resistance headroom:
    c_catalyst = closes_1h[-2]
    highs_1h[-15] = c_catalyst * (1.0 + headroom_pct)

    volumes_1h = np.full(n_1h, 1000.0)
    volumes_1h[-2] = 1000.0 * vol_shock_mult

    df_1h = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_1h],
        "date": dates_1h,
        "open": opens_1h,
        "high": highs_1h,
        "low": lows_1h,
        "close": closes_1h,
        "volume": volumes_1h
    })

    return df_1h, df_4h

    return df_1h, df_4h


class TestNewsCatalystExecutionE2E(unittest.TestCase):
    """Comprehensive Test Suite for Multi-Gate News Catalyst Execution Framework."""

    def setUp(self):
        self.pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
        self.scanner = MarketScanner(self.pairs)

        # Baseline Valid Fundamental State
        self.valid_sent_state = {
            "pairs": {
                "BTC/USDT:USDT": {
                    "sentiment_score": 0.25,
                    "sentiment_regime": "BULLISH_TAILWIND",
                    "latest_headline_score": 0.65,
                    "blackout_active": False,
                    "blackout_reason": None,
                    "latest_headline": "Bitcoin institutional treasury adoption reaches record high",
                    "latest_headline_source": "CoinDesk"
                }
            }
        }

        # Baseline Valid Derivatives State
        self.valid_deriv_state = {
            "pairs": {
                "BTC/USDT:USDT": {
                    "funding_rate_8h_pct": 0.008,
                    "squeeze_signal": "NONE",
                    "derivatives_regime": "EQUILIBRIUM",
                    "leverage_risk": "LOW"
                }
            }
        }

    # =========================================================================
    # TEST 1: PASSING ALL 5 GATES (END-TO-END SUCCESS)
    # =========================================================================
    def test_news_catalyst_all_gates_pass(self):
        """Verify that when all 5 gates are satisfied, an optimal news opportunity is generated."""
        df_1h, df_4h = create_synthetic_candle_data(
            base_price=50000.0,
            vol_shock_mult=2.6,
            thrust_atr_mult=1.6,
            headroom_pct=0.04,
            macro_bull_4h=True
        )

        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h if tf == '1h' else df_4h):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)

        self.assertIsNotNone(opp, "Opportunity must NOT be None when all 5 gates pass")
        self.assertEqual(opp['regime'], 'news_catalyst')
        self.assertEqual(opp['entry_tag'], 'ai_news_catalyst_long')
        self.assertEqual(opp['stake_scale'], 0.60, "News catalyst must enforce 0.60x stake scaling")
        self.assertEqual(opp['rr_ratio'], 2.0, "Dual-stage TP2 target must provide 2.0 R:R")

        # Check limit bid calculation: min(C - 0.20*(H - L), C * 0.9985)
        c = float(df_1h['close'].iloc[-2])
        h = float(df_1h['high'].iloc[-2])
        l = float(df_1h['low'].iloc[-2])
        expected_bid = round(min(c - 0.20 * (h - l), c * 0.9985), 2)
        self.assertEqual(opp['limit_price'], expected_bid)

        # Check Gate 5: Stop loss at 1.5% risk, TP1 = +1.0R, TP2 = +2.0R, Buffered BE = +0.15R
        expected_risk = round(expected_bid * 0.015, 2)
        expected_sl = round(expected_bid - expected_risk, 2)
        expected_tp1 = round(expected_bid + 1.0 * expected_risk, 2)
        expected_tp2 = round(expected_bid + 2.0 * expected_risk, 2)
        expected_be = round(expected_bid + 0.15 * expected_risk, 2)

        self.assertEqual(opp['risk'], expected_risk)
        self.assertEqual(opp['stop_loss'], expected_sl)
        self.assertEqual(opp['tp1'], expected_tp1)
        self.assertEqual(opp['tp2'], expected_tp2)
        self.assertEqual(opp['buffered_be'], expected_be)
        print("PASS: test_news_catalyst_all_gates_pass")

    # =========================================================================
    # TEST 2: GATE 1 (SENTIMENT GATE ENFORCEMENT & RESTRICTIONS)
    # =========================================================================
    def test_gate1_sentiment_restrictions(self):
        """Verify Gate 1 sentiment restrictions: headline threshold, pair regime, blackout veto, crisis keywords."""
        df_1h, df_4h = create_synthetic_candle_data(base_price=50000.0)

        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h if tf == '1h' else df_4h):
            # 1. Weak Sentiment (Headline < 0.50 and pair < 0.20) -> Must be rejected
            weak_sent = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "sentiment_score": 0.10,
                        "sentiment_regime": "NEUTRAL",
                        "latest_headline_score": 0.35,
                        "blackout_active": False,
                        "latest_headline": "Bitcoin holds steady at support"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", weak_sent, self.valid_deriv_state)
            self.assertIsNone(opp, "Weak sentiment must be rejected by Gate 1")

            # 2. Blackout Active Veto -> Must be rejected
            blackout_sent = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "sentiment_score": 0.80,
                        "sentiment_regime": "BULLISH_TAILWIND",
                        "latest_headline_score": 0.85,
                        "blackout_active": True,
                        "blackout_reason": "Global regulatory summit emergency pause",
                        "latest_headline": "Bitcoin jumps 5%"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", blackout_sent, self.valid_deriv_state)
            self.assertIsNone(opp, "Active blackout must veto entry")

            # 3. Crisis Keyword with Negative Score -> Must be rejected
            crisis_sent = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "sentiment_score": -0.40,
                        "sentiment_regime": "BEARISH_HEADWIND",
                        "latest_headline_score": -0.60,
                        "blackout_active": False,
                        "latest_headline": "Major exchange exploit and hack investigation launched"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", crisis_sent, self.valid_deriv_state)
            self.assertIsNone(opp, "Crisis keywords with negative sentiment must be rejected")

            # 4. Valid Pair Score >= 0.20 with BULLISH_TAILWIND (even if headline is modest 0.40) -> Must pass
            pair_tailwind_sent = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "sentiment_score": 0.32,
                        "sentiment_regime": "BULLISH_TAILWIND",
                        "latest_headline_score": 0.40,
                        "blackout_active": False,
                        "latest_headline": "Market depth thickens on spot exchanges"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", pair_tailwind_sent, self.valid_deriv_state)
            self.assertIsNotNone(opp, "Pair score >= 0.20 with BULLISH_TAILWIND must pass Gate 1")

        print("PASS: test_gate1_sentiment_restrictions")

    # =========================================================================
    # TEST 3: GATE 2 (TECHNICAL CONFLUENCE RESTRICTIONS)
    # =========================================================================
    def test_gate2_technical_confluence_restrictions(self):
        """Verify Gate 2: volume shock >= 2.2x, thrust >= 1.4x ATR, 48h headroom >= 1.5%, macro 4H bull, RSI corridor [50, 65], volatility compression."""
        # 1. Volume Shock Failure (< 2.2x)
        df_1h_low_vol, df_4h = create_synthetic_candle_data(vol_shock_mult=1.5)  # 1.5x < 2.2x
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_low_vol if tf == '1h' else df_4h):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "Volume shock < 2.2x must be rejected")

        # 2. Thrust Failure (< 1.4x ATR)
        df_1h_low_thrust, df_4h = create_synthetic_candle_data(thrust_atr_mult=0.8)  # 0.8x < 1.4x
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_low_thrust if tf == '1h' else df_4h):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "Thrust < 1.4x ATR must be rejected")

        # 3. Resistance Headroom Failure (< 1.5%)
        df_1h_low_headroom, df_4h = create_synthetic_candle_data(headroom_pct=0.008)  # 0.8% < 1.5%
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_low_headroom if tf == '1h' else df_4h):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "Resistance headroom < 1.5% must be rejected")

        # 4. 4H Macro Bearish Failure
        df_1h, df_4h_bear = create_synthetic_candle_data(macro_bull_4h=False)
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h if tf == '1h' else df_4h_bear):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "4H Macro Bearish alignment must be rejected")

        # 5. 1H RSI Corridor Failure - Too Low (< 50.0)
        df_1h_base, df_4h_base = create_synthetic_candle_data()
        df_1h_low_rsi = df_1h_base.copy()
        df_1h_low_rsi['close'] = df_1h_low_rsi['close'] - np.linspace(0, 4000, len(df_1h_low_rsi))
        df_1h_low_rsi['open'] = df_1h_low_rsi['close'] + 50.0
        df_1h_low_rsi['high'] = df_1h_low_rsi['open'] + 10.0
        df_1h_low_rsi['low'] = df_1h_low_rsi['close'] - 10.0
        # Preserve volume shock on catalyst bar
        df_1h_low_rsi.loc[df_1h_low_rsi.index[-2], 'volume'] = df_1h_base['volume'].iloc[-2]
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_low_rsi if tf == '1h' else df_4h_base):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "1H RSI < 50.0 must be rejected by corridor restriction")

        # 6. 1H RSI Corridor Failure - Too High (> 65.0)
        df_1h_high_rsi = df_1h_base.copy()
        df_1h_high_rsi['close'] = df_1h_high_rsi['close'] + np.linspace(0, 6000, len(df_1h_high_rsi))
        df_1h_high_rsi['open'] = df_1h_high_rsi['close'] - 80.0
        df_1h_high_rsi['high'] = df_1h_high_rsi['close'] + 20.0
        df_1h_high_rsi['low'] = df_1h_high_rsi['open'] - 20.0
        df_1h_high_rsi.loc[df_1h_high_rsi.index[-2], 'volume'] = df_1h_base['volume'].iloc[-2]
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_high_rsi if tf == '1h' else df_4h_base):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "1H RSI > 65.0 must be rejected by corridor restriction")

        # 7. Volatility Compression Failure (BB bandwidth > 40th percentile AND 4H ADX >= 26.0)
        df_1h_no_comp, df_4h_no_comp = create_synthetic_candle_data(vol_compressed=False)
        # Create uncompressed wide BB bandwidth on 1H
        noise_wide = np.sin(np.linspace(0, 15, len(df_1h_no_comp))) * 800.0
        df_1h_no_comp['close'] = df_1h_no_comp['close'] + noise_wide
        df_1h_no_comp['high'] = df_1h_no_comp['close'] + 50.0
        df_1h_no_comp['low'] = df_1h_no_comp['close'] - 50.0
        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h_no_comp if tf == '1h' else df_4h_no_comp):
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, self.valid_deriv_state)
            self.assertIsNone(opp, "Setup with BB uncompressed and 4H ADX >= 26.0 must be rejected")

        print("PASS: test_gate2_technical_confluence_restrictions")

    # =========================================================================
    # TEST 4: GATE 3 (DERIVATIVES MICROSTRUCTURE VETOES)
    # =========================================================================
    def test_gate3_derivatives_restrictions(self):
        """Verify Gate 3: funding rate <= +0.025%, no LONG_FLUSH_WARNING, not LONG_OVERHEATED + HIGH risk."""
        df_1h, df_4h = create_synthetic_candle_data()

        with patch.object(self.scanner, "fetch_candles", side_effect=lambda pair, tf, limit: df_1h if tf == '1h' else df_4h):
            # 1. High Funding Rate (> 0.025%) -> Must be vetoed
            deriv_high_funding = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "funding_rate_8h_pct": 0.035,  # 0.035% > 0.025%
                        "squeeze_signal": "NONE",
                        "derivatives_regime": "EQUILIBRIUM",
                        "leverage_risk": "LOW"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, deriv_high_funding)
            self.assertIsNone(opp, "High funding rate > 0.025% must be vetoed by Gate 3")

            # 2. LONG_FLUSH_WARNING -> Must be vetoed
            deriv_flush = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "funding_rate_8h_pct": 0.010,
                        "squeeze_signal": "LONG_FLUSH_WARNING",
                        "derivatives_regime": "EQUILIBRIUM",
                        "leverage_risk": "LOW"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, deriv_flush)
            self.assertIsNone(opp, "LONG_FLUSH_WARNING must be vetoed by Gate 3")

            # 3. LONG_OVERHEATED + HIGH Risk -> Must be vetoed
            deriv_overheated = {
                "pairs": {
                    "BTC/USDT:USDT": {
                        "funding_rate_8h_pct": 0.015,
                        "squeeze_signal": "NONE",
                        "derivatives_regime": "LONG_OVERHEATED",
                        "leverage_risk": "HIGH"
                    }
                }
            }
            opp = self.scanner.evaluate_news_catalyst_opportunity("BTC/USDT:USDT", self.valid_sent_state, deriv_overheated)
            self.assertIsNone(opp, "LONG_OVERHEATED with HIGH leverage risk must be vetoed")

        print("PASS: test_gate3_derivatives_restrictions")

    # =========================================================================
    # TEST 5: STRATEGY CUSTOM_STAKE_AMOUNT SCALING (0.60X)
    # =========================================================================
    def test_custom_stake_amount_news_catalyst(self):
        """Verify custom_stake_amount applies 0.60x scaling for news_catalyst trades."""
        strat = ApexDualAlpha_Omni_V12_LinkCalibrated(config={})

        now = datetime.now(timezone.utc)
        base_stake = 300.0

        # Normal trade (no news catalyst tag)
        normal_stake = strat.custom_stake_amount(
            pair="BTC/USDT:USDT",
            current_time=now,
            current_rate=50000.0,
            proposed_stake=base_stake,
            min_stake=10.0,
            max_stake=1000.0,
            leverage=7.0,
            entry_tag="ai_pre_breakout_coiling",
            side="long"
        )
        self.assertEqual(normal_stake, 300.0, "Normal trades must receive 1.0x stake")

        # News Catalyst trade
        news_stake = strat.custom_stake_amount(
            pair="BTC/USDT:USDT",
            current_time=now,
            current_rate=50000.0,
            proposed_stake=base_stake,
            min_stake=10.0,
            max_stake=1000.0,
            leverage=7.0,
            entry_tag="ai_news_catalyst_long",
            side="long"
        )
        self.assertAlmostEqual(news_stake, 180.0, places=2, msg="News catalyst trades must receive 0.60x stake scaling (300 * 0.60 = 180)")
        print("PASS: test_custom_stake_amount_news_catalyst")

    # =========================================================================
    # TEST 6: POSITION LIFECYCLE EXITS & INVALIDATIONS
    # =========================================================================
    def test_strategy_news_catalyst_exits(self):
        """Verify strategy custom_exit handles runner hit (+4.5%), 6h rapid invalidation (-0.8% spot), and 12h cutoff (< +0.5% spot)."""
        strat = ApexDualAlpha_Omni_V12_LinkCalibrated(config={})
        open_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        # Mock Trade
        mock_trade = MagicMock()
        mock_trade.open_date_utc = open_time
        mock_trade.enter_tag = "ai_news_catalyst_long"
        mock_trade.is_short = False
        mock_trade.leverage = 3.0

        # Case 1: Runner Hit (+4.5% profit)
        exit_runner = strat.custom_exit(
            pair="SOL/USDT:USDT",
            trade=mock_trade,
            current_time=open_time + timedelta(hours=3),
            current_rate=150.0,
            current_profit=0.046  # 4.6% >= 4.5%
        )
        self.assertEqual(exit_runner, "news_tp2_runner_hit")

        # Case 2: Rapid Invalidation at 6 hours if PnL spot <= -0.80% (-2.4% leveraged at 3x)
        exit_rapid_invalid = strat.custom_exit(
            pair="SOL/USDT:USDT",
            trade=mock_trade,
            current_time=open_time + timedelta(hours=6.5),
            current_rate=145.0,
            current_profit=-0.025  # Spot = -0.025 / 3 = -0.00833 <= -0.0080 (-0.80%)
        )
        self.assertEqual(exit_rapid_invalid, "news_rapid_invalidation_6h")

        # Case 3: 6 hours with mild loss (e.g. -0.4% spot) -> Should NOT trigger rapid invalidation
        exit_mild_loss = strat.custom_exit(
            pair="SOL/USDT:USDT",
            trade=mock_trade,
            current_time=open_time + timedelta(hours=7.0),
            current_rate=148.0,
            current_profit=-0.012  # Spot = -0.012 / 3 = -0.004 (-0.4%) > -0.8%
        )
        self.assertIsNone(exit_mild_loss)

        # Case 4: Stagnation Cutoff at 12 hours if PnL spot < +0.50% (+1.5% leveraged at 3x)
        exit_stagnation = strat.custom_exit(
            pair="SOL/USDT:USDT",
            trade=mock_trade,
            current_time=open_time + timedelta(hours=13.0),
            current_rate=149.0,
            current_profit=0.010  # Spot = +0.010 / 3 = +0.00333 < +0.0050 (+0.50%)
        )
        self.assertEqual(exit_stagnation, "news_12h_cutoff")

        # Case 5: 12 hours with healthy profit (e.g. +1.0% spot) -> Should NOT trigger stagnation cutoff
        exit_healthy_gain = strat.custom_exit(
            pair="SOL/USDT:USDT",
            trade=mock_trade,
            current_time=open_time + timedelta(hours=14.0),
            current_rate=152.0,
            current_profit=0.036  # Spot = +0.036 / 3 = +0.012 (+1.2%) >= +0.5%
        )
        self.assertIsNone(exit_healthy_gain)

        print("PASS: test_strategy_news_catalyst_exits")

    # =========================================================================
    # TEST 7: DAEMON INSPECT_STALE_POSITIONS FOR NEWS TRADES
    # =========================================================================
    def test_daemon_stale_prune_news_trades(self):
        """Verify AISupervisorDaemon.inspect_stale_positions applies rapid invalidation and 12h cutoff."""
        with patch.object(AISupervisorDaemon, "__init__", lambda x: None):
            daemon = AISupervisorDaemon()
            daemon.client = MagicMock()
            daemon.state = {"pruned_trades": []}
            daemon.save_state = MagicMock()

            # Mock 3 open trades
            now = datetime.now(timezone.utc)
            t_rapid_bad = {
                "trade_id": 101,
                "pair": "SOL/USDT:USDT",
                "open_date": (now - timedelta(hours=6.5)).strftime("%Y-%m-%d %H:%M:%S"),
                "leverage": 3.0,
                "profit_pct": -2.7,  # Spot = -0.90% <= -0.80%
                "amount": 10.0,
                "enter_tag": "ai_news_catalyst_long"
            }
            t_stagnant = {
                "trade_id": 102,
                "pair": "LINK/USDT:USDT",
                "open_date": (now - timedelta(hours=13.0)).strftime("%Y-%m-%d %H:%M:%S"),
                "leverage": 3.0,
                "profit_pct": 0.6,   # Spot = +0.20% < +0.50%
                "amount": 25.0,
                "enter_tag": "ai_news_catalyst_long"
            }
            t_healthy = {
                "trade_id": 103,
                "pair": "BTC/USDT:USDT",
                "open_date": (now - timedelta(hours=8.0)).strftime("%Y-%m-%d %H:%M:%S"),
                "leverage": 7.0,
                "profit_pct": 7.0,   # Spot = +1.0% >= 0.5%
                "amount": 0.05,
                "enter_tag": "ai_news_catalyst_long"
            }

            daemon.client.get_status.return_value = [t_rapid_bad, t_stagnant, t_healthy]
            daemon.client.force_exit.return_value = (True, "OK")

            daemon.inspect_stale_positions()

            # Check that trade 101 and 102 were pruned, while 103 remained active
            self.assertIn(101, daemon.state["pruned_trades"], "Trade 101 must be pruned via 6h rapid invalidation")
            self.assertIn(102, daemon.state["pruned_trades"], "Trade 102 must be pruned via 12h stagnation cutoff")
            self.assertNotIn(103, daemon.state["pruned_trades"], "Trade 103 is healthy and must not be pruned")

        print("PASS: test_daemon_stale_prune_news_trades")

    # =========================================================================
    # TEST 8: UNFILLED RESTING MAKER ORDER TTL & PRICE DRIFT EXPIRATION
    # =========================================================================
    def test_unfilled_maker_order_ttl_and_drift(self):
        """Verify unfilled resting orders are cancelled when TTL expires or price drifts away."""
        with patch.object(AISupervisorDaemon, "__init__", lambda x: None):
            daemon = AISupervisorDaemon()
            daemon.client = MagicMock()
            daemon.state = {"pruned_trades": []}
            daemon.save_state = MagicMock()

            now = datetime.now(timezone.utc)

            # 1. News catalyst order expired by TTL (0.6h >= 0.5h)
            t_news_ttl = {
                "trade_id": 201,
                "pair": "SOL/USDT:USDT",
                "amount": 0.0,
                "open_date": (now - timedelta(minutes=35)).strftime("%Y-%m-%d %H:%M:%S"),
                "enter_tag": "ai_news_catalyst_long",
                "current_rate": 115.0,
                "orders": [{"safe_price": 114.5}]
            }

            # 2. News catalyst order expired by price drift (20 min old, but price drifted +1.8% >= 1.5%)
            t_news_drift = {
                "trade_id": 202,
                "pair": "ETH/USDT:USDT",
                "amount": 0.0,
                "open_date": (now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
                "enter_tag": "ai_news_catalyst_long",
                "current_rate": 2750.0,
                "orders": [{"safe_price": 2700.0}]  # Drift = +1.85% >= 1.5%
            }

            # 3. Technical limit order expired by TTL (2.2h >= 2.0h)
            t_tech_ttl = {
                "trade_id": 203,
                "pair": "DOGE/USDT:USDT",
                "amount": 0.0,
                "open_date": (now - timedelta(hours=2.2)).strftime("%Y-%m-%d %H:%M:%S"),
                "enter_tag": "ai_pre_breakout_coiling",
                "current_rate": 0.088,
                "orders": [{"safe_price": 0.087}]
            }

            # 4. Fresh technical order (0.8h old, drift 0.5% < 2.0%) -> Must remain open
            t_tech_fresh = {
                "trade_id": 204,
                "pair": "BTC/USDT:USDT",
                "amount": 0.0,
                "open_date": (now - timedelta(minutes=48)).strftime("%Y-%m-%d %H:%M:%S"),
                "enter_tag": "ai_pre_breakout_coiling",
                "current_rate": 84200.0,
                "orders": [{"safe_price": 84000.0}]  # Drift = 0.23% < 2.0%
            }

            daemon.client.get_status.return_value = [t_news_ttl, t_news_drift, t_tech_ttl, t_tech_fresh]
            cancelled_ids = []
            daemon.client.cancel_open_order.side_effect = lambda tid: (cancelled_ids.append(tid), (True, "OK"))[1]

            daemon.inspect_stale_positions()

            self.assertIn(201, cancelled_ids, "Unfilled news catalyst order past 30m must be cancelled")
            self.assertIn(202, cancelled_ids, "Unfilled news catalyst order with drift >= 1.5% must be cancelled")
            self.assertIn(203, cancelled_ids, "Unfilled technical order past 2.0h must be cancelled")
            self.assertNotIn(204, cancelled_ids, "Fresh unfilled technical order must NOT be cancelled")

        print("PASS: test_unfilled_maker_order_ttl_and_drift")

    # =========================================================================
    # TEST 9: DUAL-TP NEWS FALLBACK TARGETS & PARTIAL FILL PROTECTION
    # =========================================================================
    def test_dual_tp_news_fallback_and_partial_fill(self):
        """Verify dynamic take profits handles news fallback targets and protects partial fills."""
        with patch.object(AISupervisorDaemon, "__init__", lambda x: None):
            daemon = AISupervisorDaemon()
            daemon.client = MagicMock()
            daemon.state = {"ai_positions": {}, "ai_orders": []}  # Empty order history
            daemon.save_state = MagicMock()

            # Mock an active news trade entering TP1 with partial fill (5.0 units instead of 10.0)
            t_partial = {
                "trade_id": 301,
                "pair": "SOL/USDT:USDT",
                "open_rate": 100.0,
                "current_rate": 101.6,  # Above TP1 (100 + 1.5 = 101.5)
                "amount": 4.0,          # Only 4 units filled out of original 10
                "enter_tag": "ai_news_catalyst_long"
            }

            daemon.client.get_status.return_value = [t_partial]
            daemon.client.force_exit.return_value = (True, "OK")

            daemon.inspect_dynamic_take_profits()

            pos = daemon.state["ai_positions"]["301_SOL/USDT:USDT"]
            # Verify Gate 5 fallback targets initialized:
            # risk = 1.5% of 100.0 = 1.5
            # tp1 = 100 + 1.5 = 101.5
            # tp2 = 100 + 3.0 = 103.0
            # buffered_be = 100 + 0.15 * 1.5 = 100.225 -> 100.23
            self.assertEqual(pos["risk"], 1.5, "News catalyst risk must fallback to 1.5% of entry rate")
            self.assertEqual(pos["tp1_target"], 101.5, "TP1 must be +1.0R (101.5)")
            self.assertEqual(pos["tp2_target"], 103.0, "TP2 must be +2.0R (103.0)")
            expected_be = round(100.0 + 0.15 * 1.5, 2)
            self.assertEqual(pos["buffered_be"], expected_be, f"Buffered BE must be +0.15R ({expected_be})")
            self.assertTrue(pos["tp1_executed"], "TP1 must be executed at 101.6")

            # Check that scale_out_amt in force_exit did not exceed available amount (4.0)
            args, kwargs = daemon.client.force_exit.call_args
            scale_amt = kwargs.get("amount")
            self.assertLessEqual(scale_amt, 4.0, "Scale out amount must never exceed available trade amount")

        print("PASS: test_dual_tp_news_fallback_and_partial_fill")

    # =========================================================================
    # TEST 10: RUN_CYCLE CANDIDATE RADAR PRESERVATION WHEN SLOTS ARE FULL
    # =========================================================================
    def test_run_cycle_radar_preservation_when_slots_full(self):
        """Verify run_cycle updates ai_candidate_radar even when all AI slots are full."""
        with patch.object(AISupervisorDaemon, "__init__", lambda x: None):
            daemon = AISupervisorDaemon()
            daemon.client = MagicMock()
            daemon.scanner = MagicMock()
            daemon.state = {"ai_positions": {}, "ai_orders": [], "pruned_trades": []}
            daemon.save_state = MagicMock()
            daemon.pairs = ["BTC/USDT:USDT"]
            daemon.max_portfolio_slots = 3
            daemon.max_ai_slots = 1
            daemon.normal_idle_threshold_hours = 0.0
            daemon.squeeze_idle_threshold_hours = 0.0
            daemon.breakout_idle_threshold_hours = 0.0
            daemon.coiling_idle_threshold_hours = 0.0

            daemon.get_pair_cluster = MagicMock(return_value="major")
            daemon.get_sentiment_state = MagicMock(return_value=self.valid_sent_state)
            daemon.get_derivatives_state = MagicMock(return_value=self.valid_deriv_state)
            daemon.get_available_ai_slots = MagicMock(return_value=0)  # Slots FULL

            # Mock 1 open AI trade (so slots_to_fill = 0)
            t_open = {
                "trade_id": 401,
                "pair": "SOL/USDT:USDT",
                "amount": 5.0,
                "open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "enter_tag": "ai_pre_breakout_coiling",
                "profit_pct": 0.5
            }
            daemon.client.get_status.return_value = [t_open]
            daemon.client.get_trades.return_value = []

            # Mock news opportunity detected by scanner
            mock_news = {
                "pair": "BTC/USDT:USDT",
                "regime": "news_catalyst",
                "limit_price": 84000.0,
                "headline_score": 0.70,
                "volume_ratio": 2.8,
                "thrust_ratio": 1.6,
                "rr_ratio": 2.0,
                "stake_scale": 0.60
            }
            daemon.scanner.evaluate_news_catalyst_opportunity.return_value = mock_news
            daemon.scanner.evaluate_prebreakout_expansion_opportunity.return_value = None
            daemon.scanner.evaluate_opportunity.return_value = None

            daemon.run_cycle()

            # Verify that candidate radar was updated in state despite 0 slots available
            self.assertIn("ai_candidate_radar", daemon.state, "ai_candidate_radar must exist in state")
            radar = daemon.state["ai_candidate_radar"]
            self.assertEqual(len(radar), 1, "Candidate radar must contain evaluated news catalyst")
            self.assertEqual(radar[0]["pair"], "BTC/USDT:USDT")
            self.assertEqual(radar[0]["regime"], "news_catalyst")

            # Verify no new orders were dispatched because slots_to_fill was 0
            daemon.client.force_enter.assert_not_called()

        print("PASS: test_run_cycle_radar_preservation_when_slots_full")

    # =========================================================================
    # TEST 11: DISPATCH NEWS CATALYST SINGLE SCALING INTEGRATION
    # =========================================================================
    def test_dispatch_news_catalyst_single_scaling(self):
        """Verify that news catalyst dispatch passes base_stake to force_enter so custom_stake_amount scales exactly once (0.60x)."""
        with patch.object(AISupervisorDaemon, "__init__", lambda x: None):
            daemon = AISupervisorDaemon()
            daemon.pairs = ["BTC/USDT:USDT"]
            daemon.max_portfolio_slots = 3
            daemon.client = MagicMock()
            daemon.scanner = MagicMock()
            daemon.state = {"ai_orders": [], "pruned_trades": [], "ai_candidate_radar": []}
            daemon.save_state = MagicMock()
            daemon.normal_idle_threshold_hours = 0.0
            daemon.squeeze_idle_threshold_hours = 0.0
            daemon.breakout_idle_threshold_hours = 0.0
            daemon.coiling_idle_threshold_hours = 0.0

            daemon.get_pair_cluster = MagicMock(return_value="major")
            daemon.get_sentiment_state = MagicMock(return_value=self.valid_sent_state)
            daemon.get_derivatives_state = MagicMock(return_value=self.valid_deriv_state)
            daemon.get_available_ai_slots = MagicMock(return_value=1)

            daemon.client.get_status.return_value = []
            daemon.client.get_trades.return_value = []
            daemon.client.get_balance.return_value = {"total": 1000.0}
            daemon.client.force_enter.return_value = (True, {"status": "ok"})

            mock_news = {
                "pair": "BTC/USDT:USDT",
                "regime": "news_catalyst",
                "limit_price": 84000.0,
                "headline_score": 0.70,
                "volume_ratio": 2.8,
                "thrust_ratio": 1.6,
                "rr_ratio": 2.0,
                "stake_scale": 0.60,
                "risk": 1260.0,
                "tp1": 85260.0,
                "tp2": 86520.0,
                "buffered_be": 84189.0
            }
            daemon.scanner.evaluate_news_catalyst_opportunity.return_value = mock_news
            daemon.scanner.evaluate_prebreakout_expansion_opportunity.return_value = None
            daemon.scanner.evaluate_opportunity.return_value = None

            daemon.run_cycle()

            daemon.client.force_enter.assert_called_once()
            call_kwargs = daemon.client.force_enter.call_args.kwargs
            
            # Base stake for 1000 total balance with 3 slots is: (1000 / 3) * 0.95 = 316.67
            self.assertEqual(call_kwargs["pair"], "BTC/USDT:USDT")
            self.assertEqual(call_kwargs["entry_tag"], "ai_news_catalyst_long")
            self.assertEqual(call_kwargs["stake_amount"], 316.67, "force_enter must receive base_stake so custom_stake_amount can scale cleanly")

            # Check that recorded ai_orders has the effective scaled stake (190.00 = 316.67 * 0.60)
            self.assertEqual(len(daemon.state["ai_orders"]), 1)
            recorded_order = daemon.state["ai_orders"][0]
            self.assertEqual(recorded_order["stake_amount"], 190.00, "Recorded stake in state must reflect 0.60x scaling")

            # Verify that when Freqtrade custom_stake_amount processes this proposed stake, it scales to 190.00
            strat = ApexDualAlpha_Omni_V12_LinkCalibrated(config={})
            simulated_freqtrade_stake = strat.custom_stake_amount(
                pair="BTC/USDT:USDT",
                current_time=datetime.now(timezone.utc),
                current_rate=84000.0,
                proposed_stake=call_kwargs["stake_amount"],
                min_stake=10.0,
                max_stake=1000.0,
                leverage=7.0,
                entry_tag=call_kwargs["entry_tag"],
                side="long"
            )
            self.assertAlmostEqual(simulated_freqtrade_stake, 190.002, places=1, msg="Final trade stake in Freqtrade must be exactly 0.60x of base stake (not double scaled)")

        print("PASS: test_dispatch_news_catalyst_single_scaling")


if __name__ == "__main__":
    unittest.main()
