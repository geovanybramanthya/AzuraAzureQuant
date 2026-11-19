#!/usr/bin/env python3
"""Deterministic unit tests for ApexDualAlpha_Omni_V29_LiveReady live-safety invariants."""

import json
import math
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "user_data" / "strategies"))

from ApexDualAlpha_Omni_V29_LiveReady import ApexDualAlpha_Omni_V29_LiveReady
from freqtrade.persistence import Trade


def make_cfg(**overrides):
    cfg = {
        "max_open_trades": 8,
        "stake_currency": "USDT",
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 0.95,
        "dry_run": False,
        "entry_pricing": {"price_side": "other"},
        "exit_pricing": {"price_side": "other"},
        "order_types": {"entry": "limit", "exit": "limit"},
    }
    cfg.update(overrides)
    return cfg


def make_candle(**overrides):
    c = {
        "idx": 100,
        "open": 100.0,
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "atr": 1.5,
        "atr_pct": 0.015,
        "atr_pct_med168": 0.014,
        "atr_pct_med672": 0.012,
        "rel_vol_672": 1.6,
        "btc_corr_336": 0.80,
        "btc_corr_672": 0.80,
        "rsi": 55.0,
        "slowk": 45.0,
        "slowd": 40.0,
        "ema_9": 99.0,
        "ema_21": 98.0,
        "ema_50": 95.0,
        "ema_200": 90.0,
        "adx_4h": 26.0,
        "macro_bull_4h": True,
        "macro_bear_4h": False,
        "btc_strong_bull_4h": True,
        "funding_rate": 0.0001,
        "funding_mean_24h": 0.0001,
        "fr_zscore_168": 0.0,
        "ema168_slope24": 0.01,
        "dc72_pos": 0.5,
        "btc_ema168_slope24": 0.01,
        "volume": 1000.0,
        "volume_mean_20": 900.0,
        "support_level_20": 94.0,
        "bb_middleband": 98.0,
        "bb_upperband": 102.0,
        "bb_lowerband": 94.0,
        "er_12": 0.30,
        "er_24": 0.30,
        "ser_12": 0.20,
        "cs_bull_breadth": 0.55,
        "cs_bear_breadth": 0.20,
        "cs_bull_4h": 0.55,
        "cs_bear_4h": 0.20,
        "cs_er12_mean": 0.30,
        "cs_mom_rank": 3.0,
        "cs_active_n": 8.0,
        "fr_div": 0.0,
        "adx_rising_4h": True,
    }
    c.update(overrides)
    return c


def test_1_small_live_wallet_no_dry_run_wallet():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=False))
    assert strat._last_known_wallet == 0.0, f"Expected 0.0 init in live, got {strat._last_known_wallet}"
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    strat._candle_cache["SOL/USDT:USDT"] = {dt - timedelta(hours=1): make_candle()}
    with patch.object(Trade, "get_open_trades", return_value=[]):
        # $100 live account: tradable = 95, proposed = 95/8 = 11.875, max_stake = 95.0
        st = strat.custom_stake_amount(
            "SOL/USDT:USDT", dt, 150.0, proposed_stake=11.875, min_stake=5.0, max_stake=95.0,
            leverage=3.0, entry_tag="long_pullback_alpha", side="long",
        )
        assert st >= 5.6 and st <= 95.0, f"Small $100 live stake invalid: {st}"
        assert abs(strat._last_known_wallet - 100.0) < 0.01, f"Wallet tracking wrong: {strat._last_known_wallet}"
        ok = strat.confirm_trade_entry(
            "SOL/USDT:USDT", "limit", amount=(st * 3.0 / 150.0), rate=150.0,
            time_in_force="GTC", current_time=dt, entry_tag="long_pullback_alpha", side="long",
        )
        assert ok is True, "confirm_trade_entry falsely rejected valid $100 live trade!"
    print("[PASS] Test 1: Small $100 live wallet (no dry_run_wallet) sizes and confirms cleanly")


def test_2_drawdown_over_25pct_no_stale_latch():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=True, dry_run_wallet=1000.0))
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    strat._candle_cache["SOL/USDT:USDT"] = {dt - timedelta(hours=1): make_candle()}
    with patch.object(Trade, "get_open_trades", return_value=[]):
        # Simulate -40% drawdown to $600 wallet -> tradable = 570, proposed = 570/8 = 71.25
        st = strat.custom_stake_amount(
            "SOL/USDT:USDT", dt, 150.0, proposed_stake=71.25, min_stake=5.0, max_stake=570.0,
            leverage=3.0, entry_tag="long_pullback_alpha", side="long",
        )
        assert abs(strat._last_known_wallet - 600.0) < 0.01, f"Stale latch after -40% DD: {strat._last_known_wallet}"
        ok = strat.confirm_trade_entry(
            "SOL/USDT:USDT", "limit", amount=(st * 3.0 / 150.0), rate=150.0,
            time_in_force="GTC", current_time=dt, entry_tag="long_pullback_alpha", side="long",
        )
        assert ok is True, "confirm_trade_entry blocked valid trade after -40% drawdown!"
    print("[PASS] Test 2: -40% real drawdown updates _last_known_wallet cleanly without peak latch")


