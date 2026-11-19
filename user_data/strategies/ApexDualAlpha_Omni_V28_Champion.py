"""
ApexDualAlpha_Omni_V28_Champion

Institutional Multi-Alpha Futures Strategy — Strictly Causal T-1 Zero-Lookahead Architecture
============================================================================================
1. Strict T-1 Closed-Candle Causality & Zero-Exploit Execution:
   - _get_candle_info: O(1) lookup querying exclusively prev_hour = floor(T) - 1h.
   - _get_causal_peak_profit: Reconstructs post-entry MFE strictly over closed bars [open_hour..T-1] + open(T).
   - custom_entry_price: Clamps all resting maker limits within [0.9885 * proposed_rate, 1.0095 * proposed_rate].
   - _merge_causal_funding: Backward-merges 1h funding rates at settlement + 1h <= candle_date (0.0001 live fallback).
   - trailing_stop = False, minimal_roi["0"] = 2.500, and custom_exit duration <= 0 guard -> 0 dur==0 trades.
   - Institutional Leverage Cap: 3.0x on all Shorts and Altcoin Longs, 4.0x on BTC Longs.

2. Orthogonal 10-Tag Signal Stack:
   - Perpetual Funding Contango/Backwardation Regimes, Symmetric BTC 1H Velocity & 6H Waterfall Shields,
     4H EMA50 Rollover/Recovery Shields, and BTC-aligned Adaptive Structural Transitions.
   - Clamp-Safe Value Pullbacks (long_pullback_alpha), ADA Lower-BB Reversals (long_bb_pullback),
     and High-Purity Non-Overlapping EMA9 Trend Pullbacks (long_ema9_pullback).
   - TTM Volatility Squeeze Expansions (long_squeeze_expansion, short_squeeze_expansion) and
     Beta-Adjusted Cross-Sectional Relative-Strength Momentum (long_rs_momentum).
   - Bearish Resistance Exhaustion (short_atr_fade_alpha), All-Pair Crowded-Mania Funding (>= +0.022%)
     Upper-BB Exhaustion (short_crowded_fade_alpha), Bear Upper-BB Fade (short_bb_fade), and
     Uncrowded/Throwback Support Breakdown Continuation (short_breakdown_alpha).

3. Portfolio Heat, Zero-Dust Sizing & Pure Fixed-Stake Honesty:
   - 5-Slot Core-6 Reservation, De-Risked Short Squeeze Cluster Gate, and Concurrent Breakdown Cap.
   - Capital-Velocity Fractional-Kelly Sizing with Causal BTC-4H Trend-Strength Alt-LP Sub-Cluster Damper,
     T-1 Tail-Volatility Dampening, and Hard >= 0.35x Slot Floor (zero micro-dust fillers).
   - Pure Non-Compounding Fixed-Stake Mode: scales strictly around proposed_stake with zero wallet-growth ramp.
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

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
from freqtrade.enums import CandleType

logger = logging.getLogger(__name__)


class ApexDualAlpha_Omni_V28_Champion(IStrategy):
    # Tunable core parameters for +-15% sensitivity & Leave-One-Out ablation audits
    KNOB_ADX_GATE_SCALE: float = 1.0
    KNOB_BREAKOUT_TP_ROE: float = 0.0420
    KNOB_PULLBACK_STALL_ROE: float = 0.0330
    KNOB_STALE_PRUNE_SCALE: float = 1.0
    KNOB_KELLY_SCALE: float = 1.0

    ABLATE_FUNDING_REGIME: bool = False
    ABLATE_UPPER_BB_FADES: bool = False
    ABLATE_SQUEEZE_AND_RS: bool = False
    ABLATE_REGIME_RISK_SHIELDS: bool = False
    ABLATE_KELLY_SIZING: bool = False
    INTERFACE_VERSION = 3
    max_open_trades = 5
    CORE_6 = ("HYPE", "SOL", "ADA", "DOGE", "LINK", "ETH")

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    minimal_roi = {
        "0": 2.500,
        "60": 0.526,
        "180": 0.180,
        "467": 0.155,
        "688": 0.092,
        "1173": 0.010,
    }

    stoploss = -0.297

    trailing_stop = False
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

    _candle_cache: Dict[str, Dict[datetime, Dict[str, Any]]] = {}
    _candle_arrays: Dict[str, Dict[str, np.ndarray]] = {}

    def __init__(self, config: dict) -> None:
        if isinstance(config, dict) and config.get("max_open_trades", 4) < self.max_open_trades:
            config["max_open_trades"] = self.max_open_trades
        super().__init__(config)
        self._candle_cache = {}
        self._candle_arrays = {}

    _sentiment_cache: Dict[str, Any] = {}
    _sentiment_cache_time: float = 0.0
    _sentiment_lock = threading.Lock()

    def _get_cached_sentiment(self, pair: str | None = None) -> Dict[str, Any]:
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
                        self._sentiment_cache = safe_neutral if is_stale else loaded_data
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

    def _is_core6(self, pair: str) -> bool:
        return any(sym in pair for sym in self.CORE_6)

    def _resolve_pair_cache(self, pair: str) -> Dict[datetime, Dict[str, Any]] | None:
        if not hasattr(self, "_candle_cache"):
            return None
        pair_cache = self._candle_cache.get(pair)
        if not pair_cache:
            for k, v in self._candle_cache.items():
                if k == pair or k.split(":")[0] == pair.split(":")[0]:
                    pair_cache = v
                    break
        return pair_cache

    @staticmethod
    def _lookup_dt(pair_cache: Dict[datetime, Dict[str, Any]], target_dt: datetime) -> Dict[str, Any] | None:
        if target_dt in pair_cache:
            return pair_cache[target_dt]
        if target_dt.tzinfo is not None:
            naive_dt = target_dt.replace(tzinfo=None)
            if naive_dt in pair_cache:
                return pair_cache[naive_dt]
        else:
            utc_dt = target_dt.replace(tzinfo=timezone.utc)
            if utc_dt in pair_cache:
                return pair_cache[utc_dt]
        return None

    def _get_candle_info(self, pair: str, current_time: datetime) -> Dict[str, Any] | None:
        """Strictly causal T-1 closed-candle O(1) lookup (queries ONLY prev_hour)."""
        pair_cache = self._resolve_pair_cache(pair)
        if pair_cache:
            prev_hour = current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            rec = self._lookup_dt(pair_cache, prev_hour)
            if rec is not None:
                return rec

        if hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) not in ("backtest", "hyperopt"):
                try:
                    analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                    if analyzed_df is not None and not analyzed_df.empty:
                        last_row = analyzed_df.iloc[-1]
                        c = float(last_row.get("close", 0.0))
                        e9 = last_row.get("ema_9", c)
                        e21 = last_row.get("ema_21", c)
                        e50 = last_row.get("ema_50", c)
                        e200 = last_row.get("ema_200", c)
                        mbull_col = f"macro_bull_4h_{self.informative_timeframe}"
                        mbear_col = f"macro_bear_4h_{self.informative_timeframe}"
                        return {
                            "idx": -1,
                            "close": c,
                            "high": float(last_row.get("high", c)),
                            "low": float(last_row.get("low", c)),
                            "atr": float(last_row.get("atr", 0.0)),
                            "atr_pct": float(last_row.get("atr_pct", 0.0)),
                            "atr_pct_med168": float(last_row.get("atr_pct_med168", 0.0)),
                            "rsi": float(last_row.get("rsi", 50.0)),
                            "slowk": float(last_row.get("slowk", 50.0)),
                            "slowd": float(last_row.get("slowd", 50.0)),
                            "ema_9": float(e9) if not np.isnan(e9) else c,
                            "ema_21": float(e21) if not np.isnan(e21) else c,
                            "ema_50": float(e50) if not np.isnan(e50) else c,
                            "ema_200": float(e200) if not np.isnan(e200) else c,
                            "adx_4h": float(last_row.get(f"adx_{self.informative_timeframe}", 25.0)),
                            "macro_bull_4h": bool(last_row.get(mbull_col, False)),
                            "macro_bear_4h": bool(last_row.get(mbear_col, False)),
                            "btc_strong_bull_4h": bool(last_row.get("btc_strong_bull_btc_4h", True)),
                            "funding_rate": float(last_row.get("funding_rate", 0.0001)),
                            "funding_mean_24h": float(last_row.get("funding_mean_24h", 0.0001)),
                            "volume": float(last_row.get("volume", 0.0)),
                            "volume_mean_20": float(last_row.get("volume_mean_20", 1.0)),
                            "support_level_20": float(last_row.get("support_level_20", 0.0)),
                        }
                except Exception as e:
                    logger.debug(f"DataProvider candle retrieval exception for {pair}: {e}")
        return None

    def _get_causal_peak_profit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        lev: float,
        current_rate: float,
        prev_candle: Dict[str, Any] | None,
    ) -> tuple[float, float]:
        """Reconstruct post-entry MFE strictly from closed candles [open_hour..T-1] + open(T)."""
        open_rate = float(trade.open_rate or 0.0)
        if open_rate <= 0.0:
            return 0.0, 0.0
        is_short = bool(trade.is_short)
        best_rate = open_rate
        if current_rate and current_rate > 0.0:
            best_rate = min(best_rate, current_rate) if is_short else max(best_rate, current_rate)

        pair_cache = self._resolve_pair_cache(pair)
        arrays = self._candle_arrays.get(pair) if hasattr(self, "_candle_arrays") else None
        if pair_cache and arrays and prev_candle is not None and int(prev_candle.get("idx", -1)) >= 0 and trade.open_date_utc:
            prev_hour = current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            open_dt = trade.open_date_utc
            if open_dt.tzinfo is None and prev_hour.tzinfo is not None:
                open_dt = open_dt.replace(tzinfo=timezone.utc)
            elif open_dt.tzinfo is not None and prev_hour.tzinfo is None:
                prev_hour = prev_hour.replace(tzinfo=timezone.utc)
            open_floored = open_dt.replace(minute=0, second=0, microsecond=0)
            if open_floored <= prev_hour:
                open_rec = self._lookup_dt(pair_cache, open_floored)
                i_prev = int(prev_candle["idx"])
                if open_rec is not None and int(open_rec.get("idx", -1)) >= 0:
                    i_open = int(open_rec["idx"])
                    if i_prev >= i_open:
                        c_open = float(arrays["close"][i_open])
                        if c_open > 0.0:
                            best_rate = min(best_rate, c_open) if is_short else max(best_rate, c_open)
                        if i_prev > i_open:
                            if is_short:
                                span_min = float(np.min(arrays["low"][i_open + 1 : i_prev + 1]))
                                if span_min > 0.0:
                                    best_rate = min(best_rate, span_min)
                            else:
                                span_max = float(np.max(arrays["high"][i_open + 1 : i_prev + 1]))
                                best_rate = max(best_rate, span_max)
        elif hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) not in ("backtest", "hyperopt"):
                raw_peak = getattr(trade, "min_rate" if is_short else "max_rate", None)
                if isinstance(raw_peak, (int, float)) and raw_peak > 0.0:
                    best_rate = min(best_rate, float(raw_peak)) if is_short else max(best_rate, float(raw_peak))

        if not is_short:
            peak_profit = (best_rate - open_rate) / open_rate * lev
        else:
            peak_profit = (open_rate - best_rate) / open_rate * lev
        return best_rate, max(0.0, peak_profit)

    def _eval_open_cluster_state(self, open_trades: list, current_time: datetime) -> tuple[int, bool]:
        derisked_count = 0
        has_low_beta = False
        for ot in open_trades:
            ot_pair = getattr(ot, "pair", "") or ""
            if "PAXG" in ot_pair or "BTC" in ot_pair:
                has_low_beta = True
            try:
                candle = self._get_candle_info(ot_pair, current_time)
                if candle:
                    ot_close = candle.get("close", 0.0)
                    if ot_close and ot_close > 0.0 and ot.open_rate and ot.open_rate > 0.0:
                        if ot.calc_profit_ratio(ot_close) >= 0.010:
                            derisked_count += 1
            except Exception as ot_err:
                logger.debug(f"Error checking T-1 profit for open trade {ot_pair}: {ot_err}")
        return derisked_count, has_low_beta

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        sent = self._get_cached_sentiment(pair)
        is_pair_blackout = bool(sent.get("blackout_active", False) or sent.get("is_event_risk_blackout", False))
        macro_crash = bool(sent.get("macro_crash_alert", False) or sent.get("is_macro_crash_alert", False))
        macro_sent = sent.get("macro_sentiment", {}) if isinstance(sent.get("macro_sentiment"), dict) else {}
        macro_score = float(macro_sent.get("score", 0.0) if macro_sent else sent.get("macro_score", 0.0) or 0.0)

        global_abort = macro_crash or (macro_score <= -0.45)
        score = float(sent.get("pair_score", sent.get("sentiment_score", 0.0)) or 0.0)
        is_short = str(side).lower() in ("short", "sell")

        if not is_short:
            if is_pair_blackout or global_abort or (score < -0.35):
                return False
        else:
            if global_abort or (score >= 0.40):
                return False

        try:
            open_trades = Trade.get_open_trades() or []
            total_open = len(open_trades)
            is_core6 = self._is_core6(pair)
            is_paxg = "PAXG" in pair
            tag_str = str(entry_tag or "")
            derisked_count, has_low_beta = self._eval_open_cluster_state(open_trades, current_time)

            # M6 5th-slot Core-6 reservation (except 91%-WR EMA9 shallow BTC/PAXG when de-risked)
            if total_open >= 4 and not is_core6:
                if not ("ema9_pullback" in tag_str and derisked_count >= 1):
                    return False

            # Cap concurrent open short_breakdown_alpha trades at <= 2
            if is_short and "breakdown" in tag_str:
                open_breakdowns = sum(
                    1 for t in open_trades if "breakdown" in str(getattr(t, "enter_tag", "") or "")
                )
                if open_breakdowns >= 2:
                    return False

            same_side_count = sum(1 for t in open_trades if bool(getattr(t, "is_short", False)) == is_short)

            if is_short:
                if self.ABLATE_REGIME_RISK_SHIELDS:
                    if same_side_count >= 4:
                        return bool(is_core6 and "fade" in tag_str and (has_low_beta or derisked_count >= 1))
                    if same_side_count == 3:
                        return bool(is_paxg or is_core6)
                else:
                    same_shorts = [t for t in open_trades if bool(getattr(t, "is_short", False))]
                    derisked_shorts, _ = self._eval_open_cluster_state(same_shorts, current_time)
                    if same_side_count >= 4:
                        return False
                    if same_side_count == 3:
                        return bool((is_paxg or ("fade" in tag_str and is_core6)) and derisked_shorts >= 1)
                    if same_side_count == 2 and not is_paxg:
                        return bool(derisked_shorts >= 1 or has_low_beta)
            else:
                if same_side_count >= 5:
                    return False
                if same_side_count == 4:
                    return bool(is_core6 and ((has_low_beta and derisked_count >= 1) or derisked_count >= 2))
        except Exception as e:
            logger.debug(f"Error in confirm_trade_entry cluster gating: {e}")

        return True

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        if str(side).lower() in ("short", "sell"):
            return 3.0
        if "BTC" in pair:
            return 4.0
        return 3.0

    @staticmethod
    def _is_non_hype_alt_lp(t_pair: str, t_tag: str, t_short: bool) -> bool:
        return (
            (not t_short)
            and (t_tag == "long_pullback_alpha")
            and all(sym not in t_pair for sym in ("HYPE", "BTC", "PAXG"))
        )

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        tag = str(entry_tag or "")
        is_short = str(side).lower() in ("short", "sell")
        is_core6 = self._is_core6(pair)
        is_unlimited = isinstance(self.config, dict) and self.config.get("stake_amount") == "unlimited"
        n_slots = float(self.max_open_trades) if (isinstance(self.max_open_trades, (int, float)) and 0 < self.max_open_trades < 100) else 5.0
        is_alt_lp = self._is_non_hype_alt_lp(pair, tag, is_short)

        candle = self._get_candle_info(pair, current_time)
        fr_raw = float(candle.get("funding_rate", 0.0001)) if candle else 0.0001
        adx_4h = float(candle.get("adx_4h", 25.0)) if candle else 25.0
        btc_strong_bull_4h = True if self.ABLATE_REGIME_RISK_SHIELDS else (bool(candle.get("btc_strong_bull_4h", True)) if candle else True)

        if is_unlimited and self.ABLATE_KELLY_SIZING:
            open_tr_eq = Trade.get_open_trades() or []
            tied_eq = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in open_tr_eq)
            tot_eq = max(proposed_stake * n_slots, tied_eq + proposed_stake)
            slot_eq = tot_eq / n_slots
            avail_eq = float(max_stake) if (max_stake is not None and max_stake > 0.0) else slot_eq
            if avail_eq < 0.36 * slot_eq:
                return 0.0
            return float(min(slot_eq, avail_eq))

        # 1. Pair capital-velocity Kelly weights (every pair >= 0.36x institutional slot floor; zero dust)
        if "HYPE" in pair:
            pair_mult = 1.58
        elif "BTC" in pair:
            pair_mult = 0.28
        elif "PAXG" in pair:
            pair_mult = 0.26
        elif "LINK" in pair or "DOGE" in pair:
            pair_mult = 1.30
        elif "ETH" in pair or "SOL" in pair:
            pair_mult = 1.22
        else:
            pair_mult = 0.94

        # 2. Engine Fractional-Kelly Conviction Weights (calibrated to verified H1+H2 %PF)
        if tag == "short_crowded_fade_alpha":
            engine_mult = 1.36
        elif "bb_fade" in tag:
            engine_mult = 0.88 if "ETH" in pair else 0.98
        elif "fade" in tag:
            if "ETH" in pair:
                engine_mult = 0.65
            elif "DOGE" in pair or "LINK" in pair:
                engine_mult = 1.42
            elif fr_raw >= 0.00004:
                engine_mult = 1.38
            else:
                engine_mult = 1.22
        elif "rs_momentum" in tag:
            engine_mult = 1.38
        elif "long_squeeze" in tag:
            engine_mult = 1.40
        elif "short_squeeze" in tag:
            engine_mult = 1.26
        elif "breakdown" in tag:
            engine_mult = 1.00 if fr_raw <= 0.00010 else 0.90
        elif "bb_pullback" in tag:
            engine_mult = 1.10
        elif "pullback" in tag:
            if "HYPE" in pair:
                engine_mult = 1.40
            elif "ema9_pullback" in tag and "SOL" in pair:
                engine_mult = 1.32
            elif is_alt_lp and not btc_strong_bull_4h:
                engine_mult = 0.70 if ("LINK" in pair or "SOL" in pair) else 0.84
            elif 0.00003 <= fr_raw <= 0.00050 and adx_4h >= 22.0:
                engine_mult = 1.25
            else:
                engine_mult = 1.14
        else:
            engine_mult = 1.00

        # 3. Mild causal T-1 tail-volatility dampener
        vol_mult = 1.00
        if candle and is_core6:
            atr_pct_t1 = float(candle.get("atr_pct", 0.0) or 0.0)
            atr_med_t1 = float(candle.get("atr_pct_med168", 0.0) or 0.0)
            if atr_pct_t1 > 1e-6 and atr_med_t1 > 1e-6:
                vol_mult = float(np.clip((atr_med_t1 / atr_pct_t1) ** 0.75, 0.80, 1.16))

        kelly_knob = float(self.KNOB_KELLY_SCALE)
        cap_knob = float(np.clip(kelly_knob, 0.85, 1.10))

        try:
            open_trades = Trade.get_open_trades() or []
            same_side_trades = [t for t in open_trades if bool(getattr(t, "is_short", False)) == is_short]
            same_side_open = len(same_side_trades)
            tied_up = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in open_trades)
            same_side_tied = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in same_side_trades)
            alt_lp_tied = sum(
                float(getattr(t, "stake_amount", 0.0) or 0.0)
                for t in same_side_trades
                if self._is_non_hype_alt_lp(
                    str(getattr(t, "pair", "") or ""),
                    str(getattr(t, "enter_tag", "") or ""),
                    bool(getattr(t, "is_short", False)),
                )
            )

            if is_unlimited:
                true_total_tradable = max(proposed_stake * n_slots, tied_up + proposed_stake)
                slot_1x = true_total_tradable / n_slots
                min_inst_stake = 0.355 * slot_1x

                if is_short:
                    co_table_s = (1.04, 0.80, 0.56, 0.42, 0.36)
                    co_mult = co_table_s[min(same_side_open, 4)]
                    slot_base = true_total_tradable / 3.20
                    raw_mult = float(np.clip(pair_mult * engine_mult * vol_mult * co_mult * kelly_knob, 0.24, 1.68))
                    stake = slot_base * raw_mult
                    if not is_core6:
                        single_cap = (0.088 if "BTC" in pair else 0.073) * true_total_tradable
                    elif tag == "short_crowded_fade_alpha":
                        single_cap = 0.32 * cap_knob * true_total_tradable
                    elif "bb_fade" in tag:
                        single_cap = 0.26 * cap_knob * true_total_tradable
                    elif "fade" in tag and "ETH" not in pair:
                        single_cap = 0.35 * cap_knob * true_total_tradable
                    elif "short_squeeze" in tag:
                        single_cap = 0.29 * cap_knob * true_total_tradable
                    else:
                        single_cap = 0.22 * cap_knob * true_total_tradable
                    side_cap_ratio = 0.67
                else:
                    co_table_l = (1.12, 0.92, 0.71, 0.50, 0.39)
                    co_mult = co_table_l[min(same_side_open, 4)]
                    if is_alt_lp and not btc_strong_bull_4h and alt_lp_tied > 0.12 * true_total_tradable:
                        co_mult *= 0.62
                    slot_base = true_total_tradable / 2.82
                    raw_mult = float(np.clip(pair_mult * engine_mult * vol_mult * co_mult * kelly_knob, 0.22, 1.82))
                    stake = slot_base * raw_mult
                    if not is_core6:
                        single_cap = (0.085 if "BTC" in pair else 0.073) * true_total_tradable
                    elif "HYPE" in pair:
                        single_cap = 0.40 * cap_knob * true_total_tradable
                    elif "bb_pullback" in tag:
                        single_cap = 0.16 * cap_knob * true_total_tradable
                    elif "rs_momentum" in tag or "long_squeeze" in tag:
                        single_cap = 0.40 * cap_knob * true_total_tradable
                    elif is_alt_lp and not btc_strong_bull_4h:
                        single_cap = 0.22 * cap_knob * true_total_tradable
                    elif "ADA" in pair:
                        single_cap = 0.24 * cap_knob * true_total_tradable
                    elif "SOL" in pair and is_alt_lp:
                        single_cap = 0.345 * cap_knob * true_total_tradable
                    else:
                        single_cap = 0.37 * cap_knob * true_total_tradable
                    side_cap_ratio = 0.88

                stake = max(min_inst_stake, min(stake, single_cap))
                total_heat_budget = 0.98 * true_total_tradable - tied_up
                side_heat_budget = side_cap_ratio * true_total_tradable - same_side_tied
                avail_cap = float(max_stake) if (max_stake is not None and max_stake > 0.0) else stake
                eff_budget = min(total_heat_budget, side_heat_budget, avail_cap)
                if is_alt_lp and not btc_strong_bull_4h:
                    eff_budget = min(eff_budget, 0.38 * true_total_tradable - alt_lp_tied)
                if eff_budget < min_inst_stake:
                    return 0.0
                stake = min(stake, eff_budget)
            else:
                # Pure non-compounding fixed-stake mode: strictly scaled around proposed_stake (ZERO wallet ramp!)
                min_fixed_stake = 0.36 * proposed_stake
                co_fixed = (1.00, 0.74, 0.52, 0.40)[min(same_side_open, 3)]
                if "BTC" in pair:
                    base_f = 0.50
                elif "PAXG" in pair:
                    base_f = 0.36
                elif "HYPE" in pair:
                    base_f = 1.82
                elif tag in ("long_squeeze_expansion", "short_squeeze_expansion", "short_crowded_fade_alpha"):
                    base_f = 1.44
                elif tag == "long_rs_momentum":
                    base_f = 1.32
                elif tag == "short_bb_fade":
                    base_f = 0.85 if "ETH" in pair else 1.24
                elif tag == "long_bb_pullback":
                    base_f = 0.52
                elif "fade" in tag and is_short:
                    base_f = 0.50 if ("ETH" in pair or "ADA" in pair) else 1.22
                elif is_short and "breakdown" in tag:
                    base_f = 0.96 if "DOGE" in pair else (0.48 if "ADA" in pair else 0.72)
                elif is_alt_lp:
                    if "LINK" in pair or "SOL" in pair:
                        base_f = 0.48
                    elif "ADA" in pair:
                        base_f = 0.72
                    elif "ETH" in pair:
                        base_f = 0.84
                    else:
                        base_f = 0.96
                else:
                    base_f = 1.00

                scale_fixed = 1.25 * kelly_knob
                single_fixed_cap = (1.36 if is_short else 2.28) * proposed_stake
                target_fixed = min(single_fixed_cap, proposed_stake * max(0.36, base_f * scale_fixed * co_fixed))
                total_fixed_budget = 3.75 * proposed_stake - tied_up
                side_fixed_budget = (1.78 if is_short else 3.05) * proposed_stake - same_side_tied
                avail_cap = float(max_stake) if (max_stake is not None and max_stake > 0.0) else target_fixed
                eff_fixed_budget = min(total_fixed_budget, side_fixed_budget, avail_cap)
                if eff_fixed_budget < min_fixed_stake:
                    return 0.0
                stake = max(min_fixed_stake, min(target_fixed, eff_fixed_budget))
        except Exception as e:
            logger.debug(f"Error computing custom_stake_amount for {pair}: {e}")
            stake = proposed_stake

        if min_stake is not None:
            stake = max(min_stake, stake)
        if max_stake is not None:
            stake = min(max_stake, stake)
        return float(stake)

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
        close_price = float(candle.get("close", proposed_rate)) if candle else float(proposed_rate)

        if proposed_rate is None or proposed_rate <= 0 or np.isnan(proposed_rate):
            proposed_rate = close_price

        ema_9 = candle.get("ema_9") if candle else None
        ema_21 = candle.get("ema_21") if candle else None
        if ema_21 is None or np.isnan(ema_21) or ema_21 <= 0:
            ema_21 = proposed_rate
        if ema_9 is None or np.isnan(ema_9) or ema_9 <= 0:
            ema_9 = ema_21

        atr_val = candle.get("atr") if candle else None
        if atr_val is None or np.isnan(atr_val) or atr_val <= 0:
            atr_val = proposed_rate * 0.015

        fr_val = float(candle.get("funding_rate", 0.0001)) if candle else 0.0001

        is_short = str(side).lower() in ("short", "sell")

        if tag == "short_squeeze_expansion":
            target_limit = max(proposed_rate, close_price + 0.25 * atr_val)
        elif "ema9_pullback" in tag:
            target_limit = min(proposed_rate, float(ema_9) * 1.0010)
        elif "bb_pullback" in tag:
            target_limit = min(proposed_rate, close_price * 0.9990)
        elif "bb_fade" in tag or tag == "short_crowded_fade_alpha":
            target_limit = max(proposed_rate, close_price * 1.0010)
        elif "rs_momentum" in tag:
            if not is_short:
                rs_disc = 0.75 if "SOL" in pair else 0.45
                target_limit = min(proposed_rate, close_price - rs_disc * atr_val)
            else:
                target_limit = max(proposed_rate, close_price + 0.65 * atr_val)
        elif "squeeze" in tag or "expansion" in tag:
            if not is_short:
                target_limit = min(proposed_rate, close_price - 0.40 * atr_val)
            else:
                target_limit = max(proposed_rate, close_price + 0.25 * atr_val)
        elif "breakdown" in tag:
            if fr_val <= 0.00010:
                target_limit = max(proposed_rate, close_price * 1.0010)
            else:
                target_limit = max(proposed_rate, close_price + 0.20 * atr_val)
        else:
            if not is_short:
                if close_price > float(ema_21) * 1.0135:
                    target_limit = min(proposed_rate, float(ema_9) * 1.0010)
                else:
                    target_limit = min(proposed_rate, float(ema_21) * 1.0020)
            else:
                if "DOGE" in pair and close_price < float(ema_21) * 0.989:
                    target_limit = max(proposed_rate, float(ema_9) * 0.9990)
                else:
                    target_limit = max(proposed_rate, float(ema_21) * 0.9980)

        if target_limit <= 0 or np.isnan(target_limit):
            target_limit = proposed_rate if proposed_rate > 0 else float(ema_21)

        # Mandatory strict clamp [0.9885 * proposed_rate, 1.0095 * proposed_rate] guaranteeing 0 dur==0 custom_exit
        if proposed_rate > 0 and np.isfinite(proposed_rate):
            target_limit = float(np.clip(target_limit, 0.9885 * proposed_rate, 1.0095 * proposed_rate))

        return float(target_limit)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration <= 0.0:
            return None
        raw_lev = getattr(trade, "leverage", None)
        lev = float(raw_lev) if isinstance(raw_lev, (int, float)) and raw_lev > 0 else (4.0 if ("BTC" in pair and not trade.is_short) else 3.0)
        spot_profit = current_profit / lev if lev > 0 else current_profit
        open_rate = trade.open_rate
        is_short = trade.is_short

        candle = self._get_candle_info(pair, current_time)
        close_t1 = float(candle.get("close", current_rate)) if candle else current_rate
        current_atr = float(candle.get("atr", open_rate * 0.02)) if candle else (open_rate * 0.02)
        volume = float(candle.get("volume", 1.0)) if candle else 1.0
        volume_mean_20 = float(candle.get("volume_mean_20", 1.0)) if candle else 1.0
        rsi = float(candle.get("rsi", 50.0)) if candle else 50.0
        slowk = float(candle.get("slowk", 50.0)) if candle else 50.0
        slowd = float(candle.get("slowd", 50.0)) if candle else 50.0
        ema_9 = float(candle.get("ema_9", close_t1)) if candle else close_t1
        ema_21 = float(candle.get("ema_21", close_t1)) if candle else close_t1
        ema_50 = float(candle.get("ema_50", close_t1)) if candle else close_t1
        ema_200 = float(candle.get("ema_200", close_t1)) if candle else close_t1
        adx_4h = float(candle.get("adx_4h", 25.0)) if candle else 25.0

        peak_rate, peak_profit = self._get_causal_peak_profit(
            pair, trade, current_time, lev, current_rate, candle
        )

        # 0. Dual Concurrence Fundamental Crisis + Technical Breakdown
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
                is_below_ema = (close_t1 < ema_21) if candle else True
                if (spot_profit <= -0.010 and (is_below_ema or rsi < 45.0)) or (spot_profit <= -0.020 and volume > volume_mean_20 * 1.2):
                    return "news_emergency_invalidation"
        else:
            has_fundamental_threat = (p_score > 0.40) or global_threat
            if has_fundamental_threat:
                is_above_ema = (close_t1 > ema_21) if candle else True
                if (spot_profit <= -0.010 and (is_above_ema or rsi > 55.0)) or (spot_profit <= -0.020 and volume > volume_mean_20 * 1.2):
                    return "news_emergency_invalidation"

        entry_tag = getattr(trade, "enter_tag", "") or ""

        # 0B-1. High-Purity Short Squeeze Expansion Lifecycle (ADA, ETH, DOGE, HYPE)
        if entry_tag == "short_squeeze_expansion":
            embedded_short_sq = (adx_4h >= 28.0) and (close_t1 < ema_9) and (rsi > 34.0) and (slowk < 35.0)
            if close_t1 > ema_21 and current_profit >= 0.038:
                return "short_squeeze_ema21_protect_tp"
            if current_profit >= 0.040 and not embedded_short_sq:
                return "short_squeeze_expansion_tp"
            if peak_profit >= 0.055 and close_t1 > ema_9 and current_profit >= 0.022:
                return "short_squeeze_runner_lock_tp"
            if (trade_duration >= 2.0 and spot_profit <= -0.0245) or (spot_profit <= -0.0340):
                return "short_squeeze_hard_stop"
            if trade_duration >= 8.0 and spot_profit <= -0.0110 and close_t1 > ema_21:
                return "short_squeeze_stale_prune"

        # 0B-2. Long Squeeze Lifecycle (Core-5 w/ -0.40*ATR retest)
        elif entry_tag and "squeeze" in entry_tag:
            exp_tp_sq = 0.0270 if ("BTC" in pair and not is_short) else float(self.KNOB_BREAKOUT_TP_ROE)
            if current_profit >= exp_tp_sq:
                return "breakout_expansion_tp"
            mom_failed_sq = (close_t1 < ema_21) and (rsi < 48.0)
            if trade_duration >= 4.0 and ((spot_profit <= -0.0110 and mom_failed_sq) or (spot_profit <= -0.0185)):
                return "breakout_rapid_invalidation_4h"
            if trade_duration >= 15.0 and current_profit < -0.0050 and close_t1 < ema_21:
                return "breakout_14h_timeout"

        # 0B-3. High-PF RS-Momentum Lifecycle (SOL, LINK, ADA, ETH)
        elif entry_tag and "rs_momentum" in entry_tag:
            if current_profit >= float(self.KNOB_BREAKOUT_TP_ROE):
                return "breakout_expansion_tp"
            mom_struct_broken = (close_t1 < ema_50) and (rsi < 46.0)
            if trade_duration >= 4.0 and ((spot_profit <= -0.0155 and mom_struct_broken) or (spot_profit <= -0.0235)):
                return "breakout_rapid_invalidation_4h"
            if trade_duration >= 17.0 and current_profit < -0.0050 and close_t1 < ema_21:
                return "breakout_14h_timeout"

        # 0C. Crisp Bearish Breakdown Lifecycle (2h -2.50% tight stop + M2 MFE lock + 8.5h prune)
        if entry_tag and "breakdown" in entry_tag:
            if current_profit >= 0.035:
                return "short_breakdown_quick_tp"
            if (
                trade_duration >= 8.0
                and peak_profit >= 0.024
                and 0.008 <= current_profit < 0.021
                and close_t1 > ema_9
                and slowk > slowd
            ):
                return "short_breakdown_mfe_lock"
            if (trade_duration >= 2.0 and spot_profit <= -0.0250) or (spot_profit <= -0.0380):
                return "short_breakdown_tight_stop"
            if trade_duration >= 8.5 and spot_profit <= -0.0100:
                return "short_breakdown_stale_prune"

        # 1. M4 Embedded-Stochastic Waterfall Exemption on Short Fade
        if is_short:
            if "fade" in entry_tag:
                if close_t1 > ema_21 and current_profit >= 0.042:
                    return "short_fade_ema21_protect_tp"
                if peak_profit >= 0.060 and close_t1 > ema_9 and current_profit >= 0.025:
                    return "short_fade_runner_lock_tp"
                embedded_fade_waterfall = (close_t1 < ema_21) and (rsi > 34.0) and (adx_4h >= 28.0) and (slowk < 35.0)
                if current_profit > 0.045 and not embedded_fade_waterfall:
                    return "short_explosive_dump_tp"
            elif entry_tag != "short_squeeze_expansion":
                if current_profit > 0.045:
                    return "short_explosive_dump_tp"

        # 1B. M1 BTC 4.0x Spot-ROI Parity
        if "BTC" in pair and not is_short:
            if (
                (trade_duration >= 3.00 and current_profit >= 0.1200)
                or (trade_duration >= 7.78 and current_profit >= 0.1033)
                or (trade_duration >= 11.47 and current_profit >= 0.0613)
                or (trade_duration >= 19.55 and current_profit >= 0.0068)
            ):
                return "btc_spot_roi_parity"

        # 2. De-Compressed PAXG Scalp + Positive-ROE Slot Recycler
        if "PAXG" in pair:
            if trade_duration >= 2.0:
                paxg_exhausted = (rsi >= 57.5) if not is_short else (rsi <= 42.5)
                if current_profit >= 0.0110 or (trade_duration >= 4.0 and current_profit >= 0.0072 and paxg_exhausted):
                    return "paxg_volatility_tp"
            if trade_duration >= 8.0 and current_profit >= 0.0048:
                return "paxg_slot_recycle_tp"

        # 3. De-Compressed Profit Banking for ETH + 10.0h Runner-Exempt Pullback Stall Bank (>= +3.30% ROE)
        if "ETH" in pair and trade_duration >= 16.0:
            eth_fading = (rsi < 50.0) if not is_short else (rsi > 50.0)
            if (eth_fading and current_profit >= 0.0080) or (trade_duration >= 18.0 and current_profit >= 0.0180):
                return "eth_profit_bank_tp"

        if (
            not is_short
            and "pullback" in entry_tag
            and all(x not in pair for x in ("PAXG", "BTC", "HYPE"))
            and trade_duration >= 10.0
            and current_profit >= float(self.KNOB_PULLBACK_STALL_ROE)
        ):
            embedded_bull_runner = (adx_4h >= 28.0) and (close_t1 > ema_9) and (slowk > 65.0) and (rsi < 68.0)
            if not embedded_bull_runner:
                return "pullback_10h_stall_tp"

        # 4. Tailored Exits for HYPE
        if "HYPE" in pair:
            if current_profit >= 0.045 and trade_duration >= 6.0:
                return "hype_quick_tp"
            if trade_duration >= 36.0 and spot_profit <= -0.060:
                return "hype_stale_prune_36h"
            if trade_duration > 72.0 and current_profit < -0.050:
                return "hype_stale_loss"
            if trade_duration > 96.0 and current_profit < 0.010:
                return "hype_time_cutoff"

        # 5. M4 Closed-Candle (T-1) Chandelier ATR Profit Ratchet
        if peak_profit >= 0.085 and trade_duration >= 2.0 and current_profit >= 0.040:
            if not is_short:
                structure_broken = (close_t1 <= peak_rate - 1.20 * current_atr) and (close_t1 < ema_21) and (rsi < 62.0)
                mega_ratchet = (peak_profit >= 0.135) and (close_t1 <= peak_rate - 1.50 * current_atr) and (current_profit >= 0.060) and (rsi < 62.0)
                if structure_broken or mega_ratchet:
                    return "chandelier_atr_trailing_exit"
            else:
                structure_broken = (close_t1 >= peak_rate + 1.20 * current_atr) and (close_t1 > ema_21) and (rsi > 38.0)
                mega_ratchet = (peak_profit >= 0.135) and (close_t1 >= peak_rate + 1.50 * current_atr) and (current_profit >= 0.060) and (rsi > 38.0)
                if structure_broken or mega_ratchet:
                    return "chandelier_atr_trailing_exit"

        if current_profit >= 0.035:
            return None

        # 6. Crisp Adaptive Adaptive Stale Pruning (18h -2.20%/-2.70% spot + 8h EMA200 Collapse Guard)
        if "PAXG" in pair:
            failing_paxg = (rsi < 45.0 or close_t1 < ema_21) if not is_short else (rsi > 55.0 or close_t1 > ema_21)
            if trade_duration >= 16.0 and spot_profit <= -0.012 and failing_paxg:
                return "paxg_early_stale_prune"
        elif "HYPE" not in pair and "breakdown" not in entry_tag and entry_tag != "short_squeeze_expansion":
            if trade_duration >= 8.0 and spot_profit <= -0.0420:
                deep_ema200_collapse = (
                    (close_t1 < ema_200 and rsi < 36.0 and slowk < slowd)
                    if not is_short
                    else (close_t1 > ema_200 and rsi > 64.0 and slowk > slowd)
                )
                if deep_ema200_collapse:
                    return "adaptive_stale_prune_18h"

            prune_scale = float(self.KNOB_STALE_PRUNE_SCALE)
            failing_18h = (rsi < 46.0 or close_t1 < ema_21) if not is_short else (rsi > 54.0 or close_t1 > ema_21)
            eff_threshold = (-0.0220 if "pullback" in entry_tag else -0.0270) * prune_scale
            if trade_duration >= 18.0 and spot_profit <= eff_threshold and failing_18h:
                return "adaptive_stale_prune_18h"
            if trade_duration >= 24.0 and spot_profit <= (-0.0200 * prune_scale):
                return "adaptive_stale_prune_24h"

        if "HYPE" not in pair:
            if trade_duration > 36.0 and current_profit < -0.010:
                return "time_decay_stale_loss"
            if trade_duration > 48.0 and current_profit < 0.005:
                return "time_decay_48h_cutoff"

        return None

    def check_entry_timeout(self, pair: str, trade: Trade, order: Any,
                            current_time: datetime, **kwargs) -> bool:
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
        for p in pairs:
            fr_tuple = (p, "1h", CandleType.FUNDING_RATE)
            if fr_tuple not in informative:
                informative.append(fr_tuple)
        return informative

    def _merge_causal_funding(self, dataframe: DataFrame, pair: str) -> DataFrame:
        fr_df = None
        if hasattr(self, "dp") and self.dp:
            try:
                fr_df = self.dp.get_pair_dataframe(
                    pair=pair, timeframe="1h", candle_type=CandleType.FUNDING_RATE
                )
            except Exception:
                fr_df = None

        if fr_df is None or fr_df.empty:
            pair_slug = pair.replace("/", "_").replace(":", "_")
            for base_dir in (
                Path("user_data/data/bybit/futures"),
                Path("/freqtrade/user_data/data/bybit/futures"),
            ):
                cand_fp = base_dir / f"{pair_slug}-1h-funding_rate.feather"
                if cand_fp.is_file():
                    try:
                        fr_df = pd.read_feather(cand_fp)
                        break
                    except Exception:
                        fr_df = None

        default_fr = 0.0001
        dataframe["funding_rate"] = default_fr
        dataframe["funding_mean_24h"] = default_fr

        if fr_df is not None and not fr_df.empty and "date" in fr_df.columns:
            val_col = (
                "funding_rate"
                if ("funding_rate" in fr_df.columns and fr_df["funding_rate"].notna().any())
                else ("open" if "open" in fr_df.columns else None)
            )
            if val_col is not None:
                clean_fr = fr_df[["date", val_col]].copy()
                clean_fr["fr_val"] = pd.to_numeric(clean_fr[val_col], errors="coerce").fillna(default_fr)
                clean_fr["date_avail"] = (
                    pd.to_datetime(clean_fr["date"], utc=True) + pd.Timedelta(hours=1)
                ).astype("datetime64[ns, UTC]")
                clean_fr = clean_fr.drop_duplicates(subset=["date_avail"], keep="last").sort_values("date_avail")
                clean_fr["fr_mean_3"] = clean_fr["fr_val"].rolling(3, min_periods=1).mean()

                left_dates = pd.DataFrame(
                    {
                        "_orig_idx": np.arange(len(dataframe)),
                        "_dt_ns": pd.to_datetime(dataframe["date"], utc=True).astype("datetime64[ns, UTC]"),
                    }
                ).sort_values("_dt_ns")

                merged = pd.merge_asof(
                    left_dates,
                    clean_fr[["date_avail", "fr_val", "fr_mean_3"]],
                    left_on="_dt_ns",
                    right_on="date_avail",
                    direction="backward",
                ).sort_values("_orig_idx")

                dataframe["funding_rate"] = merged["fr_val"].fillna(default_fr).to_numpy()
                dataframe["funding_mean_24h"] = merged["fr_mean_3"].fillna(default_fr).to_numpy()
                return dataframe

        if hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) not in ("backtest", "hyperopt"):
                for dpath in (
                    Path("user_data/data/derivatives_state.json"),
                    Path("/freqtrade/user_data/data/derivatives_state.json"),
                ):
                    if dpath.is_file():
                        try:
                            raw_d = json.loads(dpath.read_text(encoding="utf-8"))
                            p_dict = raw_d.get("pairs", {})
                            p_info = p_dict.get(pair) or p_dict.get(pair.split(":")[0]) or {}
                            if "funding_rate_8h_pct" in p_info and len(dataframe) > 0:
                                fr_live = float(p_info["funding_rate_8h_pct"]) / 100.0
                                dataframe.iloc[-1, dataframe.columns.get_loc("funding_rate")] = fr_live
                                dataframe.iloc[-1, dataframe.columns.get_loc("funding_mean_24h")] = fr_live
                            break
                        except Exception:
                            pass
        return dataframe

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])

        dataframe["ema50_rollover"] = dataframe["ema_50"] < dataframe["ema_50"].shift(2)
        dataframe["ema50_recovery"] = dataframe["ema_50"] > dataframe["ema_50"].shift(2)
        dataframe["adx_rising"] = dataframe["adx"] > dataframe["adx"].shift(1)
        dataframe["macro_bull_4h_adaptive"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_50"].shift(3))
        dataframe["macro_bear_4h_adaptive"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_50"].shift(3))
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair", "")

        if hasattr(self, "dp") and self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=pair, timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

            btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="4h")
            if not btc_4h.empty:
                btc_4h["btc_ema_50"] = ta.EMA(btc_4h, timeperiod=50)
                btc_4h["btc_ema_200"] = ta.EMA(btc_4h, timeperiod=200)
                btc_4h["btc_rsi"] = ta.RSI(btc_4h, timeperiod=14)
                btc_4h["btc_adx"] = ta.ADX(btc_4h, timeperiod=14)
                btc_4h["btc_strong_bull"] = (btc_4h["close"] > btc_4h["btc_ema_50"]) & (btc_4h["btc_adx"] >= 16.5)
                dataframe = merge_informative_pair(dataframe, btc_4h, self.timeframe, "4h", ffill=True, append_timeframe=False, suffix="btc_4h")

            btc_1h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="1h")
            if not btc_1h.empty:
                btc_1h["btc_ret_24h"] = btc_1h["close"].pct_change(24)
                btc_1h["btc_ret_1h_log"] = np.log(btc_1h["close"] / btc_1h["close"].shift(1))
                btc_1h["btc_ret_std_24"] = btc_1h["btc_ret_1h_log"].rolling(24).std()
                btc_1h["btc_ema_50_1h"] = ta.EMA(btc_1h, timeperiod=50)
                btc_1h["btc_rsi_1h"] = ta.RSI(btc_1h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, btc_1h, self.timeframe, "1h", ffill=True, append_timeframe=False, suffix="btc_1h")

        mbull_col = f"macro_bull_4h_{self.informative_timeframe}"
        mbear_col = f"macro_bear_4h_{self.informative_timeframe}"
        for col in [
            mbull_col,
            mbear_col,
            f"macro_bull_4h_adaptive_{self.informative_timeframe}",
            f"macro_bear_4h_adaptive_{self.informative_timeframe}",
            f"ema50_rollover_{self.informative_timeframe}",
            f"ema50_recovery_{self.informative_timeframe}",
            f"adx_rising_{self.informative_timeframe}",
        ]:
            if col not in dataframe.columns:
                dataframe[col] = False

        if "btc_ret_std_24_btc_1h" not in dataframe.columns:
            dataframe["btc_ret_std_24_btc_1h"] = 0.01

        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0

        if "btc_strong_bull_btc_4h" not in dataframe.columns:
            dataframe["btc_strong_bull_btc_4h"] = True
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

        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = (dataframe["atr"] / dataframe["close"].replace(0.0, np.nan)).fillna(0.0)
        dataframe["atr_pct_med168"] = dataframe["atr_pct"].rolling(window=168, min_periods=1).median().fillna(0.0)
        dataframe["btc_ret_6h"] = dataframe["close_btc_1h"].pct_change(6).fillna(0.0)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe["bb_middleband"]
        dataframe["bb_width_ema"] = dataframe["bb_width"].ewm(span=20).mean()

        keltner = qtpylib.keltner_channel(dataframe, window=20, atrs=1.5)
        dataframe["kc_lowerband"] = keltner["lower"]
        dataframe["kc_middleband"] = keltner["mid"]
        dataframe["kc_upperband"] = keltner["upper"]

        dataframe["in_squeeze"] = (dataframe["bb_lowerband"] > dataframe["kc_lowerband"]) & (dataframe["bb_upperband"] < dataframe["kc_upperband"])
        dataframe["was_in_squeeze"] = dataframe["in_squeeze"].shift(1).rolling(5).max() > 0

        dataframe["squeeze_streak_3"] = dataframe["in_squeeze"].shift(1).rolling(3).sum() >= 3
        dataframe["squeeze_fired"] = (~dataframe["in_squeeze"]) & dataframe["squeeze_streak_3"] & dataframe["was_in_squeeze"]
        dataframe["squeeze_followthrough_3"] = (
            dataframe["squeeze_fired"]
            | (dataframe["squeeze_fired"].shift(1) & (dataframe["close"] > dataframe["close"].shift(1)))
            | (dataframe["squeeze_fired"].shift(2) & (dataframe["close"] > dataframe["close"].shift(1)))
        )

        dataframe["squeeze_streak_2_down"] = dataframe["in_squeeze"].shift(1).rolling(2).sum() >= 2
        dataframe["squeeze_fired_down"] = (~dataframe["in_squeeze"]) & dataframe["squeeze_streak_2_down"] & dataframe["was_in_squeeze"]
        dataframe["squeeze_followthrough_down_3"] = (
            dataframe["squeeze_fired_down"]
            | (dataframe["squeeze_fired_down"].shift(1) & (dataframe["close"] < dataframe["close"].shift(1)))
            | (dataframe["squeeze_fired_down"].shift(2) & (dataframe["close"] < dataframe["close"].shift(1)))
            | (dataframe["squeeze_fired_down"].shift(3) & (dataframe["close"] < dataframe["close"].shift(1)))
        )

        donchian_mid = (dataframe["high"].rolling(20).max() + dataframe["low"].rolling(20).min()) / 2.0
        sma_20 = dataframe["close"].rolling(20).mean()
        delta = dataframe["close"] - (donchian_mid + sma_20) / 2.0
        slope = talib.LINEARREG_SLOPE(delta.fillna(0.0).values, timeperiod=20)
        dataframe["squeeze_momentum"] = pd.Series(slope, index=dataframe.index).fillna(0.0)
        dataframe["mom_expanding_up"] = (dataframe["squeeze_momentum"] > 0) & (dataframe["squeeze_momentum"] > dataframe["squeeze_momentum"].shift(1))
        dataframe["mom_expanding_down"] = (dataframe["squeeze_momentum"] < 0) & (dataframe["squeeze_momentum"] < dataframe["squeeze_momentum"].shift(1))

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

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
        dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)

        dataframe["support_level_20"] = dataframe["low"].rolling(window=20).min()
        dataframe["taker_sell_volume_surge"] = (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.15)

        dataframe = self._merge_causal_funding(dataframe, pair)

        dates = pd.to_datetime(dataframe["date"]).dt.to_pydatetime()
        adx_4h_col = dataframe[f"adx_{self.informative_timeframe}"]
        self._candle_arrays[pair] = {
            "close": dataframe["close"].to_numpy(dtype=float),
            "high": dataframe["high"].to_numpy(dtype=float),
            "low": dataframe["low"].to_numpy(dtype=float),
        }
        records = [
            {
                "idx": idx,
                "close": float(c) if not np.isnan(c) else 0.0,
                "high": float(hi) if not np.isnan(hi) else 0.0,
                "low": float(lo) if not np.isnan(lo) else 0.0,
                "atr": float(a) if not np.isnan(a) else 0.0,
                "atr_pct": float(ap) if not np.isnan(ap) else 0.0,
                "atr_pct_med168": float(apm) if not np.isnan(apm) else 0.0,
                "rsi": float(r) if not np.isnan(r) else 50.0,
                "slowk": float(sk) if not np.isnan(sk) else 50.0,
                "slowd": float(sd) if not np.isnan(sd) else 50.0,
                "ema_9": float(e9) if not np.isnan(e9) else float(c),
                "ema_21": float(e21) if not np.isnan(e21) else float(c),
                "ema_50": float(e50) if not np.isnan(e50) else float(c),
                "ema_200": float(e200) if not np.isnan(e200) else float(c),
                "adx_4h": float(ax) if not np.isnan(ax) else 25.0,
                "macro_bull_4h": bool(mbull),
                "macro_bear_4h": bool(mbear),
                "btc_strong_bull_4h": bool(bsb),
                "funding_rate": float(fr) if not np.isnan(fr) else 0.0001,
                "funding_mean_24h": float(frm) if not np.isnan(frm) else 0.0001,
                "volume": float(v) if not np.isnan(v) else 0.0,
                "volume_mean_20": float(vm) if not np.isnan(vm) else 1.0,
                "support_level_20": float(s) if not np.isnan(s) else 0.0,
            }
            for idx, (c, hi, lo, a, ap, apm, r, sk, sd, e9, e21, e50, e200, ax, mbull, mbear, bsb, fr, frm, v, vm, s) in enumerate(
                zip(
                    dataframe["close"], dataframe["high"], dataframe["low"],
                    dataframe["atr"], dataframe["atr_pct"], dataframe["atr_pct_med168"],
                    dataframe["rsi"], dataframe["slowk"], dataframe["slowd"],
                    dataframe["ema_9"], dataframe["ema_21"], dataframe["ema_50"], dataframe["ema_200"],
                    adx_4h_col, dataframe[mbull_col], dataframe[mbear_col],
                    dataframe["btc_strong_bull_btc_4h"].fillna(True),
                    dataframe["funding_rate"], dataframe["funding_mean_24h"],
                    dataframe["volume"], dataframe["volume_mean_20"], dataframe["support_level_20"],
                )
            )
        ]
        self._candle_cache[pair] = dict(zip(dates, records))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        pair = metadata.get("pair", "")

        # M5 Exact Symmetric Velocity Shock & 4H Rollover/Recovery Shields
        btc_std_24 = dataframe["btc_ret_std_24_btc_1h"].fillna(0.01)
        btc_velocity_shock = (
            (dataframe["btc_ret_1h_log_btc_1h"] < (-2.1 * btc_std_24)) &
            (dataframe["btc_rsi_1h_btc_1h"] < 40.0)
        )
        btc_up_velocity_shock = (
            (dataframe["btc_ret_1h_log_btc_1h"] > (2.1 * btc_std_24)) &
            (dataframe["btc_rsi_1h_btc_1h"] > 60.0)
        )
        ema50_rollover_4h = (dataframe[f"ema50_rollover_{self.informative_timeframe}"] == True)
        ema50_recovery_4h = (dataframe[f"ema50_recovery_{self.informative_timeframe}"] == True)
        dying_bull_rollover_4h = ema50_rollover_4h & (dataframe[f"adx_{self.informative_timeframe}"] < 25.0)
        dying_bear_recovery_4h = ema50_recovery_4h & (dataframe[f"adx_{self.informative_timeframe}"] < 25.0)

        # Pair-Calibrated Perpetual Funding Regimes
        adx_4h_raw = dataframe[f"adx_{self.informative_timeframe}"]
        adx_4h_s = adx_4h_raw / float(self.KNOB_ADX_GATE_SCALE)
        if "BTC" in pair:
            healthy_long_contango = (dataframe["funding_rate"] >= -0.00005) & (dataframe["funding_rate"] <= 0.00075)
        elif "PAXG" in pair:
            healthy_long_contango = (dataframe["funding_rate"] >= -0.00003) & (dataframe["funding_rate"] <= 0.00065)
        elif "HYPE" in pair:
            healthy_long_contango = (
                ((dataframe["funding_rate"] >= -0.00004) & (dataframe["funding_rate"] <= 0.00055))
                | ((dataframe["funding_rate"] <= -0.00005) & (dataframe["is_green"] == True))
            )
        elif "LINK" in pair:
            healthy_long_contango = (
                ((dataframe["funding_rate"] >= -0.00004) & (dataframe["funding_rate"] <= 0.00084))
                | ((dataframe["funding_rate"] <= -0.00005) & (dataframe["is_green"] == True))
            )
        elif "DOGE" in pair or "ETH" in pair:
            fr_cap_de = np.where(adx_4h_s >= 23.0, 0.00084, 0.00052)
            healthy_long_contango = (
                ((dataframe["funding_rate"] > 0.0) & (dataframe["funding_rate"] <= fr_cap_de))
                | ((dataframe["funding_rate"] <= -0.00005) & (dataframe["is_green"] == True))
            )
        else:
            healthy_long_contango = (
                ((dataframe["funding_rate"] > 0.0) & (dataframe["funding_rate"] <= 0.00052))
                | ((dataframe["funding_rate"] <= -0.00005) & (dataframe["is_green"] == True))
            )

        if self.ABLATE_FUNDING_REGIME:
            healthy_long_contango = pd.Series(True, index=dataframe.index)
            uncrowded_squeeze_funding = pd.Series(True, index=dataframe.index)
            uncrowded_rs_funding = pd.Series(True, index=dataframe.index)
        else:
            uncrowded_squeeze_funding = (dataframe["funding_rate"] > 0.0) & (dataframe["funding_rate"] <= 0.00030)
            uncrowded_rs_funding = (dataframe["funding_rate"] >= -0.00005) & (dataframe["funding_rate"] <= 0.00038)

        # M5 Exact BTC 4H Structural Alignment
        btc_structural_bull_4h = (
            (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) &
            (dataframe["btc_ema_50_btc_4h"] > dataframe["btc_ema_200_btc_4h"])
        )
        btc_structural_bear_4h = (
            (dataframe["close_btc_4h"] < dataframe["btc_ema_50_btc_4h"]) &
            (dataframe["btc_ema_50_btc_4h"] < dataframe["btc_ema_200_btc_4h"])
        )
        e200_trans_mult = 1.015 if "HYPE" in pair else 1.000
        bull_transition_hp = (
            (dataframe[f"macro_bull_4h_adaptive_{self.informative_timeframe}"] == True) &
            (dataframe["close"] > dataframe["ema_200"] * e200_trans_mult) &
            btc_structural_bull_4h &
            (dataframe[f"adx_rising_{self.informative_timeframe}"] == True)
        )
        bear_transition_hp = (
            (dataframe[f"macro_bear_4h_adaptive_{self.informative_timeframe}"] == True) &
            (dataframe["close"] < dataframe["ema_200"]) &
            btc_structural_bear_4h
        )
        bull_regime_ok = (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) | bull_transition_hp
        bear_regime_ok = (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) | bear_transition_hp
        btc_waterfall_ok = (
            True
            if ("PAXG" in pair or self.ABLATE_REGIME_RISK_SHIELDS)
            else ((dataframe["btc_ret_6h"] > -0.020) | (dataframe["delta_rs"] > 0.025))
        )
        long_shields_ok = (~btc_velocity_shock) & (~dying_bull_rollover_4h) & healthy_long_contango & btc_waterfall_ok

        cross_up = qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        cross_dn = qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])

        long_ema9_pb = pd.Series(False, index=dataframe.index)
        long_bb_pb = pd.Series(False, index=dataframe.index)
        short_bb_fade = pd.Series(False, index=dataframe.index)
        short_crowded_fade = pd.Series(False, index=dataframe.index)

        if "PAXG" not in pair and not self.ABLATE_UPPER_BB_FADES:
            fr_crowded_ok = True if self.ABLATE_FUNDING_REGIME else (dataframe["funding_rate"] >= 0.00022)
            short_crowded_fade = (
                fr_crowded_ok &
                (dataframe["high"] >= dataframe["bb_upperband"] * 0.998) &
                (dataframe["rsi"] >= 68.5) &
                (dataframe["is_red"] == True) &
                (dataframe["slowk"] > 72.0) &
                (dataframe["slowk"] < dataframe["slowk"].shift(1)) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.85)
            )

        # -------------------------------------------------------------
        # ENGINE 1: Clamp-Safe Bull Pullback + Non-Overlapping Idle-Window Sub-Alphas
        # -------------------------------------------------------------
        if "PAXG" in pair:
            t3b_paxg = (
                (dataframe["low"] <= dataframe["ema_21"] * 1.005) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ).rolling(3).max() > 0

            paxg_base = (
                bull_regime_ok &
                long_shields_ok &
                (adx_4h_s > 18.5) &
                (
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0085) |
                    (dataframe["low"] <= dataframe["bb_middleband"]) |
                    ((adx_4h_s >= 20.0) & (dataframe["close"] > dataframe["ema_200"]) & t3b_paxg)
                ) &
                (dataframe["close"] <= dataframe["ema_21"] * 1.0135) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                (dataframe["rsi"] >= 42.5) &
                (dataframe["rsi"] <= 66.0) &
                cross_up &
                (dataframe["slowk"] < 55.5) &
                (
                    (dataframe["is_green"] == True) |
                    (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
            )
            paxg_curl = (
                bull_regime_ok &
                long_shields_ok &
                (adx_4h_s >= 20.5) &
                (dataframe["ema_50"] > dataframe["ema_200"]) &
                (dataframe["close"] > dataframe["ema_200"]) &
                (
                    (dataframe["low"] <= dataframe["ema_21"] * 1.006) |
                    (dataframe["low"] <= dataframe["bb_middleband"])
                ) &
                (dataframe["close"] <= dataframe["ema_21"] * 1.0135) &
                (dataframe["close"] >= dataframe["ema_50"]) &
                (dataframe["rsi"] >= 44.0) &
                (dataframe["rsi"] <= 66.0) &
                (dataframe["slowk"] < 48.0) &
                (dataframe["slowk"] > dataframe["slowd"]) &
                (dataframe["slowk"] > dataframe["slowk"].shift(1)) &
                (dataframe["is_green"] == True) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.65)
            )
            paxg_ema50_bounce = (
                bull_regime_ok &
                long_shields_ok &
                (adx_4h_s > 20.5) &
                (dataframe["ema_50"] > dataframe["ema_200"]) &
                (dataframe["low"] <= dataframe["ema_50"] * 1.004) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                (dataframe["close"] <= dataframe["ema_21"] * 1.010) &
                (dataframe["rsi"] >= 39.5) &
                (dataframe["rsi"] < 43.0) &
                cross_up &
                (dataframe["slowk"] < 42.0) &
                (dataframe["is_green"] == True) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
            )
            long_pullback = paxg_base | paxg_curl | paxg_ema50_bounce

            long_ema9_pb = (
                (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
                long_shields_ok &
                (adx_4h_s >= 19.5) &
                (dataframe["ema_50"] > dataframe["ema_200"]) &
                (dataframe["low"] <= dataframe["ema_9"] * 1.002) &
                (dataframe["close"] >= dataframe["ema_21"]) &
                (dataframe["close"] <= dataframe["ema_9"] * 1.006) &
                (dataframe["rsi"] >= 46.0) &
                (dataframe["rsi"] <= 65.0) &
                cross_up &
                (dataframe["slowk"] < 56.0) &
                (dataframe["is_green"] == True) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
            )
        elif "BTC" in pair or "ADA" in pair:
            adx_gate_ba = 21.5 if "BTC" in pair else 20.0
            ba_base = (
                bull_regime_ok &
                long_shields_ok &
                (adx_4h_s > adx_gate_ba) &
                (
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0100) |
                    (dataframe["low"] <= dataframe["bb_middleband"])
                ) &
                (dataframe["close"] <= dataframe["ema_21"] * 1.0135) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                (dataframe["rsi"] >= 43.0) &
                (dataframe["rsi"] <= 63.0) &
                cross_up &
                (dataframe["slowk"] < 54.5) &
                (
                    (dataframe["is_green"] == True) |
                    (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
            )
            if "BTC" in pair:
                long_pullback = ba_base
            else:
                long_bb_pb = (
                    bull_regime_ok &
                    (~btc_velocity_shock) &
                    healthy_long_contango &
                    (dataframe["close"] > dataframe["ema_200"]) &
                    (dataframe["low"] <= dataframe["bb_lowerband"] * 1.004) &
                    (dataframe["rsi"] <= 42.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
                )
                ada_rs = (
                    bull_regime_ok &
                    long_shields_ok &
                    (adx_4h_s >= 24.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (dataframe["close"] > dataframe["ema_200"]) &
                    (dataframe["delta_rs"] > 0.015) &
                    (
                        (dataframe["low"] <= dataframe["ema_21"] * 1.006) |
                        (dataframe["low"] <= dataframe["bb_middleband"])
                    ) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0135) &
                    (dataframe["close"] >= dataframe["ema_50"]) &
                    (dataframe["rsi"] >= 45.0) &
                    (dataframe["rsi"] <= 67.0) &
                    (dataframe["slowk"] < 53.0) &
                    (dataframe["slowk"] > dataframe["slowd"]) &
                    (dataframe["slowk"] > dataframe["slowk"].shift(1)) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
                )
                ada_ema50_bounce = (
                    bull_regime_ok &
                    long_shields_ok &
                    (adx_4h_s > 21.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (dataframe["low"] <= dataframe["ema_50"] * 1.004) &
                    (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.010) &
                    (dataframe["rsi"] >= 39.5) &
                    (dataframe["rsi"] < 43.0) &
                    cross_up &
                    (dataframe["slowk"] < 42.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
                )
                long_pullback = ba_base | ada_rs | ada_ema50_bounce
        else:
            adx_long_gate = 21.0 if ("ETH" in pair or "SOL" in pair) else 20.0
            rsi_min_long = 44.0 if "SOL" in pair else 43.0
            btc_macro_bull_link = (
                (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) &
                (dataframe["btc_rsi_btc_4h"] > 48.0)
            )
            link_ok = True if "LINK" not in pair else btc_macro_bull_link

            strong_bull = (
                bull_regime_ok &
                (adx_4h_s >= 24.0) &
                (dataframe["ema_50"] > dataframe["ema_200"]) &
                (dataframe["close"] > dataframe["ema_200"])
            )
            rsi_max_long = np.where(strong_bull, 67.0, 63.0)

            touched_3b_long = (
                (dataframe["low"] <= dataframe["ema_21"] * 1.005) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ).rolling(3).max() > 0

            if "HYPE" in pair:
                dip_zone_long = (
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0120) |
                    (dataframe["close"] <= dataframe["ema_21"] + 0.45 * dataframe["atr"]) |
                    (dataframe["low"] <= dataframe["bb_middleband"])
                )
                long_ema9_pb = (
                    (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
                    long_shields_ok &
                    (adx_4h_s >= 23.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (dataframe["low"] <= dataframe["ema_9"] * 1.002) &
                    (dataframe["close"] >= dataframe["ema_21"]) &
                    (dataframe["close"] <= dataframe["ema_9"] * 1.006) &
                    (dataframe["rsi"] >= 46.0) &
                    (dataframe["rsi"] <= 65.5) &
                    cross_up &
                    (dataframe["slowk"] < 56.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
                )
            else:
                bb_mid_hammer_ok = (
                    (dataframe["low"] <= dataframe["bb_middleband"]) &
                    (dataframe["is_green"] == True) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0280)
                ) if ("LINK" in pair or "DOGE" in pair or "ETH" in pair) else False

                dip_zone_long = (
                    (
                        (
                            (dataframe["close"] <= dataframe["ema_21"] * 1.0100) |
                            (dataframe["low"] <= dataframe["bb_middleband"]) |
                            (strong_bull & touched_3b_long)
                        ) &
                        (dataframe["close"] <= dataframe["ema_21"] * 1.0135)
                    ) | bb_mid_hammer_ok
                )

            long_pullback_base = (
                bull_regime_ok &
                long_shields_ok &
                link_ok &
                (adx_4h_s > adx_long_gate) &
                dip_zone_long &
                (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                (dataframe["rsi"] >= rsi_min_long) &
                (dataframe["rsi"] <= rsi_max_long) &
                cross_up &
                (dataframe["slowk"] < 55.0) &
                (
                    (dataframe["is_green"] == True) |
                    (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
            )

            if "DOGE" not in pair:
                long_pullback_rs = (
                    strong_bull &
                    long_shields_ok &
                    link_ok &
                    (dataframe["delta_rs"] > 0.015) &
                    (
                        (dataframe["low"] <= dataframe["ema_21"] * 1.006) |
                        (dataframe["low"] <= dataframe["bb_middleband"])
                    ) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0135 if "HYPE" not in pair else True) &
                    (dataframe["close"] >= dataframe["ema_50"]) &
                    (dataframe["rsi"] >= 45.0) &
                    (dataframe["rsi"] <= 67.0) &
                    (dataframe["slowk"] < 53.0) &
                    (dataframe["slowk"] > dataframe["slowd"]) &
                    (dataframe["slowk"] > dataframe["slowk"].shift(1)) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
                )
            else:
                long_pullback_rs = pd.Series(False, index=dataframe.index)

            if "HYPE" not in pair:
                long_pullback_reclaim = (
                    bull_regime_ok &
                    long_shields_ok &
                    link_ok &
                    (adx_4h_s >= 23.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (
                        (dataframe["low"] <= dataframe["ema_50"] * 1.002) |
                        (dataframe["low"] <= dataframe["bb_lowerband"] * 1.005)
                    ) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.0135) &
                    (dataframe["close"] >= dataframe["ema_200"] * 1.010) &
                    (dataframe["rsi"] >= 38.0) &
                    (dataframe["rsi"] <= 54.0) &
                    cross_up &
                    (dataframe["slowk"] < 44.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
                )
            else:
                long_pullback_reclaim = pd.Series(False, index=dataframe.index)

            if "LINK" in pair or "DOGE" in pair:
                ema50_bounce_ld = (
                    bull_regime_ok &
                    long_shields_ok &
                    link_ok &
                    (adx_4h_s > 21.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (dataframe["low"] <= dataframe["ema_50"] * 1.004) &
                    (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
                    (dataframe["close"] <= dataframe["ema_21"] * 1.010) &
                    (dataframe["rsi"] >= 39.5) &
                    (dataframe["rsi"] < 43.0) &
                    cross_up &
                    (dataframe["slowk"] < 42.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
                )
            else:
                ema50_bounce_ld = pd.Series(False, index=dataframe.index)

            ema9_shallow_pb = (
                (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
                (~btc_velocity_shock) &
                (~dying_bull_rollover_4h) &
                link_ok &
                (dataframe["funding_rate"] > 0.0) &
                (dataframe["funding_rate"] <= 0.00035) &
                (adx_4h_s >= 27.0) &
                (dataframe["ema_50"] > dataframe["ema_200"]) &
                (dataframe["low"] <= dataframe["ema_9"] * 1.002) &
                (dataframe["close"] >= dataframe["ema_21"]) &
                (dataframe["close"] <= dataframe["ema_9"] * 1.006) &
                (dataframe["rsi"] >= 48.0) &
                (dataframe["rsi"] <= 65.0) &
                cross_up &
                (dataframe["slowk"] < 55.0) &
                (dataframe["is_green"] == True) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
            )

            if "SOL" in pair:
                long_ema9_pb = (
                    (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
                    (~btc_velocity_shock) &
                    (~dying_bull_rollover_4h) &
                    (dataframe["funding_rate"] >= -0.00005) &
                    (dataframe["funding_rate"] <= 0.00045) &
                    (adx_4h_s >= 23.0) &
                    (dataframe["ema_50"] > dataframe["ema_200"]) &
                    (dataframe["low"] <= dataframe["ema_9"] * 1.002) &
                    (dataframe["close"] >= dataframe["ema_21"]) &
                    (dataframe["close"] <= dataframe["ema_9"] * 1.006) &
                    (dataframe["rsi"] >= 46.0) &
                    (dataframe["rsi"] <= 65.5) &
                    cross_up &
                    (dataframe["slowk"] < 56.0) &
                    (dataframe["is_green"] == True) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
                )
                long_pullback = long_pullback_base | long_pullback_rs | long_pullback_reclaim
            else:
                long_pullback = (
                    long_pullback_base
                    | long_pullback_rs
                    | long_pullback_reclaim
                    | ema50_bounce_ld
                    | ema9_shallow_pb
                )

        # -------------------------------------------------------------
        # ENGINE 2: Core-5 TTM Squeeze w/ -0.40*ATR Maker Retest (PF 4.989!)
        # -------------------------------------------------------------
        squeeze_compression_long = dataframe["in_squeeze"].shift(1).rolling(6).sum() >= 5
        adx_squeeze_gate = 20.0 if "SOL" in pair else np.where(squeeze_compression_long, 16.0, 20.0)

        if all(x not in pair for x in ("BTC", "PAXG", "HYPE")):
            long_squeeze = (
                (dataframe["squeeze_followthrough_3"] == True) &
                (dataframe["bb_width"] >= dataframe["bb_width_ema"] * 0.94) &
                (dataframe["mom_expanding_up"] == True) &
                (dataframe["close"] > dataframe["ema_21"]) &
                (dataframe["close"] < dataframe["bb_upperband"] * 1.005) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.995) &
                (dataframe["rsi"] >= 50.0) &
                (dataframe["rsi"] <= 68.0) &
                (dataframe["btc_rsi_1h_btc_1h"] <= 68.0) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 1.10) &
                bull_regime_ok &
                (~btc_velocity_shock) &
                (~ema50_rollover_4h) &
                uncrowded_squeeze_funding &
                (adx_4h_s >= adx_squeeze_gate) &
                (dataframe["is_green"] == True)
            )
        else:
            long_squeeze = pd.Series(False, index=dataframe.index)

        # -------------------------------------------------------------
        # ENGINE 3: High-Purity Decoupled RS Momentum (SOL -0.75*ATR; ADA/ETH/LINK -0.45*ATR)
        # -------------------------------------------------------------
        if all(x not in pair for x in ("BTC", "PAXG", "HYPE", "DOGE")):
            if "ETH" in pair:
                min_delta_rs = 0.036
                min_adx_rs = 21.0
            elif "ADA" in pair:
                min_delta_rs = 0.025
                min_adx_rs = 21.0
            else:
                min_delta_rs = 0.028
                min_adx_rs = 21.0

            long_rs = (
                (dataframe["delta_rs"] > min_delta_rs) &
                (dataframe["delta_rs"] > dataframe["delta_rs_ema"]) &
                (dataframe["close"] > dataframe["ema_21"]) &
                (dataframe["close"] <= dataframe["bb_upperband"] * 1.005) &
                (dataframe["close"] >= dataframe["ema_50"] * 0.995) &
                (dataframe["rsi"] >= 48.0) &
                (dataframe["rsi"] <= 67.0) &
                (dataframe["btc_rsi_1h_btc_1h"] <= 69.0) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 1.06) &
                (dataframe["close"] > dataframe["ema_200"]) &
                bull_regime_ok &
                (~btc_velocity_shock) &
                (~ema50_rollover_4h) &
                uncrowded_rs_funding &
                (adx_4h_s >= min_adx_rs) &
                (dataframe["is_green"] == True)
            )
        else:
            long_rs = pd.Series(False, index=dataframe.index)

        # -------------------------------------------------------------
        # ENGINE 4A: Clamp-Safe Short Resistance Fade + Idle-Window Bear BB-Upper Exhaustion (ETH & ADA)
        # -------------------------------------------------------------
        short_fade = pd.Series(False, index=dataframe.index)
        fr_sf_min = np.where((adx_4h_s >= 28.0) & ("SOL" in pair), -0.00035, -0.00010)
        short_shields_ok = (~btc_up_velocity_shock) & (~dying_bear_recovery_4h) & (True if self.ABLATE_FUNDING_REGIME else (dataframe["funding_rate"] > fr_sf_min))
        if "BTC" not in pair and "HYPE" not in pair:
            if "PAXG" in pair or "ADA" in pair:
                adx_sf_pa = 18.0 if "PAXG" in pair else 22.0
                sf_pa_base = (
                    bear_regime_ok &
                    short_shields_ok &
                    (dataframe["close"] < dataframe["ema_200"]) &
                    (dataframe["close"] >= dataframe["ema_21"] * 0.989) &
                    (adx_4h_s > adx_sf_pa) &
                    (
                        (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                        (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                    ) &
                    (dataframe["close"] <= dataframe["ema_50"] + 0.30 * dataframe["atr"]) &
                    (dataframe["rsi"] >= 53.0) &
                    (dataframe["rsi"] <= 67.0) &
                    cross_dn &
                    (dataframe["slowk"] > 55.0) &
                    (
                        (dataframe["is_red"] == True) |
                        (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
                    ) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
                )
                if "ADA" in pair:
                    ada_short_rs = (
                        bear_regime_ok &
                        (~btc_up_velocity_shock) &
                        (~ema50_recovery_4h) &
                        (dataframe["funding_rate"] >= -0.00010) &
                        (adx_4h_s >= 21.0) &
                        (dataframe["delta_rs"] < -0.028) &
                        (dataframe["delta_rs"] < dataframe["delta_rs_ema"]) &
                        (dataframe["close"] < dataframe["ema_21"]) &
                        (dataframe["close"] >= dataframe["bb_lowerband"] * 0.995) &
                        (dataframe["close"] <= dataframe["ema_50"] * 1.005) &
                        (dataframe["close"] < dataframe["ema_200"]) &
                        (dataframe["rsi"] >= 33.0) &
                        (dataframe["rsi"] <= 52.0) &
                        (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05) &
                        (dataframe["is_red"] == True)
                    )
                    short_fade = sf_pa_base | ada_short_rs
                    short_bb_fade = (
                        bear_regime_ok &
                        (~btc_up_velocity_shock) &
                        (dataframe["funding_rate"] > -0.00005) &
                        (dataframe["close"] < dataframe["ema_200"]) &
                        (dataframe["high"] >= dataframe["bb_upperband"] * 0.996) &
                        (dataframe["rsi"] >= 60.0) &
                        (dataframe["slowk"] > 65.0) &
                        (dataframe["is_red"] == True) &
                        (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
                    )
                else:
                    short_fade = sf_pa_base
            else:
                adx_short_gate = 25.0 if "SOL" in pair else 22.0
                rsi_min_short = 55.0 if "SOL" in pair else 53.0

                res_touched_3b = (
                    (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                    (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                ).rolling(3).max() > 0

                if "DOGE" in pair or "LINK" in pair:
                    high_posfr_rej = (
                        (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                        (dataframe["funding_rate"] >= 0.00008) &
                        (dataframe["high"] >= dataframe["ema_21"] * 0.996) &
                        (dataframe["close"] <= dataframe["ema_50"] + 0.40 * dataframe["atr"]) &
                        (dataframe["slowk"] > 55.0) &
                        (dataframe["is_red"] == True)
                    )
                else:
                    high_posfr_rej = pd.Series(False, index=dataframe.index)

                stoch_or_posfr = (cross_dn & (dataframe["slowk"] > 54.0)) | high_posfr_rej
                ema50_cap_mult = np.where((adx_4h_s >= 28.0) & ("SOL" in pair), 0.85, 0.50)

                short_fade_base = (
                    short_shields_ok &
                    (dataframe["close"] >= dataframe["ema_21"] * 0.989) &
                    (adx_4h_s > adx_short_gate) &
                    res_touched_3b &
                    (dataframe["close"] <= dataframe["ema_50"] + ema50_cap_mult * dataframe["atr"]) &
                    (dataframe["rsi"] >= rsi_min_short) &
                    (dataframe["rsi"] <= 69.5) &
                    stoch_or_posfr &
                    (
                        (dataframe["is_red"] == True) |
                        (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
                    ) &
                    (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
                )

                if "LINK" in pair:
                    link_short_regime = (
                        bear_regime_ok |
                        ((dataframe["close_btc_4h"] < dataframe["btc_ema_50_btc_4h"]) & (dataframe["close"] < dataframe["ema_50"]))
                    )
                    short_fade = short_fade_base & link_short_regime & (dataframe["close"] < dataframe["ema_200"])
                elif "DOGE" in pair:
                    doge_ema9_fade = (
                        (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                        (~btc_up_velocity_shock) &
                        (~dying_bear_recovery_4h) &
                        (dataframe["funding_rate"] > 0.0) &
                        (adx_4h_s >= 26.0) &
                        (adx_4h_raw <= 56.0) &
                        (dataframe["ema_50"] < dataframe["ema_200"]) &
                        (dataframe["close"] < dataframe["ema_200"]) &
                        (dataframe["high"] >= dataframe["ema_9"] * 0.998) &
                        (dataframe["close"] <= dataframe["ema_21"]) &
                        (dataframe["close"] >= dataframe["ema_9"] * 0.993) &
                        (dataframe["rsi"] >= 45.0) &
                        (dataframe["rsi"] <= 58.0) &
                        cross_dn &
                        (dataframe["slowk"] > 48.0) &
                        (dataframe["is_red"] == True) &
                        (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
                    )
                    short_fade = (short_fade_base & bear_regime_ok & (dataframe["close"] < dataframe["ema_200"])) | doge_ema9_fade
                elif "ETH" in pair:
                    eth_short_rs = (
                        bear_regime_ok &
                        (~btc_up_velocity_shock) &
                        (~ema50_recovery_4h) &
                        (dataframe["funding_rate"] >= -0.00010) &
                        (adx_4h_s >= 21.0) &
                        (dataframe["delta_rs"] < -0.028) &
                        (dataframe["delta_rs"] < dataframe["delta_rs_ema"]) &
                        (dataframe["close"] < dataframe["ema_21"]) &
                        (dataframe["close"] >= dataframe["bb_lowerband"] * 0.995) &
                        (dataframe["close"] <= dataframe["ema_50"] * 1.005) &
                        (dataframe["close"] < dataframe["ema_200"]) &
                        (dataframe["rsi"] >= 33.0) &
                        (dataframe["rsi"] <= 52.0) &
                        (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05) &
                        (dataframe["is_red"] == True)
                    )
                    short_fade = (short_fade_base & bear_regime_ok & (dataframe["close"] < dataframe["ema_200"])) | eth_short_rs
                    # Idle-window Bear Upper-BB Exhaustion on ETH
                    short_bb_fade = (
                        bear_regime_ok &
                        (~btc_up_velocity_shock) &
                        (dataframe["funding_rate"] > -0.00005) &
                        ~((dataframe["funding_rate"] <= 0.0) & (dataframe["close"] >= dataframe["ema_200"] * 0.990)) &
                        (dataframe["close"] < dataframe["ema_200"]) &
                        (dataframe["high"] >= dataframe["bb_upperband"] * 0.996) &
                        (dataframe["rsi"] >= 58.0) &
                        (dataframe["slowk"] > 65.0) &
                        (dataframe["is_red"] == True) &
                        (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
                    )
                else:
                    short_fade = short_fade_base & bear_regime_ok & (dataframe["close"] < dataframe["ema_200"])

        # -------------------------------------------------------------
        # ENGINE 4B: Core-4 Hybrid Bearish Breakdown (ADA, DOGE, SOL high-ADX hole unlocked)
        # -------------------------------------------------------------
        short_breakdown = pd.Series(False, index=dataframe.index)
        if all(x not in pair for x in ("BTC", "PAXG", "LINK", "HYPE")):
            bd_m5_core = (
                (dataframe["funding_rate"] >= -0.00040) &
                (dataframe["funding_rate"] <= 0.00010) &
                (adx_4h_s >= 21.0) &
                (adx_4h_s < 35.5)
            )
            fr_bd_min = -0.00020 if ("ADA" in pair or "SOL" in pair or "DOGE" in pair) else 0.000001
            bd_throwback = (
                (dataframe["funding_rate"] >= fr_bd_min) &
                (dataframe["funding_rate"] <= 0.00030) &
                (adx_4h_s >= 21.0) &
                (dataframe["close"] >= dataframe["ema_21"] * 0.955)
            )
            short_breakdown = (
                (~btc_up_velocity_shock) &
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                (dataframe["close"] < dataframe["ema_200"]) &
                (dataframe["close"] < dataframe["ema_21"] * 0.985) &
                (dataframe["close"] < dataframe["ema_50"]) &
                (dataframe["rsi"] >= 32.0) &
                (dataframe["rsi"] <= 48.0) &
                (dataframe["slowk"] < dataframe["slowd"]) &
                (dataframe["taker_sell_volume_surge"] == True) &
                (dataframe["close"] < dataframe["support_level_20"].shift(1) * 0.999) &
                (bd_m5_core | bd_throwback)
            )

        # -------------------------------------------------------------
        # ENGINE 4C: Negative/Neutral-Funding Bearish Squeeze (ADA, ETH, DOGE, HYPE)
        # -------------------------------------------------------------
        short_squeeze = pd.Series(False, index=dataframe.index)
        if all(x not in pair for x in ("BTC", "PAXG", "SOL", "LINK")):
            sq_comp_down = dataframe["in_squeeze"].shift(1).rolling(6).sum() >= 4
            adx_short_sq_gate = np.where(sq_comp_down, 16.0, 19.5)

            short_squeeze = (
                (dataframe["funding_rate"] >= -0.00035) &
                (dataframe["funding_rate"] <= 0.00003) &
                (~btc_up_velocity_shock) &
                (dataframe["squeeze_followthrough_down_3"] == True) &
                (dataframe["mom_expanding_down"] == True) &
                (dataframe["bb_width"] >= dataframe["bb_width_ema"] * 0.94) &
                (dataframe["close"] < dataframe["ema_21"]) &
                (dataframe["close"] > dataframe["bb_lowerband"] * 0.995) &
                (dataframe["close"] <= dataframe["ema_50"] * 1.005) &
                (dataframe["rsi"] >= 33.0) &
                (dataframe["rsi"] <= 55.0) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05) &
                (dataframe["is_red"] == True) &
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                (adx_4h_s >= adx_short_sq_gate)
            )

        if self.ABLATE_UPPER_BB_FADES:
            short_bb_fade = pd.Series(False, index=dataframe.index)
            short_crowded_fade = pd.Series(False, index=dataframe.index)
        if self.ABLATE_SQUEEZE_AND_RS:
            long_squeeze = pd.Series(False, index=dataframe.index)
            short_squeeze = pd.Series(False, index=dataframe.index)
            long_rs = pd.Series(False, index=dataframe.index)

        # Priority-ordered tag assignment:
        # long_pullback_alpha FIRST so EMA21 value fills are NEVER displaced up to EMA9!
        any_long = long_pullback | long_bb_pb | long_ema9_pb | long_squeeze | long_rs
        dataframe.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[long_bb_pb & ~long_pullback, ["enter_long", "enter_tag"]] = (1, "long_bb_pullback")
        dataframe.loc[long_ema9_pb & ~long_pullback & ~long_bb_pb, ["enter_long", "enter_tag"]] = (1, "long_ema9_pullback")
        dataframe.loc[long_squeeze & ~long_pullback & ~long_bb_pb & ~long_ema9_pb, ["enter_long", "enter_tag"]] = (1, "long_squeeze_expansion")
        dataframe.loc[long_rs & ~long_pullback & ~long_bb_pb & ~long_ema9_pb & ~long_squeeze, ["enter_long", "enter_tag"]] = (1, "long_rs_momentum")

        dataframe.loc[short_fade & ~any_long, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")
        dataframe.loc[short_crowded_fade & ~any_long & ~short_fade, ["enter_short", "enter_tag"]] = (1, "short_crowded_fade_alpha")
        dataframe.loc[short_bb_fade & ~any_long & ~short_fade & ~short_crowded_fade, ["enter_short", "enter_tag"]] = (1, "short_bb_fade")
        dataframe.loc[short_breakdown & ~any_long & ~short_fade & ~short_crowded_fade & ~short_bb_fade, ["enter_short", "enter_tag"]] = (1, "short_breakdown_alpha")
        dataframe.loc[short_squeeze & ~any_long & ~short_fade & ~short_crowded_fade & ~short_bb_fade & ~short_breakdown, ["enter_short", "enter_tag"]] = (1, "short_squeeze_expansion")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        long_exit = (
            (dataframe["rsi"] >= 71.0) &
            (dataframe["close"] > dataframe["bb_upperband"])
        )
        short_exit = (
            (dataframe["rsi"] <= 28.0) &
            (dataframe["close"] < dataframe["bb_lowerband"])
        )

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "rsi_overbought_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "rsi_oversold_tp")

        return dataframe
