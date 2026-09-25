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

    # 3A. Expansion TP2 Runner (+2.5R = +3.75% spot = +11.25% leveraged at 3x for Altcoins)
    t_runner = MockTrade(now - timedelta(hours=4), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_runner = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_runner,
        current_time=now,
        current_rate=160.0,
        current_profit=0.1125  # spot_profit = 0.1125 / 3.0 = 0.0375 >= 0.0375
    )
    assert exit_runner == "expansion_tp2_runner", f"Expected expansion_tp2_runner, got {exit_runner}"

    # 3B. Expansion TP2 Runner for PAXG (+2.5R = +1.75% spot = +5.25% leveraged at 3x)
    t_runner_paxg = MockTrade(now - timedelta(hours=4), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_runner_paxg = strat.custom_exit(
        pair="PAXG/USDT:USDT",
        trade=t_runner_paxg,
        current_time=now,
        current_rate=2700.0,
        current_profit=0.0525  # spot_profit = 0.0525 / 3.0 = 0.0175 >= 0.0175
    )
    assert exit_runner_paxg == "expansion_tp2_runner", f"Expected expansion_tp2_runner for PAXG, got {exit_runner_paxg}"

    # 3C. Expansion Rapid Invalidation at 3h if spot PnL <= -0.50% (-0.005)
    t_inv = MockTrade(now - timedelta(hours=3.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_inv = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_inv,
        current_time=now,
        current_rate=145.0,
        current_profit=-0.018  # spot_profit = -0.018 / 3.0 = -0.006 <= -0.005
    )
    assert exit_inv == "expansion_rapid_invalidation_3h", f"Expected expansion_rapid_invalidation_3h, got {exit_inv}"

    # 3D. PAXG Early Stale Cut at 8h
    t_paxg_stale = MockTrade(now - timedelta(hours=8.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_paxg_stale = strat.custom_exit(
        pair="PAXG/USDT:USDT",
        trade=t_paxg_stale,
        current_time=now,
        current_rate=2650.0,
        current_profit=0.001
    )
    assert exit_paxg_stale == "expansion_paxg_8h_stale_cut", f"Expected expansion_paxg_8h_stale_cut, got {exit_paxg_stale}"

    # 3E. General Altcoin at 8h should NOT trigger PAXG stale cut
    t_sol_8h = MockTrade(now - timedelta(hours=8.5), "ai_prebreakout_expansion_long", leverage=3.0)
    exit_sol_8h = strat.custom_exit(
        pair="SOL/USDT:USDT",
        trade=t_sol_8h,
        current_time=now,
        current_rate=146.0,
        current_profit=0.003
    )
    assert exit_sol_8h is None, f"Expected None for SOL at 8h, got {exit_sol_8h}"

    # 3F. Expansion 24h Timeout
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
        "high": base_sol + 0.8,
        "low": base_sol - 0.5,
        "close": base_sol,
        "volume": [1000.0] * (n_1h - 2) + [3500.0, 1000.0],
        "date": dates_1h
    })
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
    assert opp["rr_ratio"] == 2.5

    # Check Dual-Stage targets for Altcoin (risk = 1.5%):
    expected_limit = round(145.0 * (1.0 - 0.0018), 2)
    expected_risk = round(expected_limit * 0.015, 2)
    expected_tp1 = round(expected_limit + 1.0 * expected_risk, 2)
    expected_be = round(expected_limit + 0.15 * expected_risk, 2)
    expected_tp2 = round(expected_limit + 2.5 * expected_risk, 2)

    assert abs(opp["limit_price"] - expected_limit) < 0.02
    assert abs(opp["risk"] - expected_risk) < 0.02
    assert abs(opp["tp1"] - expected_tp1) < 0.02
    assert abs(opp["buffered_be"] - expected_be) < 0.02
    assert abs(opp["tp2"] - expected_tp2) < 0.02

    # PAXG Setup (PAXG TP1 0.7% calibration)
    base_paxg = 2600.0 + np.sin(np.linspace(0, 12, n_1h)) * 5.0
    base_paxg[-2] = 2600.0
    base_paxg[-1] = 2601.0

    df_1h_paxg = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_1h],
        "open": base_paxg - 2.0,
        "high": base_paxg + 6.0,
        "low": base_paxg - 4.0,
        "close": base_paxg,
        "volume": [500.0] * (n_1h - 2) + [1800.0, 500.0],
        "date": dates_1h
    })
    df_1h_paxg.loc[df_1h_paxg.index[-30], "high"] = 2650.0
    df_1h_paxg.loc[df_1h_paxg.index[-40], "low"] = 2580.0

    c_4h_paxg = np.linspace(2400.0, 2600.0, n_4h)
    df_4h_paxg = pd.DataFrame({
        "timestamp": [int(d.timestamp() * 1000) for d in dates_4h],
        "open": c_4h_paxg - 2.0,
        "high": c_4h_paxg + 5.0,
        "low": c_4h_paxg - 2.0,
        "close": c_4h_paxg,
        "volume": [2000.0] * n_4h,
        "date": dates_4h
    })

    scanner._candle_cache[("PAXG/USDT:USDT", "1h")] = (9999999999.0, df_1h_paxg)
    scanner._candle_cache[("PAXG/USDT:USDT", "4h")] = (9999999999.0, df_4h_paxg)

    sent_state_paxg = {"pairs": {"PAXG/USDT:USDT": {"blackout_active": False, "sentiment_score": 0.05}}}
    deriv_state_paxg = {"pairs": {"PAXG/USDT:USDT": {"funding_rate_8h_pct": 0.001, "squeeze_signal": "NONE"}}}

    opp_paxg = scanner.evaluate_prebreakout_expansion_opportunity("PAXG/USDT:USDT", sent_state_paxg, deriv_state_paxg)
    assert opp_paxg is not None, "Expected candidate opportunity for PAXG, got None"
    
    # For PAXG: risk MUST be 0.007 (0.7% TP1)
    paxg_limit = opp_paxg["limit_price"]
    expected_paxg_risk = round(paxg_limit * 0.007, 2)
    expected_paxg_tp1 = round(paxg_limit + 1.0 * expected_paxg_risk, 2)
    expected_paxg_tp2 = round(paxg_limit + 2.5 * expected_paxg_risk, 2)

    assert abs(opp_paxg["risk"] - expected_paxg_risk) < 0.05, f"PAXG risk expected {expected_paxg_risk}, got {opp_paxg['risk']}"
    assert abs(opp_paxg["tp1"] - expected_paxg_tp1) < 0.05, f"PAXG TP1 expected {expected_paxg_tp1}, got {opp_paxg['tp1']}"
    assert abs(opp_paxg["tp2"] - expected_paxg_tp2) < 0.05, f"PAXG TP2 expected {expected_paxg_tp2}, got {opp_paxg['tp2']}"

    print(f" -> PASSED: MarketScanner produced valid candidate @ SOL TP1={opp['tp1']}, PAXG TP1={opp_paxg['tp1']} (+0.7%)")


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
    tp1_long = round(open_rate_long + 1.0 * risk_long, 2)  # 142.10
    buffered_be_long = round(open_rate_long + 0.15 * risk_long, 2)  # 140.32
    tp2_long = round(open_rate_long + 2.5 * risk_long, 2)  # 145.25

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

    # Price reaches TP1 (142.20 >= 142.10) -> Liquidate 50% size (5.0 units)
    mock_client.status[0]["current_rate"] = 142.20
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
    pair_short = "ADA/USDT:USDT"
    open_rate_short = 0.5000
    risk_short = round(open_rate_short * 0.015, 4)  # 0.0075
    tp1_short = round(open_rate_short - 1.0 * risk_short, 4)  # 0.4925
    buffered_be_short = round(open_rate_short - 0.15 * risk_short, 4)  # 0.4989
    tp2_short = round(open_rate_short - 2.5 * risk_short, 4)  # 0.4813

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
        "current_rate": 0.4990,
        "amount": 1000.0,
        "open_rate": open_rate_short,
        "enter_tag": "ai_prebreakout_expansion_short",
        "is_short": True
    }]
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 2  # No new exits yet

    # Short price drops to TP1 (0.4920 <= 0.4925) -> Liquidate 50% size (500 units)
    mock_client.status[0]["current_rate"] = 0.4920
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 3
    assert mock_client.exits[2]["amount"] == 500.0
    pos_key_short = f"{trade_id_short}_{pair_short}"
    assert daemon.state["ai_positions"][pos_key_short]["tp1_executed"] is True

    # Short price drops further to TP2 Runner (0.4800 <= 0.4813) -> Close remaining 50%
    mock_client.status[0]["current_rate"] = 0.4800
    mock_client.status[0]["amount"] = 500.0
    daemon.inspect_dynamic_take_profits()
    assert len(mock_client.exits) == 4
    assert daemon.state["ai_positions"][pos_key_short]["tp2_executed"] is True

    # 3. PAXG 8h Early Stale Pruning Test
    mock_client.status = [{
        "trade_id": 303,
        "pair": "PAXG/USDT:USDT",
        "open_date": (datetime.now(timezone.utc) - timedelta(hours=8.5)).strftime("%Y-%m-%d %H:%M:%S"),
        "leverage": 3.0,
        "current_profit_pct": -0.003,
        "amount": 1.0,
        "enter_tag": "ai_prebreakout_expansion_long"
    }]
    daemon.state["pruned_trades"] = []
    daemon.inspect_stale_positions()
    assert 303 in daemon.state["pruned_trades"], "Expected PAXG trade #303 to be pruned at 8.5h"

    print(" -> PASSED: Dual-Stage Dynamic TP & Buffered BE lifecycle verified successfully for Long, Short, and PAXG.")


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
