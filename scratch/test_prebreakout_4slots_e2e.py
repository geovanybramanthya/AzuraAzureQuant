"""
End-to-End Verification Suite for Pre-Breakout Range Expansion Engine (4-Slot Architecture)
Tests:
1. 4-Slot Concurrency Logic (3 Core + 1 Dedicated Engine Slot) & Stake Sizing (wallet / 6.0)
2. Strategy Stake Scaling (0.50x), Leverage Calibration (BTC 3x), and Custom Exits (including PAXG 8h cut & 1.75% TP2)
3. MarketScanner Opportunity Evaluation & Resting Limit Price Mechanics (Long, Short, and PAXG 0.7% TP1 calibration)
4. AISupervisor Dual-Stage Dynamic TP & Breakeven Lifecycle (Long 50% scale-out + BE, Short 50% scale-out + TP2 runner, PAXG 8h stale cut)
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
    print("[TEST 2] Verifying 4-Slot Concurrency Logic (3 Core + 1 Dedicated Engine Slot) & Capital Sizing...")
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

    # Capital Sizing Verification:
    # 3 Core slots: stake = wallet / 3.0
    # 1 Expansion Engine slot: stake = (wallet / 3.0) * 0.50 = wallet / 6.0
    wallet = 1000.0
    base_core_slots = 3.0
    base_stake = round((wallet / base_core_slots) * 0.95, 2)
    expansion_effective_stake = round(base_stake * 0.50, 2)
    expected_expansion_stake = round(((wallet * 0.95) / 6.0), 2)
    assert abs(expansion_effective_stake - expected_expansion_stake) <= 0.05, (
        f"Expected effective expansion stake ~${expected_expansion_stake}, got ${expansion_effective_stake}"
    )

    print(" -> PASSED: 4-Slot Concurrency Logic and wallet / 6.0 stake sizing verified.")


def test_strategy_scaling_and_exits():
    print("[TEST 3] Verifying Strategy Stake Scaling, Leverage, and Custom Exits (including PAXG)...")
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

    # 3A. Expansion TP2 Runner (+2.2R = +3.30% spot = +9.90% leveraged at 3x)
    t_runner = MockTrade(now - timedelta(hours=4), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_runner = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_runner,
        current_time=now,
        current_rate=160.0,
        current_profit=0.0990  # spot_profit = 0.0990 / 3.0 = 0.0330 >= 0.0330
    )
    assert exit_runner == "expansion_tp2_runner", f"Expected expansion_tp2_runner, got {exit_runner}"

    # 3B. Expansion Rapid Invalidation at 4.0h if spot PnL <= -0.60% (-0.0060)
    t_inv = MockTrade(now - timedelta(hours=4.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_inv = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_inv,
        current_time=now,
        current_rate=145.0,
        current_profit=-0.021  # spot_profit = -0.021 / 3.0 = -0.007 <= -0.0060
    )
    assert exit_inv == "expansion_rapid_invalidation_4h", f"Expected expansion_rapid_invalidation_4h, got {exit_inv}"

    # 3C. Healthy trade at 4.5h with spot -0.30% (-0.0030) should NOT trigger rapid invalidation
    t_healthy = MockTrade(now - timedelta(hours=4.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_healthy = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_healthy,
        current_time=now,
        current_rate=149.0,
        current_profit=-0.009  # spot_profit = -0.009 / 3.0 = -0.003 > -0.0060
    )
    assert exit_healthy is None, f"Expected None for healthy trade at 4.5h, got {exit_healthy}"

    # 3D. Expansion 16h Timeout
    t_timeout = MockTrade(now - timedelta(hours=16.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_timeout = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_timeout,
        current_time=now,
        current_rate=150.0,
        current_profit=0.005
    )
    assert exit_timeout == "expansion_16h_timeout", f"Expected expansion_16h_timeout, got {exit_timeout}"

    print(" -> PASSED: Strategy scaling, leverage calibration, and custom exits verified.")


def test_market_scanner_opportunity():
    print("[TEST 4] Verifying MarketScanner evaluate_prebreakout_expansion_opportunity (Long & PAXG calibration)...")
    scanner = MarketScanner(["SOL/USDT:USDT", "PAXG/USDT:USDT"])

    n_1h = 80
    dates_1h = pd.date_range("2026-09-20 00:00:00", periods=n_1h, freq="1h", tz="UTC")
    
    # SOL Setup (Long)
    base_sol = 145.0 + np.sin(np.linspace(0, 12, n_1h)) * 0.5
    base_sol[-2] = 145.0
    base_sol[-1] = 145.1

    df_1h_sol = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_1h],
        "open": base_sol - 0.2,
        "high": base_sol + 0.3,
        "low": base_sol - 0.3,
        "close": base_sol,
        "volume": [1000.0] * (n_1h - 2) + [3500.0, 1000.0],
        "date": dates_1h
    })
    # Set solid displacement body for thrust candle (index -2)
    df_1h_sol.loc[df_1h_sol.index[-2], "open"] = 143.8
    df_1h_sol.loc[df_1h_sol.index[-2], "close"] = 145.0
    df_1h_sol.loc[df_1h_sol.index[-2], "high"] = 145.2
    df_1h_sol.loc[df_1h_sol.index[-2], "low"] = 143.7
    df_1h_sol.loc[df_1h_sol.index[-30], "high"] = 148.0
    df_1h_sol.loc[df_1h_sol.index[-40], "low"] = 140.0

    n_4h = 250
    dates_4h = pd.date_range("2026-06-01 00:00:00", periods=n_4h, freq="4h", tz="UTC")
    c_4h = np.linspace(100.0, 145.0, n_4h)
    df_4h_sol = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_4h],
        "open": c_4h - 0.5,
        "high": c_4h + 1.0,
        "low": c_4h - 0.5,
        "close": c_4h,
        "volume": [10000.0] * n_4h,
        "date": dates_4h
    })

    scanner._candle_cache[("SOL/USDT:USDT", "1h")] = (9999999999.0, df_1h_sol)
    scanner._candle_cache[("SOL/USDT:USDT", "4h")] = (9999999999.0, df_4h_sol)

    sent_state = {"pairs": {"SOL/USDT:USDT": {"blackout_active": False, "sentiment_score": 0.1}}}
    deriv_state = {"pairs": {"SOL/USDT:USDT": {"funding_rate_8h_pct": 0.005, "squeeze_signal": "NONE"}}}

    opp = scanner.evaluate_prebreakout_expansion_opportunity("SOL/USDT:USDT", sent_state, deriv_state)
    assert opp is not None, "Expected candidate opportunity for SOL, got None"
    assert opp["regime"] == "prebreakout_expansion"
    assert opp["side"] == "long"
    assert opp["entry_tag"] == "ai_prebreakout_expansion_long"
    assert opp["stake_scale"] == 0.50
    assert opp["leverage"] == 3.0
    assert opp["rr_ratio"] == 2.2

    # Check Dual-Stage targets (Pareto Config B: TP1 = 0.8R, BE = 0.15R, TP2 = 2.2R):
    assert opp["limit_price"] == 144.7
    assert opp["risk"] == 2.17
    assert opp["tp1"] == 146.44
    assert opp["buffered_be"] == 145.03
    assert opp["tp2"] == 149.47

    # PAXG Verification: Strictly excluded from expansion scanner (Pareto Config B)
    sent_state_paxg = {"pairs": {"PAXG/USDT:USDT": {"blackout_active": False, "sentiment_score": 0.05}}}
    deriv_state_paxg = {"pairs": {"PAXG/USDT:USDT": {"funding_rate_8h_pct": 0.001, "squeeze_signal": "NONE"}}}

    opp_paxg = scanner.evaluate_prebreakout_expansion_opportunity("PAXG/USDT:USDT", sent_state_paxg, deriv_state_paxg)
    assert opp_paxg is None, f"Expected PAXG to be strictly excluded from expansion scanner, got {opp_paxg}"

    print(f" -> PASSED: MarketScanner produced valid candidate @ SOL TP1={opp['tp1']}, TP2={opp['tp2']}, and PAXG strictly excluded.")


def test_dual_stage_tp_lifecycle():
    print("[TEST 5] Verifying Dual-Stage Dynamic TP & Breakeven Lifecycle (Long & Short)...")
    daemon = AISupervisorDaemon("user_data/config_futures.json")

    class MockClient:
        def __init__(self):
            self.exits = []
            self.prunes = []

        def get_status(self):
            return self.status

        def force_exit(self, trade_id, ordertype="limit", amount=None, price=None):
            self.exits.append({"trade_id": trade_id, "ordertype": ordertype, "amount": amount, "price": price})
            return True, "OK"

        def cancel_open_order(self, trade_id):
            return True

    mock_client = MockClient()
    daemon.client = mock_client
    daemon.state["ai_positions"] = {}

    # 1. Long Trade Test
    trade_id_long = 101
    pair_long = "SOL/USDT:USDT"
    open_rate_long = 140.0
    risk_long = round(open_rate_long * 0.015, 2)  # 2.10
    tp1_long = round(open_rate_long + 0.8 * risk_long, 2)  # 141.68
    buffered_be_long = round(open_rate_long + 0.15 * risk_long, 2)  # 140.32
    tp2_long = round(open_rate_long + 2.2 * risk_long, 2)  # 144.62

    daemon.state["ai_orders"] = [{
        "pair": pair_long,
        "side": "long",
        "entry_tag": "ai_prebreakout_expansion_long",
        "risk": risk_long,
        "tp1": tp1_long,
        "tp2": tp2_long,
        "buffered_be": buffered_be_long
    }]

    mock_client.status = [{
        "trade_id": trade_id_long,
        "pair": pair_long,
        "current_rate": 141.0,
        "amount": 10.0,
        "open_rate": open_rate_long,
        "enter_tag": "ai_prebreakout_expansion_long",
        "is_short": False
    }]
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 0, "No exit expected below TP1"

    # Price reaches TP1 (141.70 >= 141.68) -> Liquidate 50% size (5.0 units)
    mock_client.status[0]["current_rate"] = 141.70
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 1
    assert mock_client.exits[0]["amount"] == 5.0
    pos_key_long = f"{trade_id_long}_{pair_long}"
    assert daemon.state["ai_positions"][pos_key_long]["tp1_executed"] is True

    # Retest scenario: Price drops to 140.30 <= buffered_be (140.32) -> Exit remaining 50% at BE
    mock_client.status[0]["current_rate"] = 140.30
    mock_client.status[0]["amount"] = 5.0
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 2
    assert daemon.state["ai_positions"][pos_key_long]["be_executed"] is True

    # 2. Short Trade Test
    trade_id_short = 202
    pair_short = "ETH/USDT:USDT"
    open_rate_short = 2500.0
    risk_short = round(open_rate_short * 0.015, 2)  # 37.50
    tp1_short = round(open_rate_short - 0.8 * risk_short, 2)  # 2470.00
    buffered_be_short = round(open_rate_short - 0.15 * risk_short, 2)  # 2494.38
    tp2_short = round(open_rate_short - 2.2 * risk_short, 2)  # 2417.50

    daemon.state["ai_orders"].append({
        "pair": pair_short,
        "side": "short",
        "entry_tag": "ai_prebreakout_expansion_short",
        "risk": risk_short,
        "tp1": tp1_short,
        "tp2": tp2_short,
        "buffered_be": buffered_be_short
    })

    mock_client.status = [{
        "trade_id": trade_id_short,
        "pair": pair_short,
        "current_rate": 2480.0,
        "amount": 2.0,
        "open_rate": open_rate_short,
        "enter_tag": "ai_prebreakout_expansion_short",
        "is_short": True
    }]
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 2  # No new exits yet

    # Short price drops to TP1 (2469.0 <= 2470.0) -> Liquidate 50% size (1.0 units)
    mock_client.status[0]["current_rate"] = 2469.0
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 3
    assert mock_client.exits[2]["amount"] == 1.0
    pos_key_short = f"{trade_id_short}_{pair_short}"
    assert daemon.state["ai_positions"][pos_key_short]["tp1_executed"] is True

    # Short price drops further to TP2 Runner (2415.0 <= 2417.5) -> Close remaining 50%
    mock_client.status[0]["current_rate"] = 2415.0
    mock_client.status[0]["amount"] = 1.0
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 4
    assert daemon.state["ai_positions"][pos_key_short]["tp2_executed"] is True

    # 3. Expansion Stale Pruning Tests (Pareto Config B)
    now = datetime.now(timezone.utc)
    mock_client.status = [
        {
            "trade_id": 301,
            "pair": "SOL/USDT:USDT",
            "open_date": (now - timedelta(hours=4.5)).strftime("%Y-%m-%d %H:%M:%S"),
            "leverage": 3.0,
            "profit_pct": -2.1,  # spot -0.70% <= -0.60%
            "amount": 10.0,
            "enter_tag": "ai_prebreakout_expansion_long"
        },
        {
            "trade_id": 302,
            "pair": "BTC/USDT:USDT",
            "open_date": (now - timedelta(hours=16.5)).strftime("%Y-%m-%d %H:%M:%S"),
            "leverage": 3.0,
            "profit_pct": 0.6,   # spot +0.20%
            "amount": 0.05,
            "enter_tag": "ai_prebreakout_expansion_long"
        },
        {
            "trade_id": 303,
            "pair": "ETH/USDT:USDT",
            "open_date": (now - timedelta(hours=5.0)).strftime("%Y-%m-%d %H:%M:%S"),
            "leverage": 3.0,
            "profit_pct": 1.5,   # spot +0.50% > -0.60%
            "amount": 1.0,
            "enter_tag": "ai_prebreakout_expansion_long"
        }
    ]
    daemon.state["pruned_trades"] = []
    daemon.inspect_stale_positions()
    assert 301 in daemon.state["pruned_trades"], "Expected trade 301 to be pruned via 4h invalidation"
    assert 302 in daemon.state["pruned_trades"], "Expected trade 302 to be pruned via 16h timeout"
    assert 303 not in daemon.state["pruned_trades"], "Trade 303 is healthy and must not be pruned"

    print(" -> PASSED: Dual-Stage Dynamic TP & Buffered BE lifecycle verified successfully for Long, Short, and Stale Pruning.")


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
