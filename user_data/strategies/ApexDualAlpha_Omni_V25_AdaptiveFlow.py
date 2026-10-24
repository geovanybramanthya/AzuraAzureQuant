"""
ApexDualAlpha_Omni_V25_AdaptiveFlow

Architecture: SOTA Adaptive Flow Dual-Alpha & Bearish Breakdown Continuation Engine for Bybit Linear Futures.

Key Structural Components & Quantum Upgrades (V25):

1. Pillar 1: Localized Sentiment Gating & Global Crash Shield:
   - Eliminates portfolio-wide veto bug. Isolates asset-specific news blackout to target pair.
   - Global cancellation applies strictly when macro_crash_alert is active or macro_score <= -0.45.

2. Pillar 2: Stochastic Momentum Regime Window (3-Bar Flex):
   - Replaces fragile 1-bar crossing with momentum inflection window:
     stoch_regime_long: ((SlowK < 45.0) & (SlowK > SlowD) & (SlowK > SlowK[t-1])) | crossed_above(SlowK, SlowD).
     stoch_regime_short: ((SlowK > 55.0) & (SlowK < SlowD) & (SlowK < SlowK[t-1])) | crossed_below(SlowK, SlowD).

3. Pillar 3: Sub-Engine 4B (Bearish Breakdown Continuation Alpha):
   - Captures confirmed bear trend momentum breakdown:
     Close < EMA21 * 0.985, Close < EMA50, RSI in [30.0, 48.0], SlowK < SlowD, Volume > 1.15 * Volume_20.
   - Dedicated quick take profit (+3.5%) and tight stop loss (-1.8% spot) protection.

4. Pillar 4: Carter TTM Squeeze 3-Bar Follow-Through & Dynamic ADX Gating:
   - Squeeze follow-through expansion across 3 bars post release.
   - Adaptive ADX relaxation (down to 16.0) during prolonged volatility compression (in_squeeze >= 5 of 6 bars).

5. Preserved Institutional Production Protections:
   - SOTA Minimal ROI Ladder (0m: +52.6%, 180m: +18.0%, 467m: +15.5%, 688m: +9.2%, 1173m: +1.0%).
   - Structural Stoploss maintained at -29.7% ROE (-9.9% spot on 3x).
   - Trailing Stop Positive Ratchet (Locks +21.7% ROE once +24.9% ROE is reached).
   - Asymmetric Leverage Calibration (6.0x BTC, 3.0x Alts and Shorts).
   - Dynamic Chandelier ATR Ratchet for mature runners (peak profit >= 15%, trailing 2.5x ATR).
   - Smart 4th Slot Directional Cluster Gating.
   - Calibrated Resting Maker Limit Engine (+0.020% Bybit rebate).
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib
import talib.abstract as ta
from technical import qtpylib

from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
)
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)


class ApexDualAlpha_Omni_V25_AdaptiveFlow(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # SOTA Calibrated Tactical Momentum ROI Ladder
    minimal_roi = {
        "0": 0.526,
        "180": 0.180,
        "467": 0.155,
        "688": 0.092,
        "1173": 0.010,
    }

    # Structural Stoploss (-29.7% ROE at 3x = -9.9% spot)
    stoploss = -0.297

    # Trailing Stoppositive Ratchet (Locks +21.7% ROE once +24.9% ROE is reached)
    trailing_stop = True
    trailing_stop_positive = 0.217
    trailing_stop_positive_offset = 0.249
    trailing_only_offset_is_reached = True

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    order_time_in_force = {
        "entry": "GTC",
        "exit": "GTC",
    }

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = True
    exit_profit_offset = 0.002
    ignore_roi_if_entry_signal = False
    startup_candle_count: int = 200

    # In-memory O(1) candle lookup cache for custom_exit, custom_entry_price, and smart gating
    _candle_cache: Dict[str, Dict[datetime, Dict[str, float]]] = {}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._candle_cache = {}

    # Dynamic thread-safe cached sentiment state (TTL = 15s)
    _sentiment_cache: Dict[str, Any] = {}
    _sentiment_cache_time: float = 0.0
    _sentiment_lock = threading.Lock()

    def _get_cached_sentiment(self, pair: str | None = None) -> Dict[str, Any]:
        """Thread-safe retrieval of sentiment_state.json with 15s TTL cache and staleness guard.
        In backtesting/hyperopt, returns safe_neutral to prevent lookahead / snapshot state leakage.
        """
        safe_neutral = {
            "last_updated": None,
            "daemon_status": "STALE_OR_UNAVAILABLE",
            "is_stale": True,
            "macro_sentiment": {
                "score": 0.0,
                "regime": "NEUTRAL",
                "macro_blackout_active": False,
                "is_macro_blackout_active": False,
                "macro_crash_alert": False,
                "is_macro_crash_alert": False,
            },
            "macro_blackout_active": False,
            "is_macro_blackout_active": False,
            "macro_crash_alert": False,
            "is_macro_crash_alert": False,
            "pairs": {},
        }

        if hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) in ("backtest", "hyperopt"):
                return safe_neutral

        now = time.time()
        need_reload = (now - self._sentiment_cache_time >= 15.0) or not self._sentiment_cache

        if need_reload:
            with self._sentiment_lock:
                if (time.time() - self._sentiment_cache_time >= 15.0) or not self._sentiment_cache:
                    custom_path = getattr(self, "_sentiment_file_path", None) or os.environ.get("FREQTRADE_SENTIMENT_FILE")
                    target_file = Path(custom_path) if custom_path else Path("user_data/data/sentiment_state.json")

                    loaded_data = None
                    if target_file.is_file():
                        try:
                            with open(target_file, "r", encoding="utf-8") as f:
                                loaded_data = json.load(f)
                        except Exception as e:
                            logger.debug(f"Failed to load sentiment state from {target_file}: {e}")

                    if not loaded_data:
                        alt_file = Path("/freqtrade/user_data/data/sentiment_state.json")
                        if alt_file.is_file():
                            try:
                                with open(alt_file, "r", encoding="utf-8") as f:
                                    loaded_data = json.load(f)
                            except Exception:
                                pass

                    if not isinstance(loaded_data, dict):
                        self._sentiment_cache = safe_neutral
                    else:
                        lu_val = loaded_data.get("last_updated")
                        is_stale = False
                        if not lu_val:
                            is_stale = True
                        else:
                            try:
                                if isinstance(lu_val, (int, float)):
                                    lu_epoch = float(lu_val)
                                elif isinstance(lu_val, str):
                                    if lu_val.endswith("Z"):
                                        lu_val = lu_val[:-1] + "+00:00"
                                    lu_dt = datetime.fromisoformat(lu_val)
                                    lu_epoch = lu_dt.timestamp()
                                else:
                                    lu_epoch = 0.0

                                if now - lu_epoch > 180.0:
                                    is_stale = True
                            except Exception:
                                is_stale = True

                        if is_stale:
                            self._sentiment_cache = safe_neutral
                        else:
                            self._sentiment_cache = loaded_data

                    self._sentiment_cache_time = now

        raw_state = self._sentiment_cache or {}
        if pair is None:
            return raw_state

        pairs_dict = raw_state.get("pairs", {})
        pair_sent = pairs_dict.get(pair) or pairs_dict.get(f"{pair}:USDT") or pairs_dict.get(pair.split(":")[0]) or {}
        macro_sent = raw_state.get("macro_sentiment", {})

        p_score = float(pair_sent.get("sentiment_score", 0.0) or 0.0)
        is_event_blackout = bool(pair_sent.get("blackout_active", False) or pair_sent.get("is_event_risk_blackout", False))
        macro_blackout = bool(raw_state.get("macro_blackout_active", False) or macro_sent.get("macro_blackout_active", False) or macro_sent.get("is_macro_blackout_active", False))
        macro_crash = bool(raw_state.get("macro_crash_alert", False) or macro_sent.get("macro_crash_alert", False) or macro_sent.get("is_macro_crash_alert", False))
        macro_score_val = float(macro_sent.get("score", 0.0) if macro_sent else raw_state.get("macro_score", 0.0) or 0.0)

        resolved = dict(pair_sent)
        resolved.update({
            "pair": pair,
            "pair_score": p_score,
            "sentiment_score": p_score,
            "is_event_risk_blackout": is_event_blackout,
            "blackout_active": is_event_blackout,
            "macro_blackout_active": macro_blackout,
            "is_macro_blackout_active": macro_blackout,
            "macro_crash_alert": macro_crash,
            "is_macro_crash_alert": macro_crash,
            "macro_score": macro_score_val,
            "macro_sentiment": macro_sent,
            "pairs": pairs_dict,
            "last_updated": raw_state.get("last_updated"),
            "is_stale": raw_state.get("is_stale", False),
        })
        return resolved

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        """
        Microsecond Gating & Smart 4th Slot Directional Cluster Gating Callback:
        Evaluated immediately before dispatching order to the exchange.

        1. Microsecond News Sentiment Gating (V25 Localized):
           - In live trading, aborts entry if news event blackout is specifically active on target pair.
           - Global blackout veto eliminated: macro abort triggered only if macro_crash_alert is True
             or macro_score <= -0.45.
        2. Smart 4th Slot Gating:
           - A 4th Long slot is permitted ONLY IF:
             a) Incoming pair is PAXG/USDT:USDT (uncorrelated digital gold macro hedge), OR
             b) At least one open Long is already de-risked in profit (current_profit >= 0.015 / +1.5% ROE).
        """
        sent = self._get_cached_sentiment(pair)

        # Asset-specific blackout isolation
        is_pair_blackout = bool(sent.get("blackout_active", False) or sent.get("is_event_risk_blackout", False))

        # Global macro threat filters
        macro_crash = bool(sent.get("macro_crash_alert", False) or sent.get("is_macro_crash_alert", False))
        macro_sent = sent.get("macro_sentiment", {}) if isinstance(sent.get("macro_sentiment"), dict) else {}
        macro_score = float(macro_sent.get("score", 0.0) if macro_sent else sent.get("macro_score", 0.0) or 0.0)

        global_abort = macro_crash or (macro_score <= -0.45)
        score = float(sent.get("pair_score", sent.get("sentiment_score", 0.0)) or 0.0)
        is_short = str(side).lower() in ("short", "sell")

        if not is_short:
            if is_pair_blackout or global_abort or (score < -0.35):
                logger.warning(
                    f"[MICROSECOND-NEWS-GATING] Aborting LONG entry for {pair}: "
                    f"pair_blackout={is_pair_blackout}, global_abort={global_abort}, "
                    f"macro_crash={macro_crash}, macro_score={macro_score:+.3f}, pair_score={score:+.3f}"
                )
                return False
        else:
            if is_pair_blackout or (score >= 0.40):
                logger.warning(
                    f"[MICROSECOND-NEWS-GATING] Aborting SHORT entry for {pair}: "
                    f"pair_blackout={is_pair_blackout}, pair_score={score:+.3f}"
                )
                return False

        # Smart 4th Slot Directional Cluster Gating
        try:
            open_trades = Trade.get_open_trades() or []
            if not is_short:
                open_long_trades = [t for t in open_trades if not getattr(t, "is_short", False)]
                open_longs_count = len(open_long_trades)

                if open_longs_count >= 4:
                    logger.info(f"[CLUSTER-GATING] Aborting LONG entry for {pair}: already {open_longs_count} open Longs.")
                    return False

                if open_longs_count == 3:
                    is_paxg = "PAXG" in pair
                    if not is_paxg and entry_tag and ("squeeze" in str(entry_tag) or "rs_momentum" in str(entry_tag)):
                        logger.info(f"[CLUSTER-GATING] Aborting 4th LONG breakout entry for {pair} ({entry_tag})")
                        return False

                    has_derisked_trade = False
                    for ot in open_long_trades:
                        try:
                            candle = self._get_candle_info(ot.pair, current_time)
                            ot_close = candle.get("close") if candle else ot.open_rate
                            if ot_close and ot.open_rate and ot.open_rate > 0:
                                ot_profit = ot.calc_profit_ratio(ot_close)
                                if ot_profit >= 0.015:
                                    has_derisked_trade = True
                                    break
                        except Exception as ot_err:
                            logger.debug(f"Error checking profit for open trade {ot.pair}: {ot_err}")

                    if is_paxg or has_derisked_trade:
                        reason = "PAXG digital gold hedge" if is_paxg else "portfolio capital de-risked (profit >= +1.5%)"
                        logger.info(f"[CLUSTER-GATING] Permitting 4th LONG slot for {pair}: {reason}.")
                    else:
                        logger.info(
                            f"[CLUSTER-GATING] Aborting 4th LONG entry for {pair}: "
                            f"all 3 existing Longs underwater/unsecured, blocking correlated flush."
                        )
                        return False
        except Exception as e:
            logger.debug(f"Error in confirm_trade_entry cluster gating: {e}")

        return True

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        """
        Asymmetric Leverage Calibration:
        - All Shorts: 3.0x max leverage (disciplined risk on resistance fades and breakdowns).
        - BTC Longs: 6.0x (widens spot stoploss tolerance from -4.24% spot to -4.95% spot).
        - Altcoin Longs: 3.0x.
        """
        if side == "short":
            return 3.0
        if "BTC" in pair:
            return 6.0
        return 3.0

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        """
        Equal-Weight Allocation across available portfolio slots.
        Removes punitive cascading co-risk haircuts that throttle positive expectancy runners.
        """
        stake = proposed_stake
        if "HYPE" in pair:
            stake = stake * 0.70
        elif "PAXG" in pair:
            stake = stake * 0.85

        if min_stake is not None:
            stake = max(min_stake, stake)
        if max_stake is not None:
            stake = min(max_stake, stake)
        return stake

    def _get_candle_info(self, pair: str, current_time: datetime) -> Dict[str, float] | None:
        """Fast O(1) retrieval of current candle metrics from in-memory cache."""
        pair_cache = self._candle_cache.get(pair) if hasattr(self, "_candle_cache") else None
        if not pair_cache and hasattr(self, "_candle_cache"):
            for k, v in self._candle_cache.items():
                if k == pair or k.split(":")[0] == pair.split(":")[0]:
                    pair_cache = v
                    break

        if pair_cache:
            if current_time in pair_cache:
                return pair_cache[current_time]
            floored_time = current_time.replace(minute=0, second=0, microsecond=0)
            if floored_time in pair_cache:
                return pair_cache[floored_time]
            if floored_time.tzinfo is not None:
                floored_naive = floored_time.replace(tzinfo=None)
                if floored_naive in pair_cache:
                    return pair_cache[floored_naive]
            else:
                floored_utc = floored_time.replace(tzinfo=timezone.utc)
                if floored_utc in pair_cache:
                    return pair_cache[floored_utc]

            prev_hour = floored_time - timedelta(hours=1)
            if prev_hour in pair_cache:
                return pair_cache[prev_hour]
            if prev_hour.tzinfo is not None:
                prev_naive = prev_hour.replace(tzinfo=None)
                if prev_naive in pair_cache:
                    return pair_cache[prev_naive]
            else:
                prev_utc = prev_hour.replace(tzinfo=timezone.utc)
                if prev_utc in pair_cache:
                    return pair_cache[prev_utc]

        if hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) not in ("backtest", "hyperopt"):
                try:
                    analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                    if analyzed_df is not None and not analyzed_df.empty:
                        last_row = analyzed_df.iloc[-1]
                        c = float(last_row.get("close", 0.0))
                        e = last_row.get("ema_21", c)
                        ema_val = float(e) if not np.isnan(e) else c
                        return {
                            "close": c,
                            "atr": float(last_row.get("atr", 0.0)),
                            "rsi": float(last_row.get("rsi", 50.0)),
                            "ema_21": ema_val,
                            "volume": float(last_row.get("volume", 0.0)),
                            "volume_mean_20": float(last_row.get("volume_mean_20", 1.0)),
                            "support_level_20": float(last_row.get("support_level_20", 0.0)),
                        }
                except Exception as e:
                    logger.debug(f"DataProvider candle retrieval exception for {pair}: {e}")
        return None

    def custom_entry_price(
        self,
        pair: str,
        trade: Trade | None = None,
        current_time: datetime | None = None,
        proposed_rate: float = 0.0,
        entry_tag: str | None = None,
        side: str = "long",
        **kwargs,
    ) -> float:
        """
        Calibrated Resting Maker Limit Engine:
        - Long Pullback Alpha: Resting limit at min(proposed_rate, ema_21 * 1.002)
        - Short Resistance Fade: Resting limit at max(proposed_rate, ema_21 * 0.998)
        - Breakout Alphas (Squeeze Volatility Expansion & Decoupled RS Momentum):
          Placed at Close * 0.999 (0.10% discount) to achieve high fill rate
          while qualifying as resting post-only maker order (0.020% Bybit fee rebate).
        - Short Breakdown Alpha:
          Placed at max(proposed_rate, close * 1.001) to ensure resting ask placement
          above market for maker fee rebate (+0.020%) while capturing prompt breakdown fills.
        """
        if isinstance(trade, datetime):
            side = entry_tag if isinstance(entry_tag, str) else side
            entry_tag = proposed_rate if isinstance(proposed_rate, (str, type(None))) else entry_tag
            proposed_rate = float(current_time) if isinstance(current_time, (int, float)) else proposed_rate
            current_time = trade
            trade = None

        if current_time is None:
            current_time = datetime.now(timezone.utc)

        if side is None and trade is not None:
            side = "short" if getattr(trade, "is_short", False) else "long"

        tag = entry_tag or (getattr(trade, "enter_tag", None) if trade else None) or ""
        candle = self._get_candle_info(pair, current_time)
        close_price = candle.get("close", proposed_rate) if candle else proposed_rate

        if proposed_rate is None or proposed_rate <= 0 or np.isnan(proposed_rate):
            proposed_rate = float(close_price)

        ema_21 = candle.get("ema_21") if candle else None
        if ema_21 is None or np.isnan(ema_21) or ema_21 <= 0:
            ema_21 = proposed_rate

        is_short = str(side).lower() in ("short", "sell")
        is_breakout = (
            "squeeze" in tag
            or "rs_momentum" in tag
            or "expansion" in tag
        )
        is_breakdown = "breakdown" in tag

        if is_breakout:
            if not is_short:
                target_limit = min(proposed_rate, close_price * 0.999)
            else:
                target_limit = max(proposed_rate, close_price * 1.001)
        elif is_breakdown:
            target_limit = max(proposed_rate, close_price * 1.001)
        else:
            if not is_short:
                target_limit = min(proposed_rate, ema_21 * 1.002)
            else:
                target_limit = max(proposed_rate, ema_21 * 0.998)

        if target_limit <= 0 or np.isnan(target_limit):
            return proposed_rate if proposed_rate > 0 else float(ema_21)

        return float(target_limit)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        raw_lev = getattr(trade, "leverage", None)
        lev = float(raw_lev) if isinstance(raw_lev, (int, float)) and raw_lev > 0 else (6.0 if "BTC" in pair else 3.0)
        spot_profit = current_profit / lev if lev > 0 else current_profit
        open_rate = trade.open_rate
        is_short = trade.is_short

        candle = self._get_candle_info(pair, current_time)
        current_atr = candle.get("atr", open_rate * 0.02) if candle else (open_rate * 0.02)
        volume = candle.get("volume", 1.0) if candle else 1.0
        volume_mean_20 = candle.get("volume_mean_20", 1.0) if candle else 1.0
        rsi = candle.get("rsi", 50.0) if candle else 50.0
        ema_21 = candle.get("ema_21", current_rate) if candle else current_rate

        # Peak profit tracking
        if not is_short:
            raw_peak = getattr(trade, "max_rate", None)
            peak_rate = float(raw_peak) if isinstance(raw_peak, (int, float)) and raw_peak > 0 else open_rate
            peak_profit = (peak_rate - open_rate) / open_rate * lev if open_rate > 0 else 0.0
        else:
            raw_peak = getattr(trade, "min_rate", None)
            peak_rate = float(raw_peak) if isinstance(raw_peak, (int, float)) and raw_peak > 0 else open_rate
            peak_profit = (open_rate - peak_rate) / open_rate * lev if open_rate > 0 else 0.0

        # ==============================================================
        # 0. DUAL CONCURRENCE PROTOCOL (Fundamental Crisis + Technical Breakdown)
        # ==============================================================
        sent = self._get_cached_sentiment(pair)
        is_pair_blackout = bool(sent.get("blackout_active", False) or sent.get("is_event_risk_blackout", False))
        p_score = float(sent.get("sentiment_score", 0.0) or 0.0)
        macro_sent = sent.get("macro_sentiment", {}) if isinstance(sent.get("macro_sentiment"), dict) else {}
        macro_crash = bool(sent.get("macro_crash_alert", False) or macro_sent.get("is_macro_crash_alert", False))
        macro_score = float(macro_sent.get("score", 0.0) if macro_sent else sent.get("macro_score", 0.0) or 0.0)
        global_threat = macro_crash or (macro_score <= -0.45)

        if not is_short:
            has_fundamental_threat = is_pair_blackout or (p_score < -0.50) or global_threat
            if has_fundamental_threat:
                is_below_ema = (current_rate < ema_21) if (candle and ema_21 != current_rate) else True
                price_breakdown = (
                    (spot_profit <= -0.010 and (is_below_ema or rsi < 45.0))
                    or (spot_profit <= -0.020 and volume > volume_mean_20 * 1.2)
                )
                if price_breakdown:
                    return "news_emergency_invalidation"
        else:
            has_fundamental_threat = is_pair_blackout or (p_score > 0.40) or global_threat
            if has_fundamental_threat:
                is_above_ema = (current_rate > ema_21) if (candle and ema_21 != current_rate) else True
                price_breakout = (
                    (spot_profit <= -0.010 and (is_above_ema or rsi > 55.0))
                    or (spot_profit <= -0.020 and volume > volume_mean_20 * 1.2)
                )
                if price_breakout:
                    return "news_emergency_invalidation"

        # ==============================================================
        # 0B. Breakout / Expansion Alpha Lifecycle (Squeeze & Decoupled RS Momentum)
        # ==============================================================
        entry_tag = getattr(trade, "enter_tag", "") or ""
        if entry_tag and ("squeeze" in entry_tag or "rs_momentum" in entry_tag):
            if current_profit >= 0.040:
                return "breakout_expansion_tp"
            if trade_duration >= 4.0 and spot_profit <= -0.0100:
                return "breakout_rapid_invalidation_4h"
            if trade_duration >= 14.0 and current_profit < -0.0050:
                return "breakout_14h_timeout"

        # ==============================================================
        # 0C. Bearish Breakdown Continuation Lifecycle (Sub-Engine 4B)
        # ==============================================================
        if entry_tag and "breakdown" in entry_tag:
            if current_profit >= 0.035:
                return "short_breakdown_quick_tp"
            if spot_profit <= -0.0180:
                return "short_breakdown_tight_stop"
            if trade_duration >= 8.0 and spot_profit <= -0.0100:
                return "short_breakdown_stale_prune"

        # 1. Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # 2. Surgical Volatility Scalp for PAXG
        if "PAXG" in pair and trade_duration >= 2.0 and current_profit >= 0.0065:
            return "paxg_volatility_tp"

        # 3. Structural Profit Banking for ETH
        if "ETH" in pair and trade_duration >= 16.0 and current_profit >= 0.0080:
            return "eth_profit_bank_tp"

        # 4. Tailored Exits for HYPE (Non-Destructive HYPE Structural Stale Pruner)
        if "HYPE" in pair:
            if current_profit >= 0.045 and trade_duration >= 6.0:
                return "hype_quick_tp"
            if trade_duration >= 36.0 and spot_profit <= -0.060:
                return "hype_stale_prune_36h"
            if trade_duration > 72.0 and current_profit < -0.050:
                return "hype_stale_loss"
            if trade_duration > 96.0 and current_profit < 0.010:
                return "hype_time_cutoff"

        # ==============================================================
        # 5. DYNAMIC CHANDELIER ATR RATCHET (Protects Massive Runners)
        # ==============================================================
        if peak_profit >= 0.150 and trade_duration >= 3.0:
            if not is_short:
                chandelier_stop_price = peak_rate - 2.5 * current_atr
                if current_rate <= chandelier_stop_price and current_profit >= 0.050 and rsi < 62.0:
                    return "chandelier_atr_trailing_exit"
            else:
                chandelier_stop_price = peak_rate + 2.5 * current_atr
                if current_rate >= chandelier_stop_price and current_profit >= 0.050 and rsi > 38.0:
                    return "chandelier_atr_trailing_exit"

        # Holding immunity: if current profit is >= +3.5% ROE, let the winning trade run
        if current_profit >= 0.035:
            return None

        # ==============================================================
        # 6. ADAPTIVE STALE PRUNING ENGINE (12h / 18h / 24h / 36h / 48h)
        # ==============================================================
        if "PAXG" in pair:
            failing_paxg = (rsi < 45.0 or current_rate < ema_21) if not is_short else (rsi > 55.0 or current_rate > ema_21)
            if trade_duration >= 16.0 and spot_profit <= -0.012 and failing_paxg:
                return "paxg_early_stale_prune"
        else:
            if "HYPE" not in pair:
                failing_18h = (rsi < 46.0 or current_rate < ema_21) if not is_short else (rsi > 54.0 or current_rate > ema_21)
                if trade_duration >= 18.0 and spot_profit <= -0.0270 and failing_18h:
                    return "adaptive_stale_prune_18h"
                if trade_duration >= 24.0 and spot_profit <= -0.020:
                    return "adaptive_stale_prune_24h"

        # Mature pair decay cutoffs (applies to BTC, ETH, SOL, ADA, DOGE, LINK, PAXG; HYPE excluded)
        if "HYPE" not in pair:
            if trade_duration > 36.0 and current_profit < -0.010:
                return "time_decay_stale_loss"
            if trade_duration > 48.0 and current_profit < 0.005:
                return "time_decay_48h_cutoff"

        return None

    def check_entry_timeout(self, pair: str, trade: Trade, order: Any,
                            current_time: datetime, **kwargs) -> bool:
        """
        Anti-Stale Limit Order Gate:
        Cancels entry limit orders that remain unfilled for more than 120 minutes (2 candles)
        to prevent catching falling knives after setup invalidation.
        Cancels orders on asset-specific news blackout or global macro crash shock.
        """
        is_short = getattr(trade, "is_short", False)
        sent = self._get_cached_sentiment(pair)

        is_pair_blackout = bool(sent.get("is_event_risk_blackout") or sent.get("blackout_active"))
        macro_crash = bool(sent.get("macro_crash_alert") or sent.get("is_macro_crash_alert"))
        macro_sent = sent.get("macro_sentiment", {}) if isinstance(sent.get("macro_sentiment"), dict) else {}
        macro_score = float(macro_sent.get("score", 0.0) if macro_sent else sent.get("macro_score", 0.0) or 0.0)
        global_abort = macro_crash or (macro_score <= -0.45)
        score = float(sent.get("pair_score", sent.get("sentiment_score", 0.0)) or 0.0)

        if not is_short:
            if is_pair_blackout or global_abort or (score < -0.35):
                return True
        else:
            if is_pair_blackout or (score >= 0.40):
                return True

        if isinstance(order, dict):
            order_date = order.get("order_date_utc") or order.get("order_date")
        else:
            order_date = getattr(order, "order_date_utc", None) or getattr(order, "order_date", None)

        if order_date:
            if isinstance(order_date, str):
                try:
                    order_date = datetime.fromisoformat(order_date.replace("Z", "+00:00"))
                except Exception:
                    try:
                        order_date = datetime.strptime(order_date.split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        order_date = None

        if order_date:
            if order_date.tzinfo is None and current_time.tzinfo is not None:
                order_date = order_date.replace(tzinfo=timezone.utc)
            elif order_date.tzinfo is not None and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            age_minutes = (current_time - order_date).total_seconds() / 60
            if age_minutes > 120:
                return True

        return False

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        informative = [(pair, self.informative_timeframe) for pair in pairs]
        btc_pair = "BTC/USDT:USDT"
        if (btc_pair, "4h") not in informative:
            informative.append((btc_pair, "4h"))
        if (btc_pair, "1h") not in informative:
            informative.append((btc_pair, "1h"))
        return informative

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # Standard macro trend indicators
        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])

        # Adaptive macro bull indicator for early recovery detection
        dataframe["macro_bull_4h_adaptive"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_50"].shift(1))
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair", "")

        # 1. Informative 4H for current pair
        if hasattr(self, "dp") and self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=pair, timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

            # 2. Informative BTC 4H and 1H for macro coupling
            btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="4h")
            if not btc_4h.empty:
                btc_4h["btc_ema_50"] = ta.EMA(btc_4h, timeperiod=50)
                btc_4h["btc_ema_200"] = ta.EMA(btc_4h, timeperiod=200)
                btc_4h["btc_rsi"] = ta.RSI(btc_4h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, btc_4h, self.timeframe, "4h", ffill=True, append_timeframe=False, suffix="btc_4h")

            btc_1h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="1h")
            if not btc_1h.empty:
                btc_1h["btc_ret_24h"] = btc_1h["close"].pct_change(24)
                btc_1h["btc_ret_1h_log"] = np.log(btc_1h["close"] / btc_1h["close"].shift(1))
                btc_1h["btc_ema_50_1h"] = ta.EMA(btc_1h, timeperiod=50)
                btc_1h["btc_rsi_1h"] = ta.RSI(btc_1h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, btc_1h, self.timeframe, "1h", ffill=True, append_timeframe=False, suffix="btc_1h")

        # Fallbacks for columns if not populated
        for col in [
            f"macro_bull_4h_{self.informative_timeframe}",
            f"macro_bear_4h_{self.informative_timeframe}",
            f"macro_bull_4h_adaptive_{self.informative_timeframe}",
        ]:
            if col not in dataframe.columns:
                dataframe[col] = True if "bull" in col else False

        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0

        if "close_btc_4h" not in dataframe.columns:
            dataframe["close_btc_4h"] = dataframe["close"]
            dataframe["btc_ema_50_btc_4h"] = dataframe["close"]
            dataframe["btc_ema_200_btc_4h"] = dataframe["close"]
            dataframe["btc_rsi_btc_4h"] = 50.0

        if "btc_ret_24h_btc_1h" not in dataframe.columns:
            dataframe["btc_ret_24h_btc_1h"] = 0.0

        if "btc_ret_1h_log_btc_1h" not in dataframe.columns:
            dataframe["btc_ret_1h_log_btc_1h"] = 0.0

        if "close_btc_1h" not in dataframe.columns:
            dataframe["close_btc_1h"] = dataframe["close"]

        if "btc_ema_50_1h_btc_1h" not in dataframe.columns:
            dataframe["btc_ema_50_1h_btc_1h"] = dataframe["close"]

        if "btc_rsi_1h_btc_1h" not in dataframe.columns:
            dataframe["btc_rsi_1h_btc_1h"] = 50.0

        # 3. Streamlined 1H Primary Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # 4. Bollinger Bands (20, 2.0 std) & Bandwidth Expansion
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe["bb_middleband"]
        dataframe["bb_width_ema"] = dataframe["bb_width"].ewm(span=20).mean()

        # 5. Keltner Channels (20, 1.5 ATR)
        keltner = qtpylib.keltner_channel(dataframe, window=20, atrs=1.5)
        dataframe["kc_lowerband"] = keltner["lower"]
        dataframe["kc_middleband"] = keltner["mid"]
        dataframe["kc_upperband"] = keltner["upper"]

        # 6. John Carter TTM Squeeze Detection & 3-Bar Follow-Through
        dataframe["in_squeeze"] = (dataframe["bb_lowerband"] > dataframe["kc_lowerband"]) & (dataframe["bb_upperband"] < dataframe["kc_upperband"])
        dataframe["was_in_squeeze"] = dataframe["in_squeeze"].shift(1).rolling(5).max() > 0
        dataframe["squeeze_release"] = dataframe["was_in_squeeze"] & (~dataframe["in_squeeze"])

        dataframe["squeeze_streak_3"] = dataframe["in_squeeze"].shift(1).rolling(3).sum() >= 3
        dataframe["squeeze_fired"] = (~dataframe["in_squeeze"]) & dataframe["squeeze_streak_3"] & dataframe["was_in_squeeze"]

        # Squeeze follow-through expansion across 3 bars post release
        dataframe["squeeze_followthrough_3"] = (
            dataframe["squeeze_fired"]
            | (dataframe["squeeze_fired"].shift(1) & (dataframe["close"] > dataframe["close"].shift(1)))
            | (dataframe["squeeze_fired"].shift(2) & (dataframe["close"] > dataframe["close"].shift(1)))
        )

        # Carter Momentum: Linear regression slope of delta over 20 bars
        donchian_mid = (dataframe["high"].rolling(20).max() + dataframe["low"].rolling(20).min()) / 2.0
        sma_20 = dataframe["close"].rolling(20).mean()
        delta = dataframe["close"] - (donchian_mid + sma_20) / 2.0
        slope = talib.LINEARREG_SLOPE(delta.fillna(0.0).values, timeperiod=20)
        dataframe["squeeze_momentum"] = pd.Series(slope, index=dataframe.index).fillna(0.0)
        dataframe["mom_expanding_up"] = (dataframe["squeeze_momentum"] > 0) & (dataframe["squeeze_momentum"] > dataframe["squeeze_momentum"].shift(1))
        dataframe["mom_expanding_down"] = (dataframe["squeeze_momentum"] < 0) & (dataframe["squeeze_momentum"] < dataframe["squeeze_momentum"].shift(1))

        # 7. Cross-Sectional Decoupled RS Momentum (Alts relative to BTC beta drift)
        dataframe["ret_1h_log"] = np.log(dataframe["close"] / dataframe["close"].shift(1)).fillna(0.0)
        dataframe["ret_24h"] = dataframe["close"].pct_change(24).fillna(0.0)

        if "BTC" not in pair and "btc_ret_1h_log_btc_1h" in dataframe.columns:
            btc_ret_1h = dataframe["btc_ret_1h_log_btc_1h"].fillna(0.0)
            cov = dataframe["ret_1h_log"].ewm(span=36).cov(btc_ret_1h)
            var_btc = btc_ret_1h.ewm(span=36).var()
            dataframe["beta_btc"] = (cov / (var_btc + 1e-9)).clip(lower=0.20, upper=2.50).fillna(1.0)
            dataframe["delta_rs"] = dataframe["ret_24h"] - dataframe["beta_btc"] * dataframe["btc_ret_24h_btc_1h"]
            dataframe["delta_rs_ema"] = dataframe["delta_rs"].ewm(span=12).mean()
        else:
            dataframe["beta_btc"] = 1.0
            dataframe["delta_rs"] = 0.0
            dataframe["delta_rs_ema"] = 0.0

        # 8. Stochastics & Regime Windows
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Stochastic momentum regime windows
        dataframe["stoch_regime_long"] = (
            ((dataframe["slowk"] < 45.0) & (dataframe["slowk"] > dataframe["slowd"]) & (dataframe["slowk"] > dataframe["slowk"].shift(1)))
            | qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        )

        dataframe["stoch_regime_short"] = (
            ((dataframe["slowk"] > 55.0) & (dataframe["slowk"] < dataframe["slowd"]) & (dataframe["slowk"] < dataframe["slowk"].shift(1)))
            | qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])
        )

        # 9. Volume Baseline & Candle Morphology
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
        dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)

        # 10. Sub-Engine 4B Supporting Indicators: Support Level 20 & Taker Sell Volume Surge
        dataframe["support_level_20"] = dataframe["low"].rolling(window=20).min()
        dataframe["taker_sell_volume_surge"] = (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.15)

        # Cache indicators in fast lookup dict for custom_exit, custom_entry_price, and smart gating
        dates = pd.to_datetime(dataframe["date"]).dt.to_pydatetime()
        records = [
            {
                "close": float(c) if not np.isnan(c) else 0.0,
                "atr": float(a) if not np.isnan(a) else 0.0,
                "rsi": float(r) if not np.isnan(r) else 50.0,
                "ema_21": float(e) if not np.isnan(e) else float(c),
                "volume": float(v) if not np.isnan(v) else 0.0,
                "volume_mean_20": float(vm) if not np.isnan(vm) else 1.0,
                "support_level_20": float(s) if not np.isnan(s) else 0.0,
            }
            for a, r, e, c, v, vm, s in zip(
                dataframe["atr"], dataframe["rsi"], dataframe["ema_21"],
                dataframe["close"], dataframe["volume"], dataframe["volume_mean_20"],
                dataframe["support_level_20"]
            )
        ]
        self._candle_cache[pair] = dict(zip(dates, records))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        pair = metadata.get("pair", "")

        # -------------------------------------------------------------
        # Long Pullback Calibration (Pillar 2 & 4 Adaptive Upgrade)
        # -------------------------------------------------------------
        if "BTC" in pair:
            adx_long_gate = 22.0
        elif "ETH" in pair or "SOL" in pair:
            adx_long_gate = 21.0
        else:
            adx_long_gate = 20.0

        rsi_min_long = 44.0 if "SOL" in pair else 43.0
        pullback_threshold = 1.0100

        # ENGINE 1: Primary Trend Pullback Alpha (Preserves V24 Proven Champion Core)
        long_pullback = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > adx_long_gate) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * pullback_threshold) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= rsi_min_long) &
            (dataframe["rsi"] <= 63.0) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 54.0) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        if "LINK" in pair:
            btc_macro_bull = (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) & (dataframe["btc_rsi_btc_4h"] > 48.0)
            long_pullback = long_pullback & btc_macro_bull
        # -------------------------------------------------------------
        # ENGINE 2: Carter TTM Volatility Squeeze Expansion Alpha (3-Bar Follow-Through)
        # Excludes SOL & ADA; incorporates squeeze follow-through and dynamic ADX modulation
        # -------------------------------------------------------------
        squeeze_compression_long = dataframe["in_squeeze"].shift(1).rolling(6).sum() >= 5
        adx_squeeze_gate = np.where(squeeze_compression_long, 16.0, 20.0)

        long_squeeze = (
            ("SOL" not in pair) &
            ("ADA" not in pair) &
            (dataframe["squeeze_followthrough_3"] == True) &
            (dataframe["bb_width"] >= dataframe["bb_width_ema"] * 0.96) &
            (dataframe["mom_expanding_up"] == True) &
            (dataframe["close"] > dataframe["ema_21"]) &
            (dataframe["close"] < dataframe["bb_upperband"] * 1.005) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.995) &
            (dataframe["rsi"] >= 50.0) &
            (dataframe["rsi"] <= 68.0) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.20) &
            (
                (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) |
                (dataframe[f"macro_bull_4h_adaptive_{self.informative_timeframe}"] == True)
            ) &
            (dataframe[f"adx_{self.informative_timeframe}"] >= adx_squeeze_gate) &
            (dataframe["is_green"] == True)
        )

        # -------------------------------------------------------------
        # ENGINE 3: Cross-Sectional Decoupled RS Momentum Alpha
        # Excludes BTC, HYPE, ADA, and ETH (Toxic Breakout Pruning)
        # -------------------------------------------------------------
        if "BTC" not in pair and "HYPE" not in pair and "ADA" not in pair and "ETH" not in pair:
            min_delta_rs = 0.034 if "DOGE" in pair else 0.032
            min_vol_mult = 1.08 if "DOGE" in pair else 1.05

            long_rs = (
                (dataframe["delta_rs"] > min_delta_rs) &
                (dataframe["delta_rs"] > dataframe["delta_rs_ema"]) &
                (dataframe["close"] > dataframe["ema_21"]) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.995) &
                (dataframe["rsi"] >= 48.0) &
                (dataframe["rsi"] <= 68.0) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * min_vol_mult) &
                (dataframe["close"] > dataframe["ema_200"]) &
                (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
                (dataframe[f"adx_{self.informative_timeframe}"] >= 21.0) &
                (dataframe["is_green"] == True)
            )
        else:
            long_rs = pd.Series(False, index=dataframe.index)

        # -------------------------------------------------------------
        # ENGINE 4A: Tailored Short Resistance Exhaustion Fade Alpha (Preserves V24 Proven 88.6% WR Core)
        # -------------------------------------------------------------
        short_fade = pd.Series(False, index=dataframe.index)
        if "BTC" not in pair and "HYPE" not in pair:
            adx_short_gate = 25.0 if "SOL" in pair else 22.0
            rsi_min_short = 56.0 if "SOL" in pair else 53.0

            short_fade_base = (
                (dataframe[f"adx_{self.informative_timeframe}"] > adx_short_gate) &
                (
                    (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                    (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                ) &
                (dataframe["close"] <= dataframe["ema_50"] + 0.30 * dataframe["atr"]) &
                (dataframe["rsi"] >= rsi_min_short) &
                (dataframe["rsi"] <= 67.0) &
                (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
                (dataframe["slowk"] > 55.0) &
                (
                    (dataframe["is_red"] == True) |
                    (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
            )

            if "LINK" in pair:
                link_short_regime = (
                    (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) |
                    ((dataframe["close_btc_4h"] < dataframe["btc_ema_50_btc_4h"]) & (dataframe["close"] < dataframe["ema_50"]))
                )
                short_fade = short_fade_base & link_short_regime & (dataframe["close"] < dataframe["ema_200"])
            else:
                short_fade = short_fade_base & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (dataframe["close"] < dataframe["ema_200"])

        # -------------------------------------------------------------
        # ENGINE 4B: Bearish Trend Continuation Breakdown Alpha
        # Excludes BTC (false breakdown prone) and PAXG (gold hedge)
        # Captures high-volume momentum breakdowns on confirmed macro downtrends
        # -------------------------------------------------------------
        short_breakdown = pd.Series(False, index=dataframe.index)
        if "BTC" not in pair and "PAXG" not in pair:
            short_breakdown = (
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                (dataframe[f"adx_{self.informative_timeframe}"] >= 21.0) &
                (dataframe["close"] < dataframe["ema_200"]) &
                (dataframe["close"] < dataframe["ema_21"] * 0.985) &
                (dataframe["close"] < dataframe["ema_50"]) &
                (dataframe["rsi"] >= 32.0) &
                (dataframe["rsi"] <= 48.0) &
                (dataframe["slowk"] < dataframe["slowd"]) &
                (dataframe["taker_sell_volume_surge"] == True) &
                (dataframe["close"] < dataframe["support_level_20"].shift(1) * 0.999)
            )

        # Prioritized Tag Attribution
        dataframe.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[long_squeeze & ~long_pullback, ["enter_long", "enter_tag"]] = (1, "long_squeeze_expansion")
        dataframe.loc[long_rs & ~long_pullback & ~long_squeeze, ["enter_long", "enter_tag"]] = (1, "long_rs_momentum")
        dataframe.loc[short_fade & ~long_pullback & ~long_squeeze & ~long_rs, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")
        dataframe.loc[short_breakdown & ~long_pullback & ~long_squeeze & ~long_rs & ~short_fade, ["enter_short", "enter_tag"]] = (1, "short_breakdown_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        long_exit = (
            (dataframe["rsi"] >= 71) &
            (dataframe["close"] >= dataframe["bb_upperband"] * 0.998)
        )
        short_exit = (
            (dataframe["rsi"] <= 28) &
            (dataframe["close"] <= dataframe["bb_lowerband"] * 1.002)
        )

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")
        return dataframe