def test_3_large_500k_paxg_cap_zero_dust_and_scaleout_no_clobber():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=False))
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    strat._candle_cache["PAXG/USDT:USDT"] = {dt - timedelta(hours=1): make_candle(rel_vol_672=0.45, atr_pct_med672=0.004)}
    with patch.object(Trade, "get_open_trades", return_value=[]):
        # $500k wallet, tradable = $475k, proposed = 475k / 8 = 59,375; Bybit PAXG max_stake cap = $16,000
        st = strat.custom_stake_amount(
            "PAXG/USDT:USDT", dt, 2700.0, proposed_stake=59375.0, min_stake=5.0, max_stake=16000.0,
            leverage=3.0, entry_tag="long_pullback_alpha", side="long",
        )
        assert st == 0.0, f"Expected 0.0 (reject dust on capped PAXG at $500k), got {st}"
        assert abs(strat._last_known_wallet - 500000.0) < 1.0, f"Wallet corrupted by PAXG cap: {strat._last_known_wallet}"

        # Now ensure _eval_scaleout_plan called with pair-capped max_stake does NOT clobber _last_known_wallet downward
        tr = SimpleNamespace(
            pair="SOL/USDT:USDT", is_short=False, enter_tag="long_breakout_expansion",
            stake_amount=100000.0, open_rate=150.0, open_date_utc=dt - timedelta(hours=5),
            leverage=3.0, amount=2000.0, nr_of_successful_exits=0, has_open_orders=False,
        )
        c_sol = make_candle(close=160.0, ema_9=158.0, ema_21=155.0, rsi=65.0, adx_4h=29.0)
        strat._eval_scaleout_plan("SOL/USDT:USDT", tr, dt, 160.0, 0.05, c_sol, min_stake=5.0, max_stake=16000.0)
        assert strat._last_known_wallet >= 500000.0, f"_eval_scaleout_plan clobbered wallet to {strat._last_known_wallet}"
    print("[PASS] Test 3: $500k PAXG contract cap blocks dust cleanly and _eval_scaleout_plan preserves wallet")


def test_4_fail_closed_exceptions():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg())
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    strat._candle_cache["SOL/USDT:USDT"] = {dt - timedelta(hours=1): make_candle()}
    with patch.object(Trade, "get_open_trades", side_effect=RuntimeError("DB locked")):
        ok = strat.confirm_trade_entry(
            "SOL/USDT:USDT", "limit", 1.0, 150.0, "GTC", dt, "long_pullback_alpha", "long"
        )
        assert ok is False, "confirm_trade_entry MUST fail-closed (False) on exception!"
        st = strat.custom_stake_amount(
            "SOL/USDT:USDT", dt, 150.0, proposed_stake=100.0, min_stake=5.0, max_stake=500.0,
            leverage=3.0, entry_tag="long_pullback_alpha", side="long",
        )
        assert st == 0.0, f"custom_stake_amount MUST fail-closed (0.0) on exception, got {st}!"
    print("[PASS] Test 4: confirm_trade_entry & custom_stake_amount fail-closed on DB/ORM exceptions")


def test_5_short_heat_budget_blocks_excess():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=True, dry_run_wallet=1000.0))
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    strat._candle_cache["LINK/USDT:USDT"] = {dt - timedelta(hours=1): make_candle(close=15.0, rsi=60.0)}
    strat._candle_cache["SOL/USDT:USDT"] = {dt - timedelta(hours=1): make_candle(close=150.0)}
    open_sh = SimpleNamespace(
        pair="SOL/USDT:USDT", is_short=True, enter_tag="short_atr_fade_alpha",
        stake_amount=540.0, open_rate=150.0, nr_of_successful_exits=0,
        calc_profit_ratio=lambda r: 0.0,
    )
    with patch.object(Trade, "get_open_trades", return_value=[open_sh]):
        st = strat.custom_stake_amount(
            "LINK/USDT:USDT", dt, 15.0, proposed_stake=118.75, min_stake=5.0, max_stake=410.0,
            leverage=3.0, entry_tag="short_atr_fade_alpha", side="short",
        )
        assert st == 0.0, f"Expected 0.0 when underisked_short_tied exceeds cap, got {st}"
    print("[PASS] Test 5: Excess underisked short heat blocks new short entry (0.0)")


