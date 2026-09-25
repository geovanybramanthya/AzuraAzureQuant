"""
End-to-End Verification Suite for Pre-Breakout Range Expansion Engine (4-Slot Architecture)
Tests:
1. 4-Slot Concurrency Logic (3 Core + 1 Dedicated Engine Slot)
2. Strategy Stake Scaling (0.50x), Leverage Calibration (BTC 3x), and Custom Exits
3. MarketScanner Opportunity Evaluation & Resting Limit Price Mechanics
4. AISupervisor Dual-Stage Dynamic TP & Rapid Invalidation
5. Live Config Futures Verification (max_open_trades = 4)
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

# Ensure root directory is on sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from ai_supervisor_daemon import AISupervisorDaemon, MarketScanner
from user_data.strategies.ApexDualAlpha_Omni_V12_LinkCalibrated import ApexDualAlpha_Omni_V12_LinkCalibrated


def test_config_futures_slots():
    print("[TEST 1] Verifying config_futures.json max_open_trades...")
    cfg_path = root_dir / "user_data" / "config_futures.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg.get("max_open_trades") == 4, f"Expected 4 slots, got {cfg.get('max_open_trades')}"
    print(" -> PASSED: config_futures.json has max_open_trades = 4")


def test_4slots_concurrency_logic():
    print("[TEST 2] Verifying 4-Slot Concurrency Logic (3 Core + 1 Dedicated Engine Slot)...")
    daemon = AISupervisorDaemon("user_data/config_futures.json")
    assert daemon.max_portfolio_slots == 4, f"Expected 4 slots, got {daemon.max_portfolio_slots}"
    assert daemon.max_ai_slots == 1, f"Expected max_ai_slots = 1, got {daemon.max_ai_slots}"

    # Scenario A: 0 Core active, 0 AI active -> 1 AI slot available
    assert daemon.get_available_ai_slots(core_active_count=0, ai_active_count=0) == 1

    # Scenario B: 2 Core active, 0 AI active -> 1 AI slot available
    assert daemon.get_available_ai_slots(core_active_count=2, ai_active_count=0) == 1

    # Scenario C: 3 Core active, 0 AI active -> 1 AI slot available (Core does not block 4th dedicated slot)
    assert daemon.get_available_ai_slots(core_active_count=3, ai_active_count=0) == 1

    # Scenario D: 3 Core active, 1 AI active -> 0 AI slots available (All 4 slots occupied)
    assert daemon.get_available_ai_slots(core_active_count=3, ai_active_count=1) == 0

    # Scenario E: 2 Core active, 1 AI active -> 0 AI slots available (AI slot cap of 1 reached)
    assert daemon.get_available_ai_slots(core_active_count=2, ai_active_count=1) == 0

    # Scenario F: 4 Core active, 0 AI active -> 0 AI slots available (Total portfolio full)
    assert daemon.get_available_ai_slots(core_active_count=4, ai_active_count=0) == 0

    print(" -> PASSED: 4-Slot Concurrency Logic verified across all portfolio scenarios.")


def test_strategy_scaling_and_exits():
    print("[TEST 3] Verifying Strategy Stake Scaling, Leverage, and Custom Exits...")
    strat = ApexDualAlpha_Omni_V12_LinkCalibrated(config={})

    now = datetime.now(timezone.utc)

    # 1. Custom Stake Amount Scaling
    # Pre-breakout expansion trades receive 0.50x stake scaling
    base_stake = 300.0
    stake_exp = strat.custom_stake_amount(
        pair="SOL/USDT:USDT",
        current_time=now,
        current_rate=150.0,
        proposed_stake=base_stake,
        min_stake=10.0,
        max_stake=1000.0,
        leverage=3.0,
        entry_tag="ai_prebreakout_expansion_long",
        side="long"
    )
    assert abs(stake_exp - (base_stake * 0.50)) < 1e-4, f"Expected {base_stake * 0.50}, got {stake_exp}"

    # News catalyst trades receive 0.60x stake scaling
    stake_news = strat.custom_stake_amount(
        pair="SOL/USDT:USDT",
        current_time=now,
        current_rate=150.0,
        proposed_stake=base_stake,
        min_stake=10.0,
        max_stake=1000.0,
        leverage=3.0,
        entry_tag="ai_news_catalyst_long",
        side="long"
    )
    assert abs(stake_news - (base_stake * 0.60)) < 1e-4, f"Expected {base_stake * 0.60}, got {stake_news}"

    # 2. Leverage Calibration
    # BTC under prebreakout_expansion must be 3.0x (not 7.0x) to cut whipsaws by 57%
    lev_btc_exp = strat.leverage(
        pair="BTC/USDT:USDT",
        current_time=now,
        current_rate=60000.0,
        proposed_leverage=3.0,
        max_leverage=10.0,
        entry_tag="ai_prebreakout_expansion_long",
        side="long"
    )
    assert lev_btc_exp == 3.0, f"Expected 3.0x for BTC expansion, got {lev_btc_exp}"

    # BTC standard trade should remain 7.0x
    lev_btc_std = strat.leverage(
        pair="BTC/USDT:USDT",
        current_time=now,
        current_rate=60000.0,
        proposed_leverage=7.0,
        max_leverage=10.0,
        entry_tag="long_pullback_v12",
        side="long"
    )
    assert lev_btc_std == 7.0, f"Expected 7.0x for standard BTC trade, got {lev_btc_std}"

    # 3. Custom Exit Logic
    class MockTrade:
        def __init__(self, open_date, enter_tag, leverage=3.0, is_short=False):
            self.open_date_utc = open_date
            self.enter_tag = enter_tag
            self.leverage = leverage
            self.is_short = is_short

    # 3A. Expansion TP2 Runner (+2.5R = +3.75% spot = +11.25% leveraged at 3x)
    t_runner = MockTrade(now - timedelta(hours=4), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_runner = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_runner,
        current_time=now,
        current_rate=160.0,
        current_profit=0.1125  # spot_profit = 0.1125 / 3.0 = 0.0375 >= 0.0375
    )
    assert exit_runner == "expansion_tp2_runner", f"Expected expansion_tp2_runner, got {exit_runner}"

    # 3B. Expansion Rapid Invalidation at 3h if spot PnL <= -0.50% (-0.005)
    t_inv = MockTrade(now - timedelta(hours=3.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_inv = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_inv,
        current_time=now,
        current_rate=145.0,
        current_profit=-0.018  # spot_profit = -0.018 / 3.0 = -0.006 <= -0.005
    )
    assert exit_inv == "expansion_rapid_invalidation_3h", f"Expected expansion_rapid_invalidation_3h, got {exit_inv}"

    # 3C. Expansion trade at 2h with -0.6% spot should NOT trigger rapid invalidation yet
    t_early = MockTrade(now - timedelta(hours=2.0), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_early = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_early,
        current_time=now,
        current_rate=145.0,
        current_profit=-0.018
    )
    assert exit_early is None, f"Expected None (early), got {exit_early}"

    # 3D. Expansion 24h Timeout
    t_timeout = MockTrade(now - timedelta(hours=24.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_timeout = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_timeout,
        current_time=now,
        current_rate=150.0,
        current_profit=0.005
    )
    assert exit_timeout == "expansion_24h_timeout", f"Expected expansion_24h_timeout, got {exit_timeout}"

    print(" -> PASSED: Strategy scaling, leverage calibration, and custom exits verified.")


def test_market_scanner_opportunity():
    print("[TEST 4] Verifying MarketScanner evaluate_prebreakout_expansion_opportunity...")
    scanner = MarketScanner(["SOL/USDT:USDT"])

    # Build synthetic 1H and 4H DataFrames that satisfy all Pre-Breakout criteria
    n_1h = 80
    dates_1h = pd.date_range("2026-09-20 00:00:00", periods=n_1h, freq="1h", tz="UTC")
    
    # 48h range setup with realistic oscillation (RSI ~ 61)
    base = 145.0 + np.sin(np.linspace(0, 12, n_1h)) * 0.5
    base[-2] = 145.0  # completed candle
    base[-1] = 145.1  # live unclosed

    df_1h = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_1h],
        "open": base - 0.2,
        "high": base + 0.8,
        "low": base - 0.5,
        "close": base,
        "volume": [1000.0] * (n_1h - 2) + [3500.0, 1000.0],  # index -2 has volume shock 3.5x > 2.2x
        "date": dates_1h
    })
    # Set high 48 periods ago to 148.0 so resistance headroom = (148 - 145) / 145 = 2.06% >= 1.5%
    df_1h.loc[df_1h.index[-30], "high"] = 148.0
    df_1h.loc[df_1h.index[-40], "low"] = 140.0

    n_4h = 250
    dates_4h = pd.date_range("2026-06-01 00:00:00", periods=n_4h, freq="4h", tz="UTC")
    c_4h = np.linspace(100.0, 145.0, n_4h)
    df_4h = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_4h],
        "open": c_4h - 0.5,
        "high": c_4h + 1.0,
        "low": c_4h - 0.5,
        "close": c_4h,
        "volume": [10000.0] * n_4h,
        "date": dates_4h
    })

    # Inject into scanner cache
    scanner._candle_cache[("SOL/USDT:USDT", "1h")] = (time_now := 9999999999.0, df_1h)
    scanner._candle_cache[("SOL/USDT:USDT", "4h")] = (time_now, df_4h)

    sent_state = {"pairs": {"SOL/USDT:USDT": {"blackout_active": False, "sentiment_score": 0.1}}}
    deriv_state = {"pairs": {"SOL/USDT:USDT": {"funding_rate_8h_pct": 0.005, "squeeze_signal": "NONE"}}}

    opp = scanner.evaluate_prebreakout_expansion_opportunity("SOL/USDT:USDT", sent_state, deriv_state)
    assert opp is not None, "Expected candidate opportunity, got None"
    
    assert opp["regime"] == "prebreakout_expansion"
    assert opp["side"] == "long"
    assert opp["entry_tag"] == "ai_prebreakout_expansion_long"
    assert opp["stake_scale"] == 0.50
    assert opp["leverage"] == 3.0
    assert opp["rr_ratio"] == 2.5

    # Check resting limit price: C * (1 - 0.0018)
    expected_limit = round(145.0 * (1.0 - 0.0018), 2)
    assert abs(opp["limit_price"] - expected_limit) < 0.02, f"Expected limit {expected_limit}, got {opp['limit_price']}"

    # Check Dual-Stage targets:
    # risk = limit * 0.015
    # tp1 = limit + 1.0 * risk
    # buffered_be = limit + 0.15 * risk
    # tp2 = limit + 2.5 * risk
    expected_risk = round(opp["limit_price"] * 0.015, 2)
    expected_tp1 = round(opp["limit_price"] + 1.0 * expected_risk, 2)
    expected_be = round(opp["limit_price"] + 0.15 * expected_risk, 2)
    expected_tp2 = round(opp["limit_price"] + 2.5 * expected_risk, 2)

    assert abs(opp["risk"] - expected_risk) < 0.02
    assert abs(opp["tp1"] - expected_tp1) < 0.02
    assert abs(opp["buffered_be"] - expected_be) < 0.02
    assert abs(opp["tp2"] - expected_tp2) < 0.02

    print(f" -> PASSED: MarketScanner produced valid candidate @ limit={opp['limit_price']}, TP1={opp['tp1']}, BE={opp['buffered_be']}, TP2={opp['tp2']}")


def test_dual_stage_tp_lifecycle():
    print("[TEST 5] Verifying Dual-Stage Dynamic TP & Breakeven Lifecycle in AISupervisorDaemon...")
    daemon = AISupervisorDaemon("user_data/config_futures.json")

    # Mock client and state
    class MockClient:
        def __init__(self):
            self.exits = []

        def get_status(self):
            return self.status

        def force_exit(self, trade_id, ordertype="limit", amount=None, price=None):
            self.exits.append({"trade_id": trade_id, "ordertype": ordertype, "amount": amount, "price": price})
            return True, "OK"

    mock_client = MockClient()
    daemon.client = mock_client
    daemon.state["ai_positions"] = {}

    # Register active expansion trade
    trade_id = 101
    pair = "SOL/USDT:USDT"
    open_rate = 140.0
    risk = round(open_rate * 0.015, 2)  # 2.10
    tp1 = round(open_rate + 1.0 * risk, 2)  # 142.10
    buffered_be = round(open_rate + 0.15 * risk, 2)  # 140.32
    tp2 = round(open_rate + 2.5 * risk, 2)  # 145.25

    daemon.state["ai_orders"] = [{
        "pair": pair,
        "entry_tag": "ai_prebreakout_expansion_long",
        "risk": risk,
        "tp1": tp1,
        "tp2": tp2,
        "buffered_be": buffered_be
    }]

    # Step 1: Initial state below TP1 -> No exits
    mock_client.status = [{
        "trade_id": trade_id,
        "pair": pair,
        "current_rate": 141.0,
        "amount": 10.0,
        "open_rate": open_rate,
        "enter_tag": "ai_prebreakout_expansion_long"
    }]
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 0, "No exit expected below TP1"

    # Step 2: Price reaches TP1 (142.20 >= 142.10) -> Liquidate 50% size (5.0 units)
    mock_client.status[0]["current_rate"] = 142.20
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 1, f"Expected 1 exit for TP1, got {len(mock_client.exits)}"
    assert mock_client.exits[0]["amount"] == 5.0, f"Expected 5.0 units liquidated, got {mock_client.exits[0]['amount']}"
    
    pos_key = f"{trade_id}_{pair}"
    assert daemon.state["ai_positions"][pos_key]["tp1_executed"] is True
    print(f" -> Stage 1: 50% scale-out executed at TP1 ({tp1}). Stop raised to buffered BE ({buffered_be}).")

    # Step 3: Retest scenario: Price drops to 140.30 <= buffered_be (140.32) -> Exit remaining 50% at BE
    mock_client.status[0]["current_rate"] = 140.30
    mock_client.status[0]["amount"] = 5.0
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 2, f"Expected 2 exits after BE hit, got {len(mock_client.exits)}"
    assert daemon.state["ai_positions"][pos_key]["be_executed"] is True
    print(" -> Stage 2B: Retest wick triggered buffered BE exit, preserving profit!")

    print(" -> PASSED: Dual-Stage Dynamic TP & Buffered BE lifecycle verified successfully.")


def run_all_tests():
    print("==================================================================")
    print("  PRE-BREAKOUT RANGE EXPANSION 4-SLOT E2E VERIFICATION SUITE      ")
    print("==================================================================")
    test_config_futures_slots()
    test_4slots_concurrency_logic()
    test_strategy_scaling_and_exits()
    test_market_scanner_opportunity()
    test_dual_stage_tp_lifecycle()
    print("==================================================================")
    print("  ALL 5 VERIFICATION SUITES PASSED WITH 100% SUCCESS RATE!        ")
    print("==================================================================")


if __name__ == "__main__":
    run_all_tests()