def test_6_micro_account_min_stake_exit_sync():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=True, dry_run_wallet=100.0))
    dt = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    # ETH at $3500, min amount 0.01 ETH => notional $35, margin at 3x = $11.67 * 1.05 = $12.25
    strat.dp = SimpleNamespace(
        runmode=SimpleNamespace(value="live"),
        market=lambda p: {"limits": {"amount": {"min": 0.01}, "cost": {"min": 5.0}}, "contractSize": 1.0},
    )
    c_eth = make_candle(close=3650.0, ema_9=3600.0, ema_21=3550.0, rsi=64.0, adx_4h=29.0, bb_upperband=3620.0)
    strat._candle_cache["ETH/USDT:USDT"] = {dt - timedelta(hours=1): c_eth}
    # Small $16 margin trade on $100 account; partial 60% = $9.60 margin < $12.25 min_stake!
    tr = SimpleNamespace(
        pair="ETH/USDT:USDT", is_short=False, enter_tag="long_breakout_expansion",
        stake_amount=16.0, open_rate=3500.0, open_date_utc=dt - timedelta(hours=4),
        leverage=3.0, amount=(16.0 * 3.0 / 3500.0), nr_of_successful_exits=0, has_open_orders=False,
        max_rate=3650.0, min_rate=3500.0,
    )
    with patch.object(Trade, "get_open_trades", return_value=[tr]):
        exit_tag = strat.custom_exit("ETH/USDT:USDT", tr, dt, 3650.0, 0.055)
        assert exit_tag == "breakout_expansion_tp", f"custom_exit deferred even though partial cannot clear min_stake! got {exit_tag}"
    print("[PASS] Test 6: Micro-account custom_exit syncs real exchange min_stake and takes full TP instead of deferring")


def test_7_market_order_price_side_and_candle_nan():
    # Lookahead CLI forces entry=market, price_side=other
    cfg_la = make_cfg(order_types={"entry": "market", "exit": "market"}, entry_pricing={"price_side": "other"})
    strat = ApexDualAlpha_Omni_V29_LiveReady(cfg_la)
    assert cfg_la["entry_pricing"]["price_side"] == "other", "Market entry price_side was wrongly clobbered to same!"

    # NaN safety in _get_candle_info live fallback
    df_nan = pd.DataFrame([{"close": 100.0, "rsi": np.nan, "slowk": None, "ema_9": float("inf"), "er_24": np.nan}])
    strat.dp = SimpleNamespace(
        runmode=SimpleNamespace(value="live"),
        get_analyzed_dataframe=lambda pair, tf: (df_nan, datetime.now(timezone.utc)),
    )
    rec = strat._get_candle_info("ETH/USDT:USDT", datetime.now(timezone.utc))
    assert rec is not None and math.isfinite(rec["rsi"]) and math.isfinite(rec["slowk"]) and math.isfinite(rec["ema_9"]) and math.isfinite(rec["er_24"])
    print("[PASS] Test 7: Market entry price_side preserved for lookahead-analysis & NaN/Inf sanitized in _get_candle_info")


def test_8_derivatives_state_ttl():
    strat = ApexDualAlpha_Omni_V29_LiveReady(make_cfg(dry_run=False))
    strat.dp = SimpleNamespace(runmode=SimpleNamespace(value="live"), get_pair_dataframe=lambda **kw: pd.DataFrame())
    df = pd.DataFrame({"date": pd.date_range("2026-11-01", periods=5, freq="1h", tz="UTC"), "close": [100.0] * 5})
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "user_data" / "data"
        p.mkdir(parents=True)
        # Stale file (600s old)
        (p / "derivatives_state.json").write_text(
            json.dumps({"last_updated": time.time() - 600.0, "pairs": {"SOL/USDT:USDT": {"funding_rate_8h_pct": 0.50}}}),
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(td)
            out_stale = strat._merge_causal_funding(df.copy(), "SOL/USDT:USDT")
            assert abs(out_stale["funding_rate"].iloc[-1] - 0.0001) < 1e-9, "Stale derivatives_state.json was NOT ignored!"
            # Fresh file (20s old)
            (p / "derivatives_state.json").write_text(
                json.dumps({"last_updated": time.time() - 20.0, "pairs": {"SOL/USDT:USDT": {"funding_rate_8h_pct": 0.05}}}),
                encoding="utf-8",
            )
            out_fresh = strat._merge_causal_funding(df.copy(), "SOL/USDT:USDT")
            assert abs(out_fresh["funding_rate"].iloc[-1] - 0.0005) < 1e-9, "Fresh derivatives_state.json was NOT applied!"
        finally:
            os.chdir(old_cwd)
    print("[PASS] Test 8: derivatives_state.json enforces 180s staleness TTL cleanly")


if __name__ == "__main__":
    test_1_small_live_wallet_no_dry_run_wallet()
    test_2_drawdown_over_25pct_no_stale_latch()
    test_3_large_500k_paxg_cap_zero_dust_and_scaleout_no_clobber()
    test_4_fail_closed_exceptions()
    test_5_short_heat_budget_blocks_excess()
    test_6_micro_account_min_stake_exit_sync()
    test_7_market_order_price_side_and_candle_nan()
    test_8_derivatives_state_ttl()
    print("\nALL 8 LIVE-SAFETY UNIT TESTS PASSED!")
