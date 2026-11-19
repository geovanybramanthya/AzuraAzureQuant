"""
ApexDualAlpha_Omni_V29_P2_PositionLifecycleEngine

Out-of-the-Box Multi-Stage Position Lifecycle Engine — Strictly Causal T-1 Zero-Lookahead Architecture
======================================================================================================
1. Strict T-1 Closed-Candle Causality & Zero-Exploit Execution:
   - _get_candle_info: O(1) lookup querying exclusively prev_hour = floor(T) - 1h.
   - _get_causal_peak_profit: Reconstructs post-entry MFE strictly over closed bars [open_hour..T-1] + open(T).
   - custom_entry_price: Clamps all resting maker limits within [0.9885 * proposed_rate, 1.0095 * proposed_rate].
   - _merge_causal_funding: Backward-merges 1h funding rates at settlement + 1h <= candle_date (0.0001 live fallback).
   - trailing_stop = False, minimal_roi["0"] = 2.500, and custom_exit duration <= 0 guard -> 0 dur==0 trades.
   - Institutional Leverage Cap: 3.0x on all Shorts and Altcoin Longs, 4.0x on BTC Longs.

2. Multi-Stage Position Lifecycle Engine (adjust_trade_position + House-Money Macro Runner):
   - Stage-1A De-Risked Winner Pyramiding (+0.35x): Adds once into confirmed early trend breakouts
     (long_squeeze_expansion, long_rs_momentum, short_squeeze_expansion) when 4H ADX is rising and T-1 EMA9/EMA21 align.
   - Stage-1B Selective High-Continuation Partial Scale-Out (-58% to -62% stake): Banks immediate cash into wallet on
     parabolic BB-walks, HYPE expansions, 10h+ bull pullbacks, short squeeze expansions, high-ADX breakouts, and bear
     upper-BB fades ONLY when remaining runner stake exceeds >= 7.7% of equity (>= 0.36x fixed stake) and >= 52% scale fraction.
   - Stage-2 Free-Roll Macro Runner Lifecycle: Overrides minimal_roi on scaled runners to capture multi-day trend tails
     with a hard >= +1.55% ROE profit floor, smooth MFE ratchet (58%-68% lock), and causal T-1 EMA9/EMA21/Chandelier trail
     guaranteeing 0 winner-to-loser round-trips after partial scale-out.
   - Runner-Exempt Slot & Risk-Heat Architecture: House-money runners locked in profit do not consume unscaled downside
     risk slots or directional side-heat budgets, unlocking high-purity slot velocity while compressing max drawdown.
   - Causal T-1 BTC Conviction Sizing & Structural Collapse Pruning: Rehabilitates BTC to net profitability across both
     compounding and pure non-compounding fixed-stake modes so all 8/8 pairs are net profitable.
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


class ApexDualAlpha_Omni_V29_Champion(IStrategy):
    KNOB_GLOBAL_THRESH_SCALE: float = 1.0
    # Tunable core parameters for +-15% sensitivity & Leave-One-Out ablation audits
    KNOB_RANGE_TP_ROE: float = 0.0420
    KNOB_SLOT01_BOOST: float = 1.0500
    ABLATE_LIFECYCLE_ENGINE: bool = False
    ABLATE_RANGE_SWEEP: bool = False
    ABLATE_ZSCORE_COHERENCE: bool = False
    ABLATE_INTRINSIC_MICRO: bool = False
    ABLATE_CROSS_SECTIONAL: bool = False
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
    max_open_trades = 8
    ACTIVE_RISK_SLOTS = 5
    position_adjustment_enable = True
    max_entry_position_adjustment = 1
    ENABLE_PYRAMID = True
    KNOB_SCALEOUT_FRAC: float = 0.60

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
        "emergency_exit": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
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
    startup_candle_count: int = 840

    _candle_cache: Dict[str, Dict[datetime, Dict[str, Any]]] = {}
    _candle_arrays: Dict[str, Dict[str, np.ndarray]] = {}

    def __init__(self, config: dict) -> None:
        if isinstance(config, dict):
            if int(config.get("max_open_trades", 4) or 4) < self.max_open_trades:
                config["max_open_trades"] = self.max_open_trades
            ep = config.setdefault("entry_pricing", {})
            if isinstance(ep, dict):
                ep["price_side"] = "same"
            xp = config.setdefault("exit_pricing", {})
            if isinstance(xp, dict):
                xp["price_side"] = "other"
            ot = config.setdefault("order_types", {})
            if isinstance(ot, dict):
                ot.setdefault("entry", "limit")
                ot.setdefault("exit", "limit")
                ot["emergency_exit"] = "market"
                ot["force_exit"] = "market"
                ot["stoploss"] = "market"
                ot["stoploss_on_exchange"] = True
        super().__init__(config)
        self._candle_cache = {}
        self._candle_arrays = {}
        self._peer_1h_cache = {}
        self._cs_tensor_cache = {}
        self._last_known_wallet = float(config.get("dry_run_wallet", 1000.0) or 1000.0) if isinstance(config, dict) else 1000.0

    def _is_backtest_mode(self) -> bool:
        if hasattr(self, "dp") and self.dp:
            rm = getattr(self.dp, "runmode", None)
            if rm is not None:
                return getattr(rm, "value", str(rm)) in ("backtest", "hyperopt")
        if isinstance(getattr(self, "config", None), dict):
            rm_cfg = self.config.get("runmode")
            if rm_cfg is not None:
                return getattr(rm_cfg, "value", str(rm_cfg)) in ("backtest", "hyperopt")
        return False

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
        return ("BTC" not in pair)

    @staticmethod
    def _tier_flags(pair: str, candle: Dict[str, Any] | None) -> tuple[bool, bool, bool]:
        """Return (is_bench, is_lowvol, is_decoupled_hv) purely from causal market features."""
        rv = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
        am = float(candle.get("atr_pct_med672", 0.010)) if candle else 0.010
        bc6 = float(candle.get("btc_corr_672", 0.80)) if candle else 0.80
        is_bench = ("BTC" in pair)
        is_lowvol = (not is_bench) and ((rv < 0.72) or (am < 0.0056 and bc6 < 0.60))
        is_dec_hv = (not is_bench) and (rv >= 2.05) and (am >= 0.0085) and (bc6 < 0.70)
        return is_bench, is_lowvol, is_dec_hv

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
                            "open": float(last_row.get("open", c)),
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
                            "fr_zscore_168": float(last_row.get("fr_zscore_168", 0.0)),
                            "ema168_slope24": float(last_row.get("ema168_slope24", 0.0)),
                            "dc72_pos": float(last_row.get("dc72_pos", 0.5)),
                            "btc_ema168_slope24": float(last_row.get("btc_ema168_slope24_btc_1h", 0.0)),
                            "volume": float(last_row.get("volume", 0.0)),
                            "volume_mean_20": float(last_row.get("volume_mean_20", 1.0)),
                            "support_level_20": float(last_row.get("support_level_20", 0.0)),
                            "bb_middleband": float(last_row.get("bb_middleband", c)),
                            "bb_upperband": float(last_row.get("bb_upperband", c * 1.02)),
                            "bb_lowerband": float(last_row.get("bb_lowerband", c * 0.98)),
                            "er_12": float(last_row.get("er_12", 0.30)),
                            "er_24": float(last_row.get("er_24", 0.30)),
                            "ser_12": float(last_row.get("ser_12", 0.0)),
                            "cs_bull_breadth": float(last_row.get("cs_bull_breadth", 0.50)),
                            "cs_bear_breadth": float(last_row.get("cs_bear_breadth", 0.25)),
                            "cs_bull_4h": float(last_row.get("cs_bull_4h", 0.50)),
                            "cs_bear_4h": float(last_row.get("cs_bear_4h", 0.25)),
                            "cs_er12_mean": float(last_row.get("cs_er12_mean", 0.30)),
                            "cs_mom_rank": float(last_row.get("cs_mom_rank", 3.5)),
                            "cs_active_n": float(last_row.get("cs_active_n", 7.0)),
                            "atr_pct_med672": float(last_row.get("atr_pct_med672", 0.010)),
                            "rel_vol_672": float(last_row.get("rel_vol_672", 1.0)),
                            "btc_corr_336": float(last_row.get("btc_corr_336", 0.8)),
                            "btc_corr_672": float(last_row.get("btc_corr_672", 0.8)),
                            "fr_div": float(last_row.get("fr_div", 0.0)),
                            "adx_rising_4h": bool(last_row.get(f"adx_rising_{self.informative_timeframe}", False)),
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

    def _get_causal_mae_profit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        prev_candle: Dict[str, Any] | None,
    ) -> tuple[float, float]:
        """Reconstruct post-entry worst spot MAE strictly from closed candles [open_hour..T-1] + open(T)."""
        open_rate = float(trade.open_rate or 0.0)
        if open_rate <= 0.0:
            return 0.0, 0.0
        is_short = bool(trade.is_short)
        worst_rate = open_rate
        if current_rate and current_rate > 0.0:
            worst_rate = max(worst_rate, current_rate) if is_short else min(worst_rate, current_rate)

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
                            worst_rate = max(worst_rate, c_open) if is_short else min(worst_rate, c_open)
                        if i_prev > i_open:
                            if is_short:
                                span_max = float(np.max(arrays["high"][i_open + 1 : i_prev + 1]))
                                worst_rate = max(worst_rate, span_max)
                            else:
                                span_min = float(np.min(arrays["low"][i_open + 1 : i_prev + 1]))
                                if span_min > 0.0:
                                    worst_rate = min(worst_rate, span_min)
        elif hasattr(self, "dp") and self.dp:
            runmode = getattr(self.dp, "runmode", None)
            if runmode and getattr(runmode, "value", str(runmode)) not in ("backtest", "hyperopt"):
                raw_worst = getattr(trade, "max_rate" if is_short else "min_rate", None)
                if isinstance(raw_worst, (int, float)) and raw_worst > 0.0:
                    worst_rate = max(worst_rate, float(raw_worst)) if is_short else min(worst_rate, float(raw_worst))

        if not is_short:
            mae_spot = (worst_rate - open_rate) / open_rate
        else:
            mae_spot = (open_rate - worst_rate) / open_rate
        return worst_rate, min(0.0, mae_spot)

    def _get_causal_cum_atr(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        prev_candle: Dict[str, Any] | None,
        trade_duration_h: float,
    ) -> float:
        """Compute cumulative realized T-1 ATR% path age strictly over closed bars [i_open+1..i_prev]."""
        pair_cache = self._resolve_pair_cache(pair)
        arrays = self._candle_arrays.get(pair) if hasattr(self, "_candle_arrays") else None
        if pair_cache and arrays and "atr_pct" in arrays and prev_candle is not None and int(prev_candle.get("idx", -1)) >= 0 and trade.open_date_utc:
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
                    if i_prev > i_open:
                        return float(np.sum(arrays["atr_pct"][i_open + 1 : i_prev + 1]))
                    return 0.0
        if (not self._is_backtest_mode()) and hasattr(self, "dp") and self.dp and trade.open_date_utc:
            try:
                analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if analyzed_df is not None and not analyzed_df.empty and "atr_pct" in analyzed_df.columns:
                    dts = pd.to_datetime(analyzed_df["date"], utc=True)
                    o_utc = trade.open_date_utc if trade.open_date_utc.tzinfo else trade.open_date_utc.replace(tzinfo=timezone.utc)
                    p_utc = (current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
                    if dts.iloc[0] <= o_utc:
                        return float(analyzed_df.loc[(dts > o_utc) & (dts <= p_utc), "atr_pct"].sum())
            except Exception:
                return 0.0
        return 0.0

    def _is_trade_derisked_t1(self, ot: Any, current_time: datetime, thresh: float = 0.010) -> bool:
        if (not self.ABLATE_LIFECYCLE_ENGINE) and int(getattr(ot, "nr_of_successful_exits", 0) or 0) >= 1:
            return True
        ot_pair = getattr(ot, "pair", "") or ""
        try:
            candle = self._get_candle_info(ot_pair, current_time)
            if candle:
                ot_close = candle.get("close", 0.0)
                if ot_close and ot_close > 0.0 and ot.open_rate and ot.open_rate > 0.0:
                    return bool(ot.calc_profit_ratio(ot_close) >= thresh)
        except Exception as ot_err:
            logger.debug(f"Error checking T-1 profit for open trade {ot_pair}: {ot_err}")
        return False

    def _eval_open_cluster_state(self, open_trades: list, current_time: datetime) -> tuple[int, bool]:
        derisked_count = 0
        has_low_beta = False
        for ot in open_trades:
            ot_pair = getattr(ot, "pair", "") or ""
            ot_c = self._get_candle_info(ot_pair, current_time)
            ot_b, ot_lv, _ = self._tier_flags(ot_pair, ot_c)
            if ot_lv or ot_b:
                has_low_beta = True
            if (not self.ABLATE_LIFECYCLE_ENGINE) and int(getattr(ot, "nr_of_successful_exits", 0) or 0) >= 1:
                derisked_count += 1
                continue
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
        if isinstance(self.config, dict) and self.config.get("stake_amount") == "unlimited":
            wk = float(getattr(self, "_last_known_wallet", 0.0) or 0.0)
            lev_c = 4.0 if ((not is_short) and ("BTC" in pair)) else 3.0
            if wk > 0.0 and ((float(amount) * float(rate) / lev_c) < 0.056 * wk):
                return False

        if not is_short:
            if is_pair_blackout or global_abort or (score < -0.35):
                return False
        else:
            if global_abort or (score >= 0.40):
                return False

        try:
            open_trades = Trade.get_open_trades() or []
            unscaled_trades = open_trades if self.ABLATE_LIFECYCLE_ENGINE else [
                t for t in open_trades if int(getattr(t, "nr_of_successful_exits", 0) or 0) == 0
            ]
            total_open = len(unscaled_trades)
            cte_c = self._get_candle_info(pair, current_time)
            is_b_cte, is_lv_cte, is_hv_cte = self._tier_flags(pair, cte_c)
            is_core6 = (not is_b_cte) and (not is_lv_cte)
            is_paxg = is_lv_cte
            tag_str = str(entry_tag or "")
            derisked_count, has_low_beta = self._eval_open_cluster_state(open_trades, current_time)

            if total_open >= self.ACTIVE_RISK_SLOTS + 1:
                return False
            if total_open == self.ACTIVE_RISK_SLOTS:
                if not (derisked_count >= 2):
                    return False

            # Cross-position portfolio coherence: do not catch altcoin pullback knives against active un-derisked downside squeeze
            if (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_ZSCORE_COHERENCE):
                if (not is_short) and is_core6 and (not is_hv_cte) and ("pullback" in tag_str):
                    active_down_sq = sum(
                        1
                        for t in unscaled_trades
                        if bool(getattr(t, "is_short", False))
                        and ("short_squeeze" in str(getattr(t, "enter_tag", "") or ""))
                        and not self._is_trade_derisked_t1(t, current_time, 0.030)
                    )
                    if active_down_sq >= 1:
                        return False

            # Cap concurrent unscaled range_sweep trades (<= 1 long, <= 2 total) to block correlated cascades
            if "range_sweep" in tag_str:
                open_lrs = sum(
                    1 for t in unscaled_trades if ("range_sweep" in str(getattr(t, "enter_tag", "") or "")) and not bool(getattr(t, "is_short", False))
                )
                open_srs = sum(
                    1 for t in unscaled_trades if ("range_sweep" in str(getattr(t, "enter_tag", "") or "")) and bool(getattr(t, "is_short", False))
                )
                if (open_lrs + open_srs) >= 2:
                    return False
                if (not is_short) and open_lrs >= 1:
                    return False

            if is_short and "bb_fade" in tag_str:
                open_sbb = sum(
                    1 for t in unscaled_trades if "bb_fade" in str(getattr(t, "enter_tag", "") or "")
                )
                if open_sbb >= 2:
                    return False

            # Cap concurrent unscaled short_breakdown_alpha trades at <= 2
            if is_short and "breakdown" in tag_str:
                open_breakdowns = sum(
                    1 for t in unscaled_trades if "breakdown" in str(getattr(t, "enter_tag", "") or "")
                )
                if open_breakdowns >= 2:
                    return False

            same_side_count = sum(
                1 for t in unscaled_trades
                if bool(getattr(t, "is_short", False)) == is_short
            )

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
                        return bool((is_paxg or (("fade" in tag_str or "range_sweep" in tag_str) and is_core6)) and derisked_shorts >= 1)
                    if same_side_count == 2 and not is_paxg:
                        c_info_cte = self._get_candle_info(pair, current_time)
                        cs_bear_cte = float(c_info_cte.get("cs_bear_breadth", 0.25)) if c_info_cte else 0.25
                        cs_bear4_cte = float(c_info_cte.get("cs_bear_4h", 0.25)) if c_info_cte else 0.25
                        sync_bear_fade_ok = (not self.ABLATE_CROSS_SECTIONAL) and ("fade" in tag_str) and is_core6 and (cs_bear_cte >= 0.71) and (cs_bear4_cte >= 0.57)
                        return bool(derisked_shorts >= 1 or has_low_beta or "crowded_fade" in tag_str or "bb_fade" in tag_str or sync_bear_fade_ok)
            else:
                if same_side_count >= 6:
                    return False
                if same_side_count == 5:
                    return bool(derisked_count >= 2)
                if same_side_count == 4:
                    return bool(not is_core6 or derisked_count >= 1 or has_low_beta)
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

    def _is_non_hype_alt_lp(self, t_pair: str, t_tag: str, t_short: bool, cur_dt: datetime | None = None) -> bool:
        if t_short or (t_tag != "long_pullback_alpha") or ("BTC" in t_pair):
            return False
        if cur_dt is None:
            return True
        tc = self._get_candle_info(t_pair, cur_dt)
        _, is_lv, is_hv = self._tier_flags(t_pair, tc)
        return (not is_lv) and (not is_hv)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        tag = str(entry_tag or "")
        is_short = str(side).lower() in ("short", "sell")
        is_unlimited = isinstance(self.config, dict) and self.config.get("stake_amount") == "unlimited"
        n_slots = float(self.ACTIVE_RISK_SLOTS)
        candle = self._get_candle_info(pair, current_time)
        is_bench_sz, is_lowvol_sz, is_hv_sz = self._tier_flags(pair, candle)
        is_core6 = (not is_bench_sz) and (not is_lowvol_sz)
        is_alt_lp = self._is_non_hype_alt_lp(pair, tag, is_short, current_time)
        fr_raw = float(candle.get("funding_rate", 0.0001)) if candle else 0.0001
        fz168_t1 = float(candle.get("fr_zscore_168", 0.0)) if candle else 0.0
        s168_t1 = float(candle.get("ema168_slope24", 0.0)) if candle else 0.0
        dc72_t1 = float(candle.get("dc72_pos", 0.5)) if candle else 0.5
        bs168_t1 = float(candle.get("btc_ema168_slope24", 0.0)) if candle else 0.0
        cs_bull_t1 = float(candle.get("cs_bull_breadth", 0.50)) if candle else 0.50
        cs_bear_t1 = float(candle.get("cs_bear_breadth", 0.25)) if candle else 0.25
        cs_bull4_t1 = float(candle.get("cs_bull_4h", 0.50)) if candle else 0.50
        cs_bear4_t1 = float(candle.get("cs_bear_4h", 0.25)) if candle else 0.25
        cs_er_t1 = float(candle.get("cs_er12_mean", 0.30)) if candle else 0.30
        cs_mrk_t1 = float(candle.get("cs_mom_rank", 3.5)) if candle else 3.5
        cs_act_t1 = float(candle.get("cs_active_n", 7.0)) if candle else 7.0
        adx_4h = float(candle.get("adx_4h", 25.0)) if candle else 25.0
        er_12_t1 = float(candle.get("er_12", 0.30)) if candle else 0.30
        er_24_t1 = float(candle.get("er_24", 0.30)) if candle else 0.30
        adx_rising_t1 = bool(candle.get("adx_rising_4h", False)) if candle else False
        btc_strong_bull_4h = True if self.ABLATE_REGIME_RISK_SHIELDS else (bool(candle.get("btc_strong_bull_4h", True)) if candle else True)

        if is_unlimited and self.ABLATE_KELLY_SIZING:
            open_tr_eq = Trade.get_open_trades() or []
            tied_eq = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in open_tr_eq)
            tot_eq = (tied_eq + float(max_stake)) if (max_stake is not None and float(max_stake) > 0.0) else max(tied_eq, float(proposed_stake) * float(self.max_open_trades))
            slot_eq = tot_eq / n_slots
            avail_eq = float(max_stake) if (max_stake is not None and max_stake > 0.0) else slot_eq
            if avail_eq < 0.36 * slot_eq:
                return 0.0
            return float(min(slot_eq, avail_eq))

        rv_t1 = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
        # 1. Pair capital-velocity Kelly weights (every pair >= 0.36x institutional slot floor; zero dust)
        if is_hv_sz:
            pair_mult = 1.06
        elif is_bench_sz:
            btc_rsi_t1 = float(candle.get("rsi", 50.0)) if candle else 50.0
            btc_sk_t1 = float(candle.get("slowk", 50.0)) if candle else 50.0
            pair_mult = 0.78 if (not is_short and btc_rsi_t1 >= 49.0 and btc_sk_t1 >= 24.0 and adx_4h >= 23.0) else 0.28
        elif is_lowvol_sz:
            pair_mult = 0.28
        elif rv_t1 >= 1.55:
            pair_mult = 1.28
        else:
            pair_mult = 1.18

        # 2. Engine Fractional-Kelly Conviction Weights (calibrated to verified H1+H2 %PF)
        if tag == "short_range_sweep_alpha" or (tag == "range_sweep_alpha" and is_short):
            engine_mult = 1.46
        elif tag == "long_range_sweep_alpha" or (tag == "range_sweep_alpha" and not is_short):
            engine_mult = 1.46
        elif tag == "short_crowded_fade_alpha":
            engine_mult = 1.44
        elif "bb_fade" in tag:
            engine_mult = 1.12 if rv_t1 < 1.50 else 1.04
        elif "fade" in tag:
            bc672_t1 = float(candle.get("btc_corr_672", 0.80)) if candle else 0.80
            if rv_t1 < 1.50 and bc672_t1 >= 0.78 and is_core6:
                engine_mult = 0.65
            elif rv_t1 >= 1.65 and cs_er_t1 < 0.45:
                engine_mult = 1.28
            else:
                engine_mult = 1.12
        elif tag in ("long_breakout_expansion", "long_rs_momentum", "long_squeeze_expansion"):
            engine_mult = 1.44 if cs_er_t1 >= 0.24 else 1.26
        elif "short_squeeze" in tag:
            engine_mult = 1.26
        elif "breakdown" in tag:
            engine_mult = 0.94 if (fr_raw <= 0.00010 and cs_er_t1 < 0.42) else 0.82
        elif "bb_pullback" in tag:
            engine_mult = 1.26 if ((not self.ABLATE_ZSCORE_COHERENCE) and rv_t1 >= 1.65) else 1.06
        elif "pullback" in tag:
            if is_hv_sz:
                engine_mult = 1.44
            elif is_alt_lp and not btc_strong_bull_4h:
                engine_mult = 0.70
            elif er_24_t1 < 0.12:
                engine_mult = 0.82
            elif 0.00003 <= fr_raw <= 0.00050 and adx_4h >= 22.0:
                engine_mult = 1.28
            else:
                engine_mult = 1.16
        else:
            engine_mult = 1.00

        # 3. Mild causal T-1 tail-volatility dampener + Kaufman ER sawtooth chop shield
        vol_mult = 1.00
        if candle and is_core6:
            atr_pct_t1 = float(candle.get("atr_pct", 0.0) or 0.0)
            atr_med_t1 = float(candle.get("atr_pct_med168", 0.0) or 0.0)
            if atr_pct_t1 > 1e-6 and atr_med_t1 > 1e-6:
                vol_mult = float(np.clip((atr_med_t1 / atr_pct_t1) ** 0.75, 0.80, 1.16))
        if is_core6 and (not is_short) and er_24_t1 >= 0.32 and adx_rising_t1:
            vol_mult *= 1.12

        macro_mult = 1.00
        if (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_ZSCORE_COHERENCE) and candle:
            if is_short:
                if "breakdown" in tag and s168_t1 > -0.003:
                    macro_mult = 0.52
                elif ("bb_fade" in tag or "atr_fade" in tag) and bs168_t1 > 0.0 and dc72_t1 > 0.58:
                    macro_mult = 0.54
                elif "fade" in tag and s168_t1 < -0.008 and dc72_t1 < 0.70:
                    macro_mult = 1.08
            elif not is_lowvol_sz:
                if s168_t1 < 0.005 and dc72_t1 < 0.35:
                    macro_mult = 0.58
                elif s168_t1 >= 0.012 and (-1.40 <= fz168_t1 <= 1.80):
                    macro_mult = 1.12

        cs_conv_mult = 1.00
        cs_cap_mult = 1.00
        if candle and is_core6 and (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_CROSS_SECTIONAL):
            if not is_short:
                if (not is_hv_sz) and cs_er_t1 < 0.16:
                    cs_conv_mult = 0.72
                    cs_cap_mult = 0.70
                elif (not is_hv_sz) and (cs_act_t1 >= 7.0) and (cs_mrk_t1 >= cs_act_t1 - 1.0):
                    cs_conv_mult = 0.72
                    cs_cap_mult = 0.70
                elif (cs_bull4_t1 < 0.57 and cs_mrk_t1 <= 3.0) or (
                    cs_bull4_t1 >= 0.57 and cs_bear_t1 >= 0.43 and cs_mrk_t1 <= 4.0
                ):
                    cs_conv_mult = 1.24
                    cs_cap_mult = 1.10
                elif cs_mrk_t1 <= 3.0 and cs_bull_t1 < 0.85:
                    cs_conv_mult = 1.14
                    cs_cap_mult = 1.05
            else:
                if "bb_fade" in tag or "crowded" in tag:
                    cs_conv_mult = 1.20
                    cs_cap_mult = 1.08
                elif cs_bear4_t1 >= 0.57 and cs_bull_t1 < 0.57:
                    cs_conv_mult = 1.16
                    cs_cap_mult = 1.06
                elif "short_squeeze" in tag and cs_bear4_t1 < 0.43:
                    cs_conv_mult = 0.70
                    cs_cap_mult = 0.74
        vol_mult = vol_mult * cs_conv_mult

        kelly_knob = float(self.KNOB_KELLY_SCALE)
        cap_knob = float(np.clip(kelly_knob, 0.85, 1.10)) * cs_cap_mult * 1.24

        try:
            open_trades = Trade.get_open_trades() or []
            unscaled_open = open_trades if self.ABLATE_LIFECYCLE_ENGINE else [
                t for t in open_trades if int(getattr(t, "nr_of_successful_exits", 0) or 0) == 0
            ]
            same_side_trades = [t for t in open_trades if bool(getattr(t, "is_short", False)) == is_short]
            unscaled_same_side = same_side_trades if self.ABLATE_LIFECYCLE_ENGINE else [
                t for t in same_side_trades if int(getattr(t, "nr_of_successful_exits", 0) or 0) == 0
            ]
            same_side_open = len(unscaled_same_side)
            opp_side_open = len(unscaled_open) - same_side_open
            tied_up = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in open_trades)
            unscaled_tied_up = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in unscaled_open)
            unscaled_same_side_tied = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in unscaled_same_side)
            underisked_short_tied = 0.0
            if is_short and (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_ZSCORE_COHERENCE):
                underisked_short_tied = sum(
                    float(getattr(t, "stake_amount", 0.0) or 0.0)
                    for t in unscaled_same_side
                    if ("BTC" not in str(getattr(t, "pair", "") or ""))
                    and not self._is_trade_derisked_t1(t, current_time, 0.010)
                )
            alt_lp_tied = sum(
                float(getattr(t, "stake_amount", 0.0) or 0.0)
                for t in unscaled_same_side
                if self._is_non_hype_alt_lp(
                    str(getattr(t, "pair", "") or ""),
                    str(getattr(t, "enter_tag", "") or ""),
                    bool(getattr(t, "is_short", False)),
                    current_time,
                )
            )

            if is_unlimited:
                true_total_tradable = (tied_up + float(max_stake)) if (max_stake is not None and float(max_stake) > 0.0) else max(tied_up, float(proposed_stake) * float(self.max_open_trades))
                prev_w = float(getattr(self, "_last_known_wallet", 0.0) or 0.0)
                new_w = max(tied_up / 0.95, true_total_tradable / 0.95)
                if not (prev_w > 0.0 and new_w < 0.75 * prev_w):
                    self._last_known_wallet = new_w
                slot_1x = true_total_tradable / n_slots
                min_inst_stake = max(5.6, 0.36 * slot_1x, 0.054 * true_total_tradable)
                s01_boost = float(self.KNOB_SLOT01_BOOST) if (same_side_open <= 1) else 1.00

                use_cs_taper = (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_CROSS_SECTIONAL)
                opp_conflict_cap = 0.74 if (use_cs_taper and is_core6 and opp_side_open >= 2) else 1.00
                cap_knob = cap_knob * opp_conflict_cap

                if is_short:
                    co_table_s = (1.16 * s01_boost, 0.90 * s01_boost, 0.64, 0.48, 0.40)
                    co_mult = co_table_s[min(same_side_open, 4)]
                    if use_cs_taper and opp_side_open >= 2:
                        co_mult *= 0.68
                    slot_base = true_total_tradable / 1.78
                    raw_mult = float(np.clip(pair_mult * engine_mult * vol_mult * macro_mult * co_mult * kelly_knob, 0.24, 1.75))
                    stake = slot_base * raw_mult
                    if not is_core6:
                        single_cap = (0.088 if is_bench_sz else 0.084) * true_total_tradable
                    elif tag in ("short_crowded_fade_alpha", "short_range_sweep_alpha", "range_sweep_alpha"):
                        single_cap = 0.34 * cap_knob * true_total_tradable
                    elif "bb_fade" in tag:
                        single_cap = 0.225 * cap_knob * true_total_tradable
                    elif "fade" in tag and rv_t1 >= 1.50:
                        single_cap = 0.235 * cap_knob * true_total_tradable
                    elif "short_squeeze" in tag:
                        single_cap = 0.28 * cap_knob * true_total_tradable
                    elif "breakdown" in tag:
                        single_cap = 0.205 * cap_knob * true_total_tradable
                    else:
                        single_cap = 0.19 * cap_knob * true_total_tradable
                    side_cap_ratio = 0.74
                else:
                    co_table_l = (1.350 * s01_boost, 1.110 * s01_boost, 0.855, 0.605, 0.465)
                    co_mult = co_table_l[min(same_side_open, 4)]
                    if use_cs_taper and opp_side_open >= 2:
                        co_mult *= 0.66
                    if is_alt_lp and not btc_strong_bull_4h and alt_lp_tied > 0.12 * true_total_tradable:
                        co_mult *= 0.62
                    if use_cs_taper and is_core6 and same_side_open >= 1 and cs_bull4_t1 >= 0.85:
                        co_mult *= 0.70
                    slot_base = true_total_tradable / 1.40
                    raw_mult = float(np.clip(pair_mult * engine_mult * vol_mult * macro_mult * co_mult * kelly_knob, 0.24, 1.92))
                    stake = slot_base * raw_mult
                    if not is_core6:
                        btc_hi = is_bench_sz and (float(candle.get("rsi", 50.0)) >= 49.0 if candle else False) and (float(candle.get("slowk", 50.0)) >= 24.0 if candle else False) and (adx_4h >= 23.0)
                        single_cap = ((0.125 if btc_hi else 0.082) if is_bench_sz else 0.082) * true_total_tradable
                    elif is_hv_sz:
                        single_cap = 0.210 * true_total_tradable
                    elif tag in ("long_range_sweep_alpha", "range_sweep_alpha"):
                        single_cap = 0.36 * cap_knob * true_total_tradable
                    elif "bb_pullback" in tag:
                        single_cap = 0.195 * cap_knob * true_total_tradable
                    elif tag in ("long_breakout_expansion", "long_squeeze_expansion", "long_rs_momentum"):
                        single_cap = 0.35 * cap_knob * true_total_tradable
                    elif is_alt_lp and not btc_strong_bull_4h:
                        single_cap = 0.22 * cap_knob * true_total_tradable
                    elif er_24_t1 < 0.12:
                        single_cap = 0.240 * cap_knob * true_total_tradable
                    elif rv_t1 >= 1.55:
                        single_cap = 0.315 * cap_knob * true_total_tradable
                    elif rv_t1 >= 1.10:
                        single_cap = 0.305 * cap_knob * true_total_tradable
                    else:
                        single_cap = 0.315 * cap_knob * true_total_tradable
                    side_cap_ratio = 0.86 if (use_cs_taper and cs_bull4_t1 >= 0.85) else 0.930

                if is_hv_sz:
                    single_cap = min(single_cap, 0.215 * true_total_tradable)
                if len(unscaled_open) >= self.ACTIVE_RISK_SLOTS:
                    single_cap = min(single_cap, 0.18 * cap_knob * true_total_tradable)
                elif len(open_trades) >= self.ACTIVE_RISK_SLOTS:
                    single_cap = min(single_cap, 0.22 * cap_knob * true_total_tradable)
                stake = max(min_inst_stake, min(stake, single_cap))
                total_heat_budget = 0.975 * true_total_tradable - unscaled_tied_up
                side_heat_budget = side_cap_ratio * true_total_tradable - unscaled_same_side_tied
                avail_cap = float(max_stake) if (max_stake is not None and max_stake > 0.0) else stake
                eff_budget = min(total_heat_budget, side_heat_budget, avail_cap)
                if is_short and (not self.ABLATE_REGIME_RISK_SHIELDS) and (not self.ABLATE_ZSCORE_COHERENCE) and is_core6 and underisked_short_tied > 0.0:
                    eff_budget = min(eff_budget, max(min_inst_stake, 0.43 * cap_knob * true_total_tradable - underisked_short_tied))
                if is_alt_lp and not btc_strong_bull_4h:
                    eff_budget = min(eff_budget, 0.38 * true_total_tradable - alt_lp_tied)
                if eff_budget < min_inst_stake:
                    return 0.0
                stake = min(stake, eff_budget)
            else:
                # Pure non-compounding fixed-stake mode: strictly scaled around proposed_stake (ZERO wallet ramp!)
                min_fixed_stake = max(float(min_stake or 5.5), 0.36 * proposed_stake)
                co_fixed = (1.00, 0.74, 0.52, 0.40)[min(same_side_open, 3)]
                if is_bench_sz:
                    btc_hi_f = (not is_short) and (float(candle.get("rsi", 50.0)) >= 49.0 if candle else False) and (float(candle.get("slowk", 50.0)) >= 24.0 if candle else False) and (adx_4h >= 23.0)
                    base_f = 1.12 if btc_hi_f else 0.36
                elif is_lowvol_sz:
                    base_f = 0.38
                elif is_hv_sz:
                    base_f = 1.22
                elif tag in ("long_breakout_expansion", "long_squeeze_expansion", "long_rs_momentum", "short_crowded_fade_alpha", "short_range_sweep_alpha", "range_sweep_alpha"):
                    base_f = 1.38
                elif tag == "short_bb_fade":
                    base_f = 0.95 if rv_t1 < 1.50 else 1.20
                elif tag == "long_bb_pullback":
                    base_f = 1.05 if rv_t1 < 1.65 else 1.12
                elif "fade" in tag and is_short:
                    base_f = 0.75 if rv_t1 < 1.65 else 1.08
                elif is_short and "breakdown" in tag:
                    base_f = 0.78
                elif is_alt_lp:
                    base_f = 0.90 if rv_t1 < 1.50 else 0.94
                else:
                    base_f = 1.00

                if is_core6 and (not is_short) and er_24_t1 >= 0.32 and adx_rising_t1:
                    base_f *= 1.08
                scale_fixed = 1.25 * kelly_knob
                single_fixed_cap = (1.30 if is_short else 1.95) * proposed_stake
                target_fixed = min(single_fixed_cap, proposed_stake * max(0.36, base_f * macro_mult * cs_conv_mult * scale_fixed * co_fixed))
                total_fixed_budget = 3.85 * proposed_stake - unscaled_tied_up
                side_fixed_budget = (1.88 if is_short else 3.25) * proposed_stake - unscaled_same_side_tied
                avail_cap = float(max_stake) if (max_stake is not None and max_stake > 0.0) else target_fixed
                eff_fixed_budget = min(total_fixed_budget, side_fixed_budget, avail_cap)
                if eff_fixed_budget < min_fixed_stake:
                    return 0.0
                stake = max(min_fixed_stake, min(target_fixed, eff_fixed_budget))
        except Exception as e:
            logger.debug(f"Error computing custom_stake_amount for {pair}: {e}")
            stake = proposed_stake

        if min_stake is not None and stake < min_stake:
            return 0.0
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

        if tag == "pyramid_trend_add":
            target_limit = proposed_rate
        elif tag == "short_squeeze_expansion":
            target_limit = max(proposed_rate, close_price + 0.25 * atr_val)
        elif "ema9_pullback" in tag:
            target_limit = min(proposed_rate, float(ema_9) * 1.0010)
        elif "bb_pullback" in tag or tag == "long_range_sweep_alpha" or (tag == "range_sweep_alpha" and not is_short):
            target_limit = min(proposed_rate, close_price * 0.9990)
        elif "bb_fade" in tag or tag in ("short_crowded_fade_alpha", "short_range_sweep_alpha") or (tag == "range_sweep_alpha" and is_short):
            target_limit = max(proposed_rate, close_price * 1.0010)
        elif tag == "long_breakout_expansion":
            is_sq_bar = bool(candle.get("squeeze_followthrough_3", False)) if candle else False
            rv_ep = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
            retest_d = 0.40 if is_sq_bar else (0.68 if rv_ep >= 1.55 else 0.45)
            target_limit = min(proposed_rate, close_price - retest_d * atr_val)
        elif "rs_momentum" in tag:
            if not is_short:
                rv_cep_rs = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
                rs_disc = 0.75 if rv_cep_rs >= 1.60 else 0.45
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
                low_cep = float(candle.get("low", close_price)) if candle else close_price
                if close_price > float(ema_21) * 1.0135 or (close_price >= float(ema_21) * 1.004 and low_cep <= float(ema_9) * 1.002 and low_cep > float(ema_21) * 1.006):
                    target_limit = min(proposed_rate, float(ema_9) * 1.0010)
                else:
                    target_limit = min(proposed_rate, float(ema_21) * 1.0020)
            else:
                rv_cep_s = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
                if rv_cep_s >= 1.65 and close_price < float(ema_21) * 0.989:
                    target_limit = max(proposed_rate, float(ema_9) * 0.9990)
                else:
                    target_limit = max(proposed_rate, float(ema_21) * 0.9980)

        if target_limit <= 0 or np.isnan(target_limit):
            target_limit = proposed_rate if proposed_rate > 0 else float(ema_21)

        # Mandatory >= 10 bps passive maker buffer + [0.9885, 1.0095] clamp
        if proposed_rate > 0 and np.isfinite(proposed_rate):
            if not is_short:
                lo_b = 0.9885 * float(proposed_rate)
                hi_b = max(lo_b, min(float(proposed_rate), close_price) * 0.9990)
                target_limit = float(np.clip(target_limit, lo_b, hi_b))
            else:
                hi_b = 1.0095 * float(proposed_rate)
                lo_b = min(hi_b, max(float(proposed_rate), close_price) * 1.0010)
                target_limit = float(np.clip(target_limit, lo_b, hi_b))

        return float(target_limit)

    def min_roi_reached(self, trade: Trade, current_profit: float, current_time: datetime) -> bool:
        if (not self.ABLATE_LIFECYCLE_ENGINE) and int(getattr(trade, "nr_of_successful_exits", 0) or 0) >= 1:
            trade_dur_min = (current_time - trade.open_date_utc).total_seconds() / 60.0
            if current_profit >= 0.450:
                return True
            if trade_dur_min >= 4320.0 and current_profit >= 0.015:
                return True
            return False
        return super().min_roi_reached(trade=trade, current_profit=current_profit, current_time=current_time)

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None | tuple[float | None, str | None]:
        if getattr(trade, "has_open_orders", False):
            return None
        if self.ABLATE_LIFECYCLE_ENGINE:
            return None
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if trade_duration < 1.0:
            return None

        pair = trade.pair
        is_short = bool(trade.is_short)
        entry_tag = str(getattr(trade, "enter_tag", "") or "")
        candle = self._get_candle_info(pair, current_time)
        is_b_aj, is_lv_aj, _ = self._tier_flags(pair, candle)
        if is_lv_aj or is_b_aj or "range_sweep" in entry_tag:
            return None
        if not candle:
            return None

        close_t1 = float(candle.get("close", current_rate))
        ema_9 = float(candle.get("ema_9", close_t1))
        ema_21 = float(candle.get("ema_21", close_t1))
        rsi = float(candle.get("rsi", 50.0))
        adx_4h = float(candle.get("adx_4h", 25.0))
        adx_rising_4h = bool(candle.get("adx_rising_4h", False))
        cur_stake = float(trade.stake_amount or 0.0)
        if cur_stake <= 0.0:
            return None

        n_exits = int(getattr(trade, "nr_of_successful_exits", 0) or 0)
        n_entries = int(getattr(trade, "nr_of_successful_entries", 1) or 1)

        if (
            self.ENABLE_PYRAMID
            and n_exits == 0
            and n_entries == 1
            and 2.0 <= trade_duration <= 6.0
            and 0.030 <= current_profit <= 0.041
            and entry_tag in ("long_breakout_expansion", "long_squeeze_expansion", "long_rs_momentum")
            and adx_rising_4h
            and adx_4h >= 24.0
        ):
            pyr_aligned = (
                (close_t1 > ema_9 and ema_9 > ema_21 and rsi < 68.0)
                if not is_short
                else (close_t1 < ema_9 and ema_9 < ema_21 and rsi > 32.0)
            )
            if pyr_aligned:
                add_stake = cur_stake * 0.35
                is_unlim_pyr = isinstance(self.config, dict) and self.config.get("stake_amount") == "unlimited"
                if not is_unlim_pyr:
                    raw_cfg_s = self.config.get("stake_amount", 250.0) if isinstance(self.config, dict) else 250.0
                    cfg_s = float(raw_cfg_s) if isinstance(raw_cfg_s, (int, float)) else 250.0
                    add_stake = min(add_stake, max(0.0, 2.24 * cfg_s - cur_stake))
                min_s = float(min_stake or 2.0)
                avail_add = float(max_stake or 0.0)
                lev = float(getattr(trade, "leverage", 3.0) or 3.0)
                if add_stake >= min_s * 1.15 and (add_stake * lev) >= 5.5 and avail_add >= add_stake * 1.15:
                    return float(add_stake), "pyramid_trend_add"

        return self._eval_scaleout_plan(
            pair=pair,
            trade=trade,
            current_time=current_time,
            current_rate=current_rate,
            current_profit=current_profit,
            candle=candle,
            min_stake=min_stake,
            max_stake=max_stake,
        )

    def _eval_scaleout_plan(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        candle: Dict[str, Any] | None,
        min_stake: float | None = None,
        max_stake: float | None = None,
    ) -> tuple[float, str] | None:
        if self.ABLATE_LIFECYCLE_ENGINE or not candle:
            return None
        if int(getattr(trade, "nr_of_successful_exits", 0) or 0) >= 1:
            return None
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if trade_duration < 1.0:
            return None
        entry_tag = str(getattr(trade, "enter_tag", "") or "")
        rv_sc = float(candle.get("rel_vol_672", 1.5))
        bc3_sc = float(candle.get("btc_corr_336", 0.8))
        bc6_sc = float(candle.get("btc_corr_672", 0.8))
        am_sc = float(candle.get("atr_pct_med672", 0.01))
        if rv_sc < 0.72 or (bc3_sc >= 0.97 and rv_sc <= 1.15) or "range_sweep" in entry_tag:
            return None
        cur_stake = float(trade.stake_amount or 0.0)
        if cur_stake <= 0.0 or current_rate <= 0.0:
            return None

        is_short = bool(trade.is_short)
        close_t1 = float(candle.get("close", current_rate))
        ema_9 = float(candle.get("ema_9", close_t1))
        ema_21 = float(candle.get("ema_21", close_t1))
        bb_upper = float(candle.get("bb_upperband", close_t1 * 1.02))
        rsi = float(candle.get("rsi", 50.0))
        adx_4h = float(candle.get("adx_4h", 25.0))

        trigger_tag = None
        target_frac = 0.60
        if not is_short and rsi >= 71.0 and close_t1 > bb_upper and current_profit >= 0.048 and adx_4h >= 23.0:
            trigger_tag = "partial_bb_walk_tp1"
            target_frac = 0.62
        elif am_sc >= 0.0145 and bc6_sc < 0.70:
            aligned_hype = (close_t1 > ema_21 and rsi < 74.0) if not is_short else (close_t1 < ema_21 and rsi > 26.0)
            if trade_duration >= 6.0 and current_profit >= 0.045 and aligned_hype:
                trigger_tag = "partial_hype_tp1"
                target_frac = 0.58
        elif not is_short and "pullback" in entry_tag and trade_duration >= 10.0 and current_profit >= 0.042:
            if close_t1 > ema_21 and rsi < 72.0 and adx_4h >= 21.0:
                trigger_tag = "partial_pb10h_tp1"
                target_frac = 0.60
        elif entry_tag == "short_squeeze_expansion" and current_profit >= 0.040:
            if close_t1 < ema_21 and rsi > 28.0:
                trigger_tag = "partial_short_sq_tp1"
                target_frac = 0.58
        elif not is_short and entry_tag in ("long_breakout_expansion", "long_squeeze_expansion", "long_rs_momentum") and current_profit >= 0.043:
            if adx_4h >= 27.0 and close_t1 > ema_9 and ema_9 > ema_21 and rsi < 70.0:
                trigger_tag = "partial_breakout_tp1"
                target_frac = 0.60
        elif is_short and entry_tag == "short_bb_fade" and trade_duration >= 2.0 and current_profit >= 0.036:
            _sc_chop = (float(candle.get("slowk", 50.0)) < 21.5) and (float(candle.get("er_24", 0.10)) < 0.025) and (float(candle.get("cs_bear_breadth", 0.0)) < 0.05)
            if (not _sc_chop) and close_t1 < ema_21 and rsi > 32.0:
                trigger_tag = "partial_short_bb_fade_tp1"
                target_frac = 0.62

        if trigger_tag is None:
            return None

        min_s = float(min_stake) if (min_stake is not None and min_stake > 0.0) else 2.0
        min_rem_margin = max(2.5, min_s * 1.25)
        is_unlimited = isinstance(self.config, dict) and self.config.get("stake_amount") == "unlimited"
        if is_unlimited:
            open_trades = Trade.get_open_trades() or []
            tied_up = sum(float(getattr(t, "stake_amount", 0.0) or 0.0) for t in open_trades)
            if max_stake is not None and float(max_stake) > 0.0:
                est_wallet = (tied_up + float(max_stake)) / 0.95
                self._last_known_wallet = max(tied_up / 0.95, est_wallet)
                inst_rem_floor = max(min_rem_margin, 0.065 * est_wallet)
            else:
                est_wallet = max(tied_up / 0.95, float(getattr(self, "_last_known_wallet", 1000.0) or 1000.0))
                inst_rem_floor = max(min_rem_margin, 0.065 * est_wallet)
        else:
            raw_cfg_stake = self.config.get("stake_amount", 250.0) if isinstance(self.config, dict) else 250.0
            cfg_stake = float(raw_cfg_stake) if isinstance(raw_cfg_stake, (int, float)) else 250.0
            inst_rem_floor = max(min_rem_margin, 0.36 * cfg_stake)

        max_allowed_frac = 1.0 - (inst_rem_floor / cur_stake)
        if max_allowed_frac < 0.52:
            return None

        scale_mult = float(self.KNOB_SCALEOUT_FRAC) / 0.60
        scaled_target = float(np.clip(target_frac * scale_mult, 0.52, 0.68))
        frac = float(min(scaled_target, max_allowed_frac))

        raw_lev = getattr(trade, "leverage", None)
        lev = float(raw_lev) if isinstance(raw_lev, (int, float)) and raw_lev > 0 else 3.0
        tot_amt = float(getattr(trade, "amount", 0.0) or 0.0)
        pe_notional = tot_amt * frac * float(current_rate)
        rem_notional = tot_amt * (1.0 - frac) * float(current_rate)
        pe_margin = pe_notional / max(1.0, lev)
        rem_margin = rem_notional / max(1.0, lev)
        if pe_notional < max(5.5, min_s * 1.10) or pe_margin < min_s * 1.10:
            return None
        if rem_notional < max(6.0, min_s * 1.20) or rem_margin < min_s * 1.20:
            return None

        return -float(cur_stake * frac), trigger_tag

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
        is_b_ce, is_lv_ce, _is_hv_sz_ce = self._tier_flags(pair, candle)
        rv_ce_f = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
        am_ce_f = float(candle.get("atr_pct_med672", 0.010)) if candle else 0.010
        bc6_ce_f = float(candle.get("btc_corr_672", 0.80)) if candle else 0.80
        is_hv_ce = (not is_b_ce) and (not is_lv_ce) and (rv_ce_f >= 2.15) and (am_ce_f >= 0.0125) and (bc6_ce_f < 0.69)
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
        _, mae_spot = self._get_causal_mae_profit(
            pair, trade, current_time, current_rate, candle
        )
        cum_atr = self._get_causal_cum_atr(pair, trade, current_time, candle, trade_duration)
        atr_med_t1 = max(0.005, float(candle.get("atr_pct_med168", 0.012) or 0.012)) if candle else 0.012
        am672_t1 = max(0.005, float(candle.get("atr_pct_med672", atr_med_t1) or atr_med_t1)) if candle else atr_med_t1
        er12_t1 = float(candle.get("er_12", 0.25)) if candle else 0.25
        ser12_t1 = float(candle.get("ser_12", 0.0)) if candle else 0.0
        use_im = not self.ABLATE_INTRINSIC_MICRO

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

        bb_upper_t1 = float(candle.get("bb_upperband", close_t1 * 1.02)) if candle else (close_t1 * 1.02)
        bb_mid_t1 = float(candle.get("bb_middleband", ema_21)) if candle else ema_21
        entry_tag = getattr(trade, "enter_tag", "") or ""

        # Stage-2 Free-Roll Macro Runner Lifecycle (Smooth Continuous MFE Ratchet + EMA9/EMA21 Trail)
        if (not self.ABLATE_LIFECYCLE_ENGINE) and int(getattr(trade, "nr_of_successful_exits", 0) or 0) >= 1:
            runner_floor = 0.0155
            if peak_profit >= 0.160:
                runner_floor = max(runner_floor, peak_profit * 0.68)
            elif peak_profit >= 0.100:
                runner_floor = max(runner_floor, peak_profit * 0.58)
            elif peak_profit >= 0.055:
                runner_floor = max(runner_floor, peak_profit - 0.030)

            if current_profit <= runner_floor:
                return "runner_freeroll_floor_tp"

            if not is_short:
                climax_ema9_break = (peak_profit >= 0.085) and (close_t1 < ema_9) and (current_profit >= 0.045)
                ema21_break = (close_t1 < ema_21) and (rsi < 55.0) and (current_profit >= 0.015)
                chand_hit = (peak_profit >= 0.100) and (close_t1 <= peak_rate - 1.35 * current_atr) and (rsi < 64.0)
                if climax_ema9_break or ema21_break or chand_hit:
                    return "runner_structure_trail_tp"
            else:
                climax_ema9_break = (peak_profit >= 0.085) and (close_t1 > ema_9) and (current_profit >= 0.045)
                ema21_break = (close_t1 > ema_21) and (rsi > 45.0) and (current_profit >= 0.015)
                chand_hit = (peak_profit >= 0.100) and (close_t1 >= peak_rate + 1.35 * current_atr) and (rsi > 36.0)
                if climax_ema9_break or ema21_break or chand_hit:
                    return "runner_structure_trail_tp"

            if (not is_hv_ce) and trade_duration >= 20.0 and peak_profit < 0.060 and current_profit < 0.028 and adx_4h < 22.0:
                return "runner_stagnant_recycle_tp"
            max_runner_hours = 96.0 if is_hv_ce else 84.0
            if trade_duration >= max_runner_hours:
                return "runner_macro_time_tp"
            return None

        _sbb_prof = is_short and (entry_tag == "short_bb_fade") and (current_profit >= 0.040)
        _sbb_chop = bool(candle) and (slowk < 21.5) and (float(candle.get("er_24", 0.10)) < 0.025) and (float(candle.get("cs_bear_breadth", 0.0)) < 0.05)
        if _sbb_prof and _sbb_chop:
            return "short_fade_chop_oversold_tp"

        # Live FreqtradeBot.process parity: defer unscaled custom_exit ONLY when adjust_trade_position WILL actually execute partial scale-out
        if (
            (not self.ABLATE_LIFECYCLE_ENGINE)
            and (not getattr(trade, "has_open_orders", False))
            and (not is_lv_ce) and (not is_b_ce) and ("range_sweep" not in entry_tag)
            and int(getattr(trade, "nr_of_successful_exits", 0) or 0) == 0
            and current_profit >= 0.034
        ):
            if self._eval_scaleout_plan(pair, trade, current_time, current_rate, current_profit, candle) is not None:
                return None

        # 0A. Orthogonal Range Liquidity-Sweep Mean-Reversion Lifecycle
        if entry_tag in ("range_sweep_alpha", "long_range_sweep_alpha", "short_range_sweep_alpha") and (not is_lv_ce):
            range_tp = float(self.KNOB_RANGE_TP_ROE)
            if current_profit >= range_tp:
                return "range_sweep_tp"
            reclaimed_mid = (close_t1 >= bb_mid_t1) if not is_short else (close_t1 <= bb_mid_t1)
            if trade_duration >= 4.0 and reclaimed_mid and current_profit >= 0.0280:
                return "range_bb_mid_tp"
            if not is_short:
                if (trade_duration >= 3.0 and spot_profit <= -0.0260 and close_t1 < ema_200) or (spot_profit <= -0.0360):
                    return "range_long_struct_stop"
                if trade_duration >= 12.0 and spot_profit <= -0.0165 and close_t1 < ema_21 and rsi < 45.0:
                    return "range_long_12h_prune"

        # Unscaled Long RSI Overbought BB TP fallback + Lower-BB Mean-Reversion Quick TP
        if not is_short and rsi >= 71.0 and close_t1 > bb_upper_t1 and current_profit > 0.002:
            return "rsi_overbought_tp"
        if not is_short and entry_tag == "long_bb_pullback" and trade_duration >= 2.0 and current_profit >= 0.040 and close_t1 > ema_9:
            return "bb_pullback_reversion_tp"

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
            exp_tp_sq = 0.0270 if (is_b_ce and not is_short) else float(self.KNOB_BREAKOUT_TP_ROE)
            if current_profit >= exp_tp_sq:
                return "breakout_expansion_tp"
            mom_failed_sq = (close_t1 < ema_21) and (rsi < 48.0)
            if trade_duration >= 4.0 and ((spot_profit <= -0.0110 and mom_failed_sq) or (spot_profit <= -0.0185)):
                return "breakout_rapid_invalidation_4h"
            if trade_duration >= 15.0 and current_profit < -0.0050 and close_t1 < ema_21:
                return "breakout_14h_timeout"

        # 0B-3. High-PF RS-Momentum Lifecycle (SOL, LINK, ADA, ETH)
        elif entry_tag in ("long_breakout_expansion", "long_rs_momentum", "long_squeeze_expansion"):
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
                trade_duration >= 3.0
                and peak_profit >= 0.024
                and 0.003 <= current_profit < 0.032
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
            if "fade" in entry_tag or entry_tag in ("short_range_sweep_alpha", "range_sweep_alpha"):
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

        # 1B. BTC 4.0x Long Spot-ROI Parity & 3.0x Bear Short Exhaustion Lifecycle
        if is_b_ce:
            if not is_short:
                if (
                    (trade_duration >= 3.00 and current_profit >= 0.1200)
                    or (trade_duration >= 7.78 and current_profit >= 0.1033)
                    or (trade_duration >= 11.47 and current_profit >= 0.0613)
                    or (trade_duration >= 19.55 and current_profit >= 0.0068)
                ):
                    return "btc_spot_roi_parity"
            elif use_im and entry_tag == "short_bb_fade":
                embedded_btc_dump = (close_t1 < ema_21) and (rsi > 34.0) and (adx_4h >= 26.0) and (slowk < 36.0)
                if trade_duration >= 2.0 and current_profit >= 0.0260 and not embedded_btc_dump:
                    return "btc_short_exhaustion_tp"
                if trade_duration >= 10.0 and current_profit >= 0.0110:
                    return "btc_short_exhaustion_tp"

        # 2. De-Compressed PAXG Scalp + Positive-ROE Slot Recycler
        if is_lv_ce:
            if trade_duration >= 2.0:
                paxg_exhausted = (rsi >= 57.5) if not is_short else (rsi <= 42.5)
                if current_profit >= 0.0110 or (trade_duration >= 4.0 and current_profit >= 0.0072 and paxg_exhausted):
                    return "paxg_volatility_tp"
            if trade_duration >= 8.0 and current_profit >= 0.0048:
                return "paxg_slot_recycle_tp"

        # 3. De-Compressed Profit Banking for Moderate-Vol Large Alts + 10.0h Runner-Exempt Pullback Stall Bank
        rv_ce = float(candle.get("rel_vol_672", 1.5)) if candle else 1.5
        bc_ce = float(candle.get("btc_corr_336", 0.8)) if candle else 0.8
        if (0.72 <= rv_ce < 1.50 and bc_ce < 0.97) and trade_duration >= 16.0:
            eth_fading = (rsi < 50.0) if not is_short else (rsi > 50.0)
            if (eth_fading and current_profit >= 0.0080) or (trade_duration >= 18.0 and current_profit >= 0.0180):
                return "eth_profit_bank_tp"

        if (
            not is_short
            and ("pullback" in entry_tag or entry_tag in ("long_range_sweep_alpha", "range_sweep_alpha"))
            and (not is_lv_ce) and (not is_b_ce) and (not is_hv_ce)
            and trade_duration >= 10.0
            and current_profit >= float(self.KNOB_PULLBACK_STALL_ROE)
        ):
            _trend_runner_ok = (adx_4h >= 28.0) and (close_t1 > ema_9) and (slowk > 65.0) and (rsi < 68.0)
            _er_runner_ok = use_im and (er12_t1 >= 0.45) and (ser12_t1 >= 0.45) and (close_t1 > ema_9) and (rsi < 69.0)
            embedded_bull_runner = _trend_runner_ok or _er_runner_ok
            if not embedded_bull_runner:
                return "pullback_10h_stall_tp"

        # 4. Tailored Exits for High-Vol Decoupled Expansion Alts
        if is_hv_ce:
            if current_profit >= 0.045 and trade_duration >= 6.0:
                return "hype_quick_tp"
            if entry_tag == "long_bb_pullback":
                if trade_duration >= 18.0 and spot_profit <= -0.028 and close_t1 < ema_200 and rsi < 40.0:
                    return "adaptive_stale_prune_18h"
                if trade_duration >= 24.0 and spot_profit <= -0.024 and close_t1 < ema_21:
                    return "adaptive_stale_prune_24h"
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

        if (not is_b_ce) and (not is_lv_ce) and (not is_hv_ce) and trade_duration >= 10.0 and peak_profit >= 0.028 and 0.004 <= current_profit <= 0.014:
            _brk_mfe = (close_t1 > ema_21 and rsi > 55.0) if is_short else (close_t1 < ema_21 and rsi < 45.0)
            if _brk_mfe:
                return "pullback_10h_stall_tp"

        if current_profit >= 0.035:
            return None

        # 6. Crisp Adaptive Adaptive Stale Pruning (18h -2.20%/-2.70% spot + 8h EMA200 Collapse Guard)
        if (not is_lv_ce) and (not is_hv_ce) and trade_duration >= 6.0 and spot_profit <= -0.0680:
            cat_brk = (close_t1 < ema_200 and rsi < 38.0) if not is_short else (close_t1 > ema_200 and rsi > 62.0)
            if cat_brk:
                return "adaptive_stale_prune_18h"
        if is_lv_ce:
            failing_paxg = (rsi < 45.0 or close_t1 < ema_21) if not is_short else (rsi > 55.0 or close_t1 > ema_21)
            if trade_duration >= 16.0 and spot_profit <= -0.012 and failing_paxg:
                return "paxg_early_stale_prune"
        elif (not is_hv_ce) and "breakdown" not in entry_tag and entry_tag != "short_squeeze_expansion":
            if use_im:
                intrinsic_hours = cum_atr / atr_med_t1
                if (
                    trade_duration >= 12.0
                    and intrinsic_hours >= 18.5
                    and spot_profit <= -0.0285
                    and (
                        (close_t1 < ema_50 and rsi < 38.0 and ser12_t1 <= -0.45)
                        if not is_short
                        else (close_t1 > ema_50 and rsi > 62.0 and ser12_t1 >= 0.45)
                    )
                ):
                    return "adaptive_stale_prune_18h"

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
            eff_threshold = (-0.0220 if ("pullback" in entry_tag or entry_tag == "long_range_sweep_alpha" or (entry_tag == "range_sweep_alpha" and not is_short)) else -0.0270) * prune_scale
            if trade_duration >= 18.0 and spot_profit <= eff_threshold and failing_18h:
                return "adaptive_stale_prune_18h"
            if trade_duration >= 24.0 and spot_profit <= (-0.0200 * prune_scale):
                return "adaptive_stale_prune_24h"

        if not is_hv_ce:
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

    def check_exit_timeout(self, pair: str, trade: Trade, order: Any, current_time: datetime, **kwargs) -> bool:
        order_date = order.get('order_date_utc') or order.get('order_date') if isinstance(order, dict) else (getattr(order, 'order_date_utc', None) or getattr(order, 'order_date', None))
        if order_date:
            if isinstance(order_date, str):
                try:
                    order_date = datetime.fromisoformat(order_date.replace('Z', '+00:00'))
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
                if (current_time - order_date).total_seconds() / 60.0 > 15.0:
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

        if (fr_df is None or fr_df.empty) and self._is_backtest_mode():
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
        merged_ok = False

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
                    tolerance=pd.Timedelta("12h"),
                ).sort_values("_orig_idx")

                dataframe["funding_rate"] = merged["fr_val"].fillna(default_fr).to_numpy()
                dataframe["funding_mean_24h"] = merged["fr_mean_3"].fillna(default_fr).to_numpy()
                merged_ok = True

        if not merged_ok and hasattr(self, "dp") and self.dp:
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

        fr_series = dataframe["funding_rate"]
        fr_mean_168 = fr_series.rolling(168, min_periods=24).mean()
        fr_std_168 = fr_series.rolling(168, min_periods=24).std().clip(lower=0.000030)
        dataframe["fr_zscore_168"] = ((fr_series - fr_mean_168) / fr_std_168).fillna(0.0)

        fr_mean_336 = fr_series.rolling(336, min_periods=48).mean()
        fr_std_336 = fr_series.rolling(336, min_periods=48).std().clip(lower=0.000030)
        dataframe["fr_zscore_336"] = ((fr_series - fr_mean_336) / fr_std_336).fillna(0.0)

        dataframe["fr_ema_24"] = fr_series.ewm(span=24, adjust=False).mean().fillna(default_fr)
        dataframe["fr_impulse_24h"] = (fr_series - dataframe["fr_ema_24"]).fillna(0.0)
        return dataframe


    def _load_causal_funding_array(self, target_pair: str, dt_ns: pd.Series, max_dt_utc: pd.Timestamp) -> np.ndarray:
        fr_df = None
        if hasattr(self, "dp") and self.dp:
            try:
                fr_df = self.dp.get_pair_dataframe(
                    pair=target_pair, timeframe="1h", candle_type=CandleType.FUNDING_RATE
                )
            except Exception:
                fr_df = None

        if (fr_df is None or fr_df.empty) and self._is_backtest_mode():
            pair_slug = target_pair.replace("/", "_").replace(":", "_")
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
                clean_fr = clean_fr[clean_fr["date_avail"] <= max_dt_utc]
                if not clean_fr.empty:
                    clean_fr = clean_fr.drop_duplicates(subset=["date_avail"], keep="last").sort_values("date_avail")
                    left_dates = pd.DataFrame(
                        {
                            "_orig_idx": np.arange(len(dt_ns)),
                            "_dt_ns": dt_ns,
                        }
                    ).sort_values("_dt_ns")
                    merged = pd.merge_asof(
                        left_dates,
                        clean_fr[["date_avail", "fr_val"]],
                        left_on="_dt_ns",
                        right_on="date_avail",
                        direction="backward",
                        tolerance=pd.Timedelta("12h"),
                    ).sort_values("_orig_idx")
                    return merged["fr_val"].fillna(default_fr).to_numpy(dtype=float)
        return np.full(len(dt_ns), default_fr, dtype=float)

    def _get_peer_1h_causal(self, peer: str, p_df: DataFrame, p_df4: DataFrame | None, max_dt_utc: pd.Timestamp) -> DataFrame:
        if not hasattr(self, "_peer_1h_cache"):
            self._peer_1h_cache = {}
        cols_p = ["date", "close", "high", "low"] if ("high" in p_df.columns and "low" in p_df.columns) else ["date", "close"]
        wdf = p_df[cols_p].copy()
        wdf["_dt_utc"] = pd.to_datetime(wdf["date"], utc=True)
        wdf = wdf[wdf["_dt_utc"] <= max_dt_utc]
        if wdf.empty:
            return pd.DataFrame()
        if not wdf["_dt_utc"].is_monotonic_increasing:
            wdf = wdf.sort_values("_dt_utc").reset_index(drop=True)

        last_dt = wdf["_dt_utc"].iloc[-1]
        ckey = (peer, len(wdf), last_dt)
        cached = self._peer_1h_cache.get(ckey)
        if cached is not None:
            return cached

        dt_ns = wdf["_dt_utc"].astype("datetime64[ns, UTC]")
        c = wdf["close"].to_numpy(dtype=float)
        ema_21 = talib.EMA(c, timeperiod=21)
        ema_50 = talib.EMA(c, timeperiod=50)
        is_crypto_peer = np.ones(len(c), dtype=bool)
        if "BTC" not in peer and "high" in wdf.columns and "low" in wdf.columns:
            _p_atr = talib.ATR(wdf["high"].to_numpy(dtype=float), wdf["low"].to_numpy(dtype=float), c, timeperiod=14)
            _p_ap = pd.Series(_p_atr / np.where(c > 0.0, c, np.nan)).rolling(672, min_periods=24).median().fillna(0.010).to_numpy(dtype=float)
            is_crypto_peer = (_p_ap >= 0.0050)
        valid_50 = (~np.isnan(ema_50)) & is_crypto_peer

        bull_1h = np.where(valid_50, ((c > ema_50) & (ema_21 > ema_50)).astype(float), np.nan)
        bear_1h = np.where(valid_50, ((c < ema_50) & (ema_21 < ema_50)).astype(float), np.nan)

        bull_4h = bull_1h.copy()
        bear_4h = bear_1h.copy()
        if p_df4 is not None and not p_df4.empty and "date" in p_df4.columns:
            w4 = p_df4[["date", "close", "high", "low"]].copy()
            w4["_dt4_utc"] = pd.to_datetime(w4["date"], utc=True)
            w4["date_avail_1h"] = (w4["_dt4_utc"] + pd.Timedelta(hours=3)).astype("datetime64[ns, UTC]")
            w4 = w4[w4["date_avail_1h"] <= max_dt_utc].sort_values("date_avail_1h").reset_index(drop=True)
            if len(w4) >= 55:
                c4 = w4["close"].to_numpy(dtype=float)
                e50_4 = talib.EMA(c4, timeperiod=50)
                e200_4 = talib.EMA(c4, timeperiod=200)
                adx_4 = talib.ADX(w4["high"].to_numpy(dtype=float), w4["low"].to_numpy(dtype=float), c4, timeperiod=14)
                v4 = ~np.isnan(adx_4) & ~np.isnan(e200_4)
                w4["mbull_4h"] = np.where(v4, ((c4 > e50_4) & (e50_4 > e200_4)).astype(float), np.nan)
                w4["mbear_4h"] = np.where(v4, ((c4 < e50_4) & (e50_4 < e200_4)).astype(float), np.nan)
                left1 = pd.DataFrame({"_i": np.arange(len(dt_ns)), "_dt": dt_ns}).sort_values("_dt")
                m4 = pd.merge_asof(
                    left1,
                    w4[["date_avail_1h", "mbull_4h", "mbear_4h"]],
                    left_on="_dt",
                    right_on="date_avail_1h",
                    direction="backward",
                ).sort_values("_i")
                bull_4h = np.where(is_crypto_peer, m4["mbull_4h"].to_numpy(dtype=float), np.nan)
                bear_4h = np.where(is_crypto_peer, m4["mbear_4h"].to_numpy(dtype=float), np.nan)

        s_c = pd.Series(c)
        ret_12 = (s_c / s_c.shift(12) - 1.0).to_numpy(dtype=float)
        ret_24 = (s_c / s_c.shift(24) - 1.0).to_numpy(dtype=float)
        mom_comp = np.where(valid_50, 0.5 * ret_12 + 0.5 * ret_24, np.nan)

        tot12 = s_c.diff().abs().rolling(12).sum().replace(0.0, np.nan)
        er_12 = np.where(valid_50, (s_c.diff(12).abs() / tot12).fillna(0.5).to_numpy(dtype=float), np.nan)
        fr_arr = self._load_causal_funding_array(peer, dt_ns, max_dt_utc)
        fr_m = np.where(valid_50, fr_arr, np.nan)

        out = pd.DataFrame(
            {
                "bull_1h": bull_1h,
                "bear_1h": bear_1h,
                "bull_4h": bull_4h,
                "bear_4h": bear_4h,
                "mom_comp": mom_comp,
                "er_12": er_12,
                "fr": fr_m,
            },
            index=pd.Index(dt_ns, name="_dt_ns"),
        )
        out = out[~out.index.duplicated(keep="last")]
        self._peer_1h_cache[ckey] = out
        return out

    def _attach_cross_sectional_tensor(self, dataframe: DataFrame, pair: str) -> DataFrame:
        dataframe["dir_er_12"] = dataframe["ser_12"]
        dataframe["cs_bull_breadth"] = 0.50
        dataframe["cs_bear_breadth"] = 0.25
        dataframe["cs_bull_4h"] = 0.50
        dataframe["cs_bear_4h"] = 0.25
        dataframe["cs_er12_mean"] = 0.30
        dataframe["cs_mom_rank"] = 3.5
        dataframe["cs_fr_rank"] = 4.0
        dataframe["cs_active_n"] = 7.0
        dataframe["cs_median_fr"] = dataframe["funding_rate"]
        dataframe["fr_div"] = 0.0

        if dataframe.empty or not (hasattr(self, "dp") and self.dp):
            return dataframe

        max_dt_utc = pd.to_datetime(dataframe["date"], utc=True).max()

        try:
            wl = list(self.dp.current_whitelist() or [])
        except Exception:
            wl = []
        if not wl:
            wl = [pair, "BTC/USDT:USDT"]
        crypto_peers = list(wl)
        if not crypto_peers:
            return dataframe

        peer_frames: Dict[str, DataFrame] = {}
        sig_parts = []
        for p in crypto_peers:
            try:
                pdf = self.dp.get_pair_dataframe(pair=p, timeframe=self.timeframe)
                pdf4 = self.dp.get_pair_dataframe(pair=p, timeframe=self.informative_timeframe)
            except Exception:
                pdf = None
                pdf4 = None
            if pdf is not None and not pdf.empty and "date" in pdf.columns:
                p_feat = self._get_peer_1h_causal(p, pdf, pdf4, max_dt_utc)
                if not p_feat.empty:
                    peer_frames[p] = p_feat
                    sig_parts.append((p, len(p_feat), p_feat.index[-1]))

        if len(peer_frames) < 3:
            return dataframe

        if not hasattr(self, "_cs_tensor_cache"):
            self._cs_tensor_cache = {}
        t_key = tuple(sig_parts)
        cached_tensor = self._cs_tensor_cache.get(t_key)

        if cached_tensor is None:
            union_idx = peer_frames[next(iter(peer_frames))].index
            for pf in peer_frames.values():
                if not pf.index.equals(union_idx):
                    union_idx = union_idx.union(pf.index)
            union_idx = union_idx.sort_values()

            is_bt_cs = self._is_backtest_mode()
            def _al_cs(col: str) -> pd.DataFrame:
                if is_bt_cs:
                    return pd.DataFrame({p: pf[col].reindex(union_idx) for p, pf in peer_frames.items()})
                return pd.DataFrame({p: pf[col].reindex(union_idx).ffill(limit=1) for p, pf in peer_frames.items()})
            bull_mat = _al_cs("bull_1h")
            bear_mat = _al_cs("bear_1h")
            bull4_mat = _al_cs("bull_4h")
            bear4_mat = _al_cs("bear_4h")
            mom_mat = _al_cs("mom_comp")
            er12_mat = _al_cs("er_12")
            fr_mat = _al_cs("fr")

            summary_df = pd.DataFrame(
                {
                    "cs_active_n": bull_mat.notna().sum(axis=1).astype(float),
                    "cs_bull_breadth": bull_mat.mean(axis=1),
                    "cs_bear_breadth": bear_mat.mean(axis=1),
                    "cs_bull_4h": bull4_mat.mean(axis=1),
                    "cs_bear_4h": bear4_mat.mean(axis=1),
                    "cs_er12_mean": er12_mat.mean(axis=1),
                    "cs_median_fr": fr_mat.median(axis=1),
                },
                index=union_idx,
            )
            mom_ranks = mom_mat.rank(axis=1, ascending=False, method="min")
            fr_ranks = fr_mat.rank(axis=1, ascending=False, method="min")
            cached_tensor = {
                "summary": summary_df,
                "mom_ranks": mom_ranks,
                "fr_ranks": fr_ranks,
            }
            self._cs_tensor_cache[t_key] = cached_tensor

        dt_target = pd.Index(pd.to_datetime(dataframe["date"], utc=True).astype("datetime64[ns, UTC]"))
        aligned_sum = cached_tensor["summary"].reindex(dt_target)

        dataframe["cs_active_n"] = aligned_sum["cs_active_n"].fillna(6.0).to_numpy(dtype=float)
        dataframe["cs_bull_breadth"] = aligned_sum["cs_bull_breadth"].fillna(0.50).to_numpy(dtype=float)
        dataframe["cs_bear_breadth"] = aligned_sum["cs_bear_breadth"].fillna(0.25).to_numpy(dtype=float)
        dataframe["cs_bull_4h"] = aligned_sum["cs_bull_4h"].fillna(0.50).to_numpy(dtype=float)
        dataframe["cs_bear_4h"] = aligned_sum["cs_bear_4h"].fillna(0.25).to_numpy(dtype=float)
        dataframe["cs_er12_mean"] = aligned_sum["cs_er12_mean"].fillna(0.30).to_numpy(dtype=float)
        med_fr_arr = aligned_sum["cs_median_fr"].to_numpy(dtype=float)
        fr_cur = dataframe["funding_rate"].to_numpy(dtype=float)
        med_fr_clean = np.where(np.isnan(med_fr_arr), fr_cur, med_fr_arr)
        dataframe["cs_median_fr"] = med_fr_clean
        dataframe["fr_div"] = fr_cur - med_fr_clean

        if pair in cached_tensor["mom_ranks"].columns:
            mr = cached_tensor["mom_ranks"][pair].reindex(dt_target).fillna(3.5).to_numpy(dtype=float)
            frr = cached_tensor["fr_ranks"][pair].reindex(dt_target).fillna(4.0).to_numpy(dtype=float)
            dataframe["cs_mom_rank"] = mr
            dataframe["cs_fr_rank"] = frr
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
                btc_1h["btc_ema_168"] = ta.EMA(btc_1h, timeperiod=168)
                btc_1h["btc_ema168_slope24"] = ((btc_1h["btc_ema_168"] - btc_1h["btc_ema_168"].shift(24)) / btc_1h["btc_ema_168"].shift(24)).fillna(0.0)
                btc_1h["btc_rsi_1h"] = ta.RSI(btc_1h, timeperiod=14)
                btc_1h["btc_atr_pct"] = (ta.ATR(btc_1h, timeperiod=14) / btc_1h["close"].replace(0.0, np.nan)).fillna(0.0)
                btc_1h["btc_atr_med672"] = btc_1h["btc_atr_pct"].rolling(window=672, min_periods=168).median().fillna(0.0065)
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
        if f"close_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"close_{self.informative_timeframe}"] = dataframe["close"]
        if f"ema_50_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"ema_50_{self.informative_timeframe}"] = dataframe["close"]
        if f"ema_200_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"ema_200_{self.informative_timeframe}"] = dataframe["close"]
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
        if "btc_ema168_slope24_btc_1h" not in dataframe.columns:
            dataframe["btc_ema168_slope24_btc_1h"] = 0.0
        if "btc_atr_med672_btc_1h" not in dataframe.columns:
            dataframe["btc_atr_med672_btc_1h"] = dataframe["atr_pct"].rolling(window=672, min_periods=24).median().fillna(0.0065)

        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_168"] = ta.EMA(dataframe, timeperiod=168)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["ema168_slope24"] = ((dataframe["ema_168"] - dataframe["ema_168"].shift(24)) / dataframe["ema_168"].shift(24)).fillna(0.0)
        dc72_hi = dataframe["high"].rolling(window=72, min_periods=12).max()
        dc72_lo = dataframe["low"].rolling(window=72, min_periods=12).min()
        dataframe["dc72_pos"] = ((dataframe["close"] - dc72_lo) / (dc72_hi - dc72_lo + 1e-12)).fillna(0.5)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = (dataframe["atr"] / dataframe["close"].replace(0.0, np.nan)).fillna(0.0)
        dataframe["atr_pct_med168"] = dataframe["atr_pct"].rolling(window=168, min_periods=1).median().fillna(0.0)
        dataframe["atr_pct_med672"] = dataframe["atr_pct"].rolling(window=672, min_periods=24).median().fillna(0.010)
        dataframe["atr_q25_672"] = dataframe["atr_pct"].rolling(window=672, min_periods=24).quantile(0.25).fillna(0.008)
        dataframe["rel_vol_672"] = (dataframe["atr_pct_med672"] / dataframe["btc_atr_med672_btc_1h"].replace(0.0, np.nan).fillna(0.0065).clip(lower=0.0015)).fillna(1.0)
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
            dataframe["btc_corr_336"] = dataframe["ret_1h_log"].rolling(window=336, min_periods=72).corr(btc_ret_1h).fillna(0.0)
            dataframe["btc_corr_672"] = dataframe["ret_1h_log"].rolling(window=672, min_periods=168).corr(btc_ret_1h).fillna(0.0)
        else:
            dataframe["beta_btc"] = 1.0
            dataframe["delta_rs"] = 0.0
            dataframe["delta_rs_ema"] = 0.0
            dataframe["btc_corr_336"] = 1.0
            dataframe["btc_corr_672"] = 1.0

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
        abs_diff_1h = dataframe["close"].diff().abs()
        er12_denom = abs_diff_1h.rolling(12).sum().replace(0.0, np.nan)
        er24_denom = abs_diff_1h.rolling(24).sum().replace(0.0, np.nan)
        dataframe["er_12"] = ((dataframe["close"] - dataframe["close"].shift(12)).abs() / er12_denom).fillna(0.0)
        dataframe["er_24"] = ((dataframe["close"] - dataframe["close"].shift(24)).abs() / er24_denom).fillna(0.0)
        dataframe["ser_12"] = ((dataframe["close"] - dataframe["close"].shift(12)) / er12_denom).fillna(0.0)
        dataframe["taker_sell_volume_surge"] = (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.15)

        dataframe = self._merge_causal_funding(dataframe, pair)
        dataframe = self._attach_cross_sectional_tensor(dataframe, pair)

        dates = pd.to_datetime(dataframe["date"]).dt.to_pydatetime()
        adx_4h_col = dataframe[f"adx_{self.informative_timeframe}"]
        self._candle_arrays[pair] = {
            "close": dataframe["close"].to_numpy(dtype=float),
            "high": dataframe["high"].to_numpy(dtype=float),
            "low": dataframe["low"].to_numpy(dtype=float),
            "atr_pct": dataframe["atr_pct"].to_numpy(dtype=float),
        }
        records = [
            {
                "idx": idx,
                "open": float(op) if not np.isnan(op) else 0.0,
                "close": float(c) if not np.isnan(c) else 0.0,
                "high": float(hi) if not np.isnan(hi) else 0.0,
                "low": float(lo) if not np.isnan(lo) else 0.0,
                "atr": float(a) if not np.isnan(a) else 0.0,
                "atr_pct": float(ap) if not np.isnan(ap) else 0.0,
                "atr_pct_med168": float(apm) if not np.isnan(apm) else 0.0,
                "atr_pct_med672": float(apm672) if not np.isnan(apm672) else 0.010,
                "rel_vol_672": float(rv672) if not np.isnan(rv672) else 1.0,
                "btc_corr_336": float(bc336) if not np.isnan(bc336) else 0.8,
                "btc_corr_672": float(bc672) if not np.isnan(bc672) else 0.8,
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
                "bb_middleband": float(bbm) if not np.isnan(bbm) else float(c),
                "bb_upperband": float(bbu) if not np.isnan(bbu) else float(c) * 1.02,
                "bb_lowerband": float(bbl) if not np.isnan(bbl) else float(c) * 0.98,
                "er_12": float(e12) if not np.isnan(e12) else 0.30,
                "er_24": float(e24) if not np.isnan(e24) else 0.30,
                "adx_rising_4h": bool(axr),
                "fr_zscore_168": float(fz168) if not np.isnan(fz168) else 0.0,
                "ema168_slope24": float(s168) if not np.isnan(s168) else 0.0,
                "dc72_pos": float(dc72) if not np.isnan(dc72) else 0.5,
                "btc_ema168_slope24": float(bs168) if not np.isnan(bs168) else 0.0,
                "ser_12": float(ser12) if not np.isnan(ser12) else 0.0,
                "cs_bull_breadth": float(csb),
                "cs_bear_breadth": float(csbr),
                "cs_bull_4h": float(csb4),
                "cs_bear_4h": float(csbr4),
                "cs_er12_mean": float(cser),
                "cs_mom_rank": float(csmr),
                "cs_active_n": float(csan),
                "fr_div": float(frd),
            }
            for idx, (op, c, hi, lo, a, ap, apm, apm672, rv672, bc336, bc672, r, sk, sd, e9, e21, e50, e200, ax, mbull, mbear, bsb, fr, frm, v, vm, s, bbm, bbu, bbl, e12, e24, axr, fz168, s168, dc72, bs168, ser12, csb, csbr, csb4, csbr4, cser, csmr, csan, frd) in enumerate(
                zip(
                    dataframe["open"], dataframe["close"], dataframe["high"], dataframe["low"],
                    dataframe["atr"], dataframe["atr_pct"], dataframe["atr_pct_med168"],
                    dataframe["atr_pct_med672"], dataframe["rel_vol_672"], dataframe["btc_corr_336"], dataframe["btc_corr_672"],
                    dataframe["rsi"], dataframe["slowk"], dataframe["slowd"],
                    dataframe["ema_9"], dataframe["ema_21"], dataframe["ema_50"], dataframe["ema_200"],
                    adx_4h_col, dataframe[mbull_col], dataframe[mbear_col],
                    dataframe["btc_strong_bull_btc_4h"].fillna(True),
                    dataframe["funding_rate"], dataframe["funding_mean_24h"],
                    dataframe["volume"], dataframe["volume_mean_20"], dataframe["support_level_20"],
                    dataframe["bb_middleband"], dataframe["bb_upperband"], dataframe["bb_lowerband"],
                    dataframe["er_12"], dataframe["er_24"],
                    dataframe[f"adx_rising_{self.informative_timeframe}"].fillna(False),
                    dataframe["fr_zscore_168"], dataframe["ema168_slope24"],
                    dataframe["dc72_pos"], dataframe["btc_ema168_slope24_btc_1h"], dataframe["ser_12"],
                    dataframe["cs_bull_breadth"], dataframe["cs_bear_breadth"],
                    dataframe["cs_bull_4h"], dataframe["cs_bear_4h"],
                    dataframe["cs_er12_mean"], dataframe["cs_mom_rank"], dataframe["cs_active_n"], dataframe["fr_div"],
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
        g_thr = float(getattr(self, "KNOB_GLOBAL_THRESH_SCALE", 1.0) or 1.0)
        if abs(g_thr - 1.0) > 1e-6:
            dataframe = dataframe.copy()
            dataframe["fr_zscore_168"] = dataframe["fr_zscore_168"] / g_thr
            dataframe["fr_zscore_336"] = dataframe["fr_zscore_336"] / g_thr

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

        # Pair-Calibrated Perpetual Funding Term-Structure Regimes (7d/14d Z-Scores)
        adx_4h_raw = dataframe[f"adx_{self.informative_timeframe}"]
        adx_4h_adx = adx_4h_raw / float(self.KNOB_ADX_GATE_SCALE)
        adx_4h_s = adx_4h_adx / g_thr
        fz168 = dataframe["fr_zscore_168"]
        fz336 = dataframe["fr_zscore_336"]
        fr_ema24 = dataframe["fr_ema_24"]
        use_zscore = (not self.ABLATE_ZSCORE_COHERENCE) and (not self.ABLATE_FUNDING_REGIME)

        regime_euphoria_long = ((fz168 > 2.20) & (dataframe["funding_rate"] > 0.00025)) if use_zscore else pd.Series(False, index=dataframe.index)
        cooling_bull_contango = (
            (dataframe["funding_rate"] > 0.00045)
            & (dataframe["funding_rate"] <= 0.00120)
            & (fz168 <= 0.30)
            & (dataframe["ema168_slope24"] >= 0.008)
        ) if use_zscore else pd.Series(False, index=dataframe.index)

        rv_u = dataframe["rel_vol_672"]
        bc_u = dataframe["btc_corr_336"]
        bc672_u = dataframe["btc_corr_672"]
        is_gold_u = rv_u < 0.72
        is_bench_u = (bc_u >= 0.97) & (rv_u >= 0.72)
        is_ultra_u = (rv_u >= 2.20) & (bc672_u < 0.68)
        is_hiatr_u = dataframe["atr_pct_med672"] >= 0.0145
        is_alt_u = (~is_gold_u) & (~is_bench_u)
        fr_cap_alt = np.where((adx_4h_s >= 23.0) & (~is_ultra_u), 0.00080, 0.00054)
        bench_fr_ok = (dataframe["funding_rate"] > 0.0) & (dataframe["funding_rate"] <= 0.00050)
        gold_fr_ok = (dataframe["funding_rate"] >= -0.00004) & (dataframe["funding_rate"] <= 0.00072)
        alt_fr_ok_regime = (((dataframe["funding_rate"] > 0.0) | (is_ultra_u & (dataframe["funding_rate"] >= -0.00004))) & (dataframe["funding_rate"] <= fr_cap_alt))
        alt_fr_ok = alt_fr_ok_regime | (cooling_bull_contango & (fz168 <= 0.10) & (adx_4h_s >= 23.0)) | ((dataframe["funding_rate"] <= -0.00005) & (dataframe["is_green"] == True))
        healthy_long_contango = pd.Series(np.where(is_gold_u, gold_fr_ok, np.where(is_bench_u, bench_fr_ok, alt_fr_ok)), index=dataframe.index) & (~regime_euphoria_long)
        if self.ABLATE_FUNDING_REGIME:
            healthy_long_contango = pd.Series(True, index=dataframe.index)
            uncrowded_squeeze_funding = pd.Series(True, index=dataframe.index)
            uncrowded_rs_funding = pd.Series(True, index=dataframe.index)
        else:
            uncrowded_squeeze_funding = (dataframe["funding_rate"] > 0.0) & (dataframe["funding_rate"] <= 0.00030) & (~regime_euphoria_long)
            uncrowded_rs_funding = (dataframe["funding_rate"] >= -0.00005) & (dataframe["funding_rate"] <= 0.00038) & (~regime_euphoria_long)

        # M5 Exact BTC 4H Structural Alignment
        btc_structural_bull_4h = (
            (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) &
            (dataframe["btc_ema_50_btc_4h"] > dataframe["btc_ema_200_btc_4h"])
        )
        btc_structural_bear_4h = (
            (dataframe["close_btc_4h"] < dataframe["btc_ema_50_btc_4h"]) &
            (dataframe["btc_ema_50_btc_4h"] < dataframe["btc_ema_200_btc_4h"])
        )
        e200_trans_mult = 1.000
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
            if self.ABLATE_REGIME_RISK_SHIELDS
            else (is_gold_u | (dataframe["btc_ret_6h"] > -0.020) | (dataframe["delta_rs"] > 0.025))
        )
        er_12 = dataframe["er_12"]
        er_24 = dataframe["er_24"]
        ser_12 = dataframe["ser_12"]
        use_im_entry = not self.ABLATE_INTRINSIC_MICRO
        long_shields_ok = (is_gold_u | (~btc_velocity_shock)) & (~dying_bull_rollover_4h) & healthy_long_contango & btc_waterfall_ok

        cross_up = qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        cross_dn = qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])

        long_ema9_pb = pd.Series(False, index=dataframe.index)
        long_bb_pb = pd.Series(False, index=dataframe.index)
        short_bb_fade = pd.Series(False, index=dataframe.index)
        short_crowded_fade = pd.Series(False, index=dataframe.index)

        if not self.ABLATE_UPPER_BB_FADES:
            cs_fr_mania = (not self.ABLATE_CROSS_SECTIONAL) & (dataframe["funding_rate"] >= 0.00013) & (dataframe["fr_div"] >= 0.00005) & (dataframe["cs_bull_4h"] < 0.85)
            scf_static_regime = (~is_gold_u) & (True if self.ABLATE_FUNDING_REGIME else ((dataframe["funding_rate"] >= 0.00022) | cs_fr_mania))
            scf_static_loc = (dataframe["high"] >= dataframe["bb_upperband"] * 0.998) & (dataframe["slowk"] > 72.0) & (dataframe["slowk"] < dataframe["slowk"].shift(1))
            scf_static = scf_static_regime & scf_static_loc & (dataframe["rsi"] >= 68.5) & (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.85)
            hi_corr_alt_sc = (rv_u < 1.50) | (bc672_u >= 0.83)
            scf_bear_c = (dataframe["close"] < dataframe["ema_200"]) | ((rv_u < 1.50) & (bc672_u >= 0.81) & (dataframe["ema168_slope24"] <= 0.002))
            scf_fz_ok = (fz168 >= np.where(hi_corr_alt_sc, 1.45, 1.10)) | ((~hi_corr_alt_sc) & (rv_u < 2.25) & (dataframe["ema168_slope24"] <= -0.004) & (fz168 >= 0.65) & (dataframe["funding_rate"] >= 0.00005))
            scf_zscore = (
                is_alt_u & scf_bear_c & (~btc_up_velocity_shock)
                & scf_fz_ok & (dataframe["funding_rate"] >= np.where(hi_corr_alt_sc, 0.00009, 0.00006))
                & (dataframe["high"] >= dataframe["bb_upperband"] * 0.997) & (dataframe["rsi"] >= np.where(hi_corr_alt_sc, 64.5, 62.0))
                & (dataframe["slowk"] > np.where(hi_corr_alt_sc, 69.0, 66.0))
                & ((~hi_corr_alt_sc) | (dataframe["slowk"] < dataframe["slowk"].shift(1)))
                & ((dataframe["ema168_slope24"] <= -0.008) | (dataframe["rsi"] >= 64.0))
                & (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
            ) if use_zscore else pd.Series(False, index=dataframe.index)
            short_crowded_fade = scf_static | scf_zscore
        if use_zscore:
            lbb_gate = np.where(is_bench_u, ((fz168 <= -1.60) | (fz336 <= -1.80)) & (dataframe["funding_rate"] <= 0.00001), (fz168 <= -0.58) & (dataframe["funding_rate"] <= 0.00025))
            btc_m4_lbb = (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) & (dataframe["btc_rsi_btc_4h"] > 48.0)
            lbb_sawtooth = (
                is_alt_u & btc_m4_lbb & healthy_long_contango & (~ema50_rollover_4h)
                & (adx_4h_s >= 20.0) & ((dataframe["ema168_slope24"] >= 0.009) | (dataframe["dc72_pos"] >= 0.30))
                & (dataframe["ema168_slope24"] >= 0.005)
                & (er_12 >= 0.16) & (~((er_24 < 0.165) & (dataframe[f"adx_rising_{self.informative_timeframe}"] != True)))
            ) if not (self.ABLATE_REGIME_RISK_SHIELDS or self.ABLATE_ZSCORE_COHERENCE) else pd.Series(False, index=dataframe.index)
            lbb_hype_fr = (bc672_u < 0.70) & (rv_u >= 2.00) & (dataframe["atr_pct_med672"] >= 0.0135) & (dataframe["funding_rate"] <= 0.00010)
            lbb_cond = lbb_gate | lbb_hype_fr | lbb_sawtooth
            long_bb_pb_regime = bull_regime_ok & (~btc_velocity_shock) & (~ema50_rollover_4h)
            long_bb_pb_loc = lbb_cond & (dataframe["close"] > dataframe["ema_200"]) & (dataframe["dc72_pos"] >= 0.12) & (dataframe["low"] <= dataframe["bb_lowerband"] * 1.004)
            long_bb_pb = long_bb_pb_regime & long_bb_pb_loc & (dataframe["rsi"] <= 43.0) & (dataframe["is_green"] == True) & (dataframe["ema168_slope24"] >= 0.005) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)

        flat_weekly_dc72_trap = (
            pd.Series(False, index=dataframe.index)
            if (self.ABLATE_REGIME_RISK_SHIELDS or self.ABLATE_ZSCORE_COHERENCE)
            else (
                (dataframe["ema168_slope24"] < 0.0025)
                & (dataframe["dc72_pos"] < 0.25)
                & (dataframe["er_12"] < 0.10)
            )
        )
        alt_sawtooth_weekly_trap = (
            pd.Series(False, index=dataframe.index)
            if (self.ABLATE_REGIME_RISK_SHIELDS or self.ABLATE_ZSCORE_COHERENCE)
            else (
                (dataframe["er_24"] < 0.08)
                & (dataframe["ema168_slope24"] < 0.012)
            )
        )

        # -------------------------------------------------------------
        # ENGINE 1: Universal Structural Bull Value Pullback (Faithful K1 Translation)
        # -------------------------------------------------------------
        is_leader_lp = (bc672_u < 0.56) & (rv_u >= 1.65)
        is_hype_dip = is_leader_lp | ((bc672_u < 0.70) & (rv_u >= 2.15) & (dataframe["atr_pct_med672"] >= 0.0138) & (dataframe["ema168_slope24"] >= 0.004) & (er_24 >= 0.10))
        is_decoupled_mom = (bc672_u < 0.70) & (rv_u >= 1.95) & (dataframe["ema168_slope24"] >= 0.015) & (adx_4h_s >= 28.0)
        strong_bull = bull_regime_ok & (adx_4h_s >= 24.0) & (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_200"])
        t3b_u = ((dataframe["low"] <= dataframe["ema_21"] * 1.005) | (dataframe["low"] <= dataframe["bb_middleband"])).rolling(3).max() > 0
        # Gold branch
        lp_gold_regime_regime = ((dataframe["close"] <= dataframe["ema_21"] * 1.0105) | (dataframe["low"] <= dataframe["bb_middleband"]) | ((adx_4h_s >= 22.0) & (dataframe["close"] > dataframe["ema_200"]) & t3b_u))
        lp_gold_regime_regime_2 = is_gold_u & bull_regime_ok & long_shields_ok & (adx_4h_s > 22.0) & lp_gold_regime_regime
        lp_gold_regime_loc = (dataframe["close"] <= dataframe["ema_21"] * 1.0135) & (dataframe["close"] >= dataframe["ema_50"] * 0.990) & (dataframe["slowk"] < 55.0) & ((dataframe["is_green"] == True) | (dataframe["lower_wick"] > dataframe["body_size"] * 0.68))
        lp_gold_regime = lp_gold_regime_regime_2 & lp_gold_regime_loc & (dataframe["rsi"] >= 43.0) & (dataframe["rsi"] <= 63.0) & cross_up & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
        lp_gold_regime2_regime = is_gold_u & bull_regime_ok & long_shields_ok & (adx_4h_s >= 22.0)
        lp_gold_regime2_loc = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_200"]) & ((dataframe["low"] <= dataframe["ema_21"] * 1.006) | (dataframe["low"] <= dataframe["bb_middleband"])) & (dataframe["close"] <= dataframe["ema_21"] * 1.0135) & (dataframe["close"] >= dataframe["ema_50"])
        lp_gold_regime2_loc_2 = (dataframe["slowk"] < 48.0) & (dataframe["slowk"] > dataframe["slowd"]) & (dataframe["slowk"] > dataframe["slowk"].shift(1))
        lp_gold_regime2_osc = (dataframe["rsi"] >= 44.0) & (dataframe["rsi"] <= 66.0) & (dataframe["is_green"] == True)
        lp_gold_regime2 = lp_gold_regime2_regime & lp_gold_regime2_loc & lp_gold_regime2_loc_2 & lp_gold_regime2_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.65)
        lp_gold = lp_gold_regime | lp_gold_regime2
        # Benchmark branch
        bench_sawtooth = (dataframe["er_24"] < 0.13) & ((dataframe[f"adx_rising_{self.informative_timeframe}"] != True) | (dataframe["er_24"] < 0.035))
        lp_bench_regime = is_bench_u & bull_regime_ok & long_shields_ok & (adx_4h_s > 21.5) & (True if self.ABLATE_REGIME_RISK_SHIELDS else (~bench_sawtooth)) & (~flat_weekly_dc72_trap)
        lp_bench_loc = ((dataframe["close"] <= dataframe["ema_21"] * 1.0100) | (dataframe["low"] <= dataframe["bb_middleband"])) & (dataframe["close"] <= dataframe["ema_21"] * 1.0135) & (dataframe["close"] >= dataframe["ema_50"] * 0.990) & (dataframe["slowk"] < 54.5)
        lp_bench_loc_2 = ((dataframe["is_green"] == True) | (dataframe["lower_wick"] > dataframe["body_size"] * 0.68))
        lp_bench_osc = (dataframe["rsi"] >= 43.0) & (dataframe["rsi"] <= 63.0) & cross_up & (~((er_24 < 0.08) & (dataframe["cs_er12_mean"] < 0.18)))
        lp_bench = lp_bench_regime & lp_bench_loc & lp_bench_loc_2 & lp_bench_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        # Alt branch (faithful K1!)
        btc_m4_ok = (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) & (dataframe["btc_rsi_btc_4h"] > 48.0)
        lag_gate_u = is_hype_dip | is_decoupled_mom | btc_m4_ok
        bb_mid_hammer_u = (~is_leader_lp) & (rv_u >= 1.48) & (bc672_u >= 0.66) & (dataframe["low"] <= dataframe["bb_middleband"]) & (dataframe["is_green"] == True) & (dataframe["close"] <= dataframe["ema_21"] * 1.0280)
        dip_alt = np.where(
            is_hype_dip,
            (dataframe["close"] <= dataframe["ema_21"] * 1.0120) | (dataframe["close"] <= dataframe["ema_21"] + 0.45 * dataframe["atr"]) | (dataframe["low"] <= dataframe["bb_middleband"]),
            ((((dataframe["close"] <= dataframe["ema_21"] * 1.0100) | (dataframe["low"] <= dataframe["bb_middleband"]) | (strong_bull & t3b_u)) & (dataframe["close"] <= dataframe["ema_21"] * 1.0135)) | bb_mid_hammer_u),
        )
        adx_pb_alt = np.where((bc672_u >= 0.78) & (rv_u < 1.85), 21.0, 20.0)
        sk_pb_alt = np.where(use_zscore & (~is_hype_dip) & (dataframe["ema168_slope24"] >= 0.012) & (dataframe["dc72_pos"] >= 0.35) & (fz168 <= 0.35), 65.0, 55.0)
        is_struct_hype_lp = (dataframe["atr_pct_med672"] >= 0.0145) & (rv_u >= 1.95) & (bc672_u < 0.68)
        kauf_alt_ok = ((is_struct_hype_lp & ((er_24 >= 0.10) | (dataframe["rsi"] >= 45.0))) | ((~flat_weekly_dc72_trap) & (~alt_sawtooth_weekly_trap))) & (~((er_24 < 0.08) & (dataframe["cs_er12_mean"] < 0.18)))
        lp_alt_base_regime = is_alt_u & bull_regime_ok & long_shields_ok & kauf_alt_ok & (adx_4h_s > adx_pb_alt) & (dataframe["rsi"] <= np.where(strong_bull, 67.0, 63.0))
        lp_alt_base_loc = dip_alt & (dataframe["close"] >= dataframe["ema_50"] * 0.990) & (dataframe["slowk"] < sk_pb_alt) & ((dataframe["is_green"] == True) | (dataframe["lower_wick"] > dataframe["body_size"] * 0.68))
        lp_alt_base = lp_alt_base_regime & lp_alt_base_loc & (dataframe["rsi"] >= np.where((bc672_u >= 0.76) & (rv_u >= 1.55) & (rv_u <= 1.95), 44.0, 43.0)) & cross_up & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70) & lag_gate_u
        lp_rs_gate = is_hype_dip | (rv_u <= 1.82) | ((dataframe["btc_strong_bull_btc_4h"] == True) & (dataframe["ema168_slope24"] >= 0.012) & (fz168 <= 0.50))
        lp_alt_rs_regime = is_alt_u & strong_bull & long_shields_ok & kauf_alt_ok & (dataframe["close"] <= np.where(is_hype_dip, dataframe["close"], dataframe["ema_21"] * 1.0135))
        lp_alt_rs_loc = ((dataframe["low"] <= dataframe["ema_21"] * 1.006) | (dataframe["low"] <= dataframe["bb_middleband"])) & (dataframe["close"] >= dataframe["ema_50"]) & (dataframe["slowk"] < 53.0) & (dataframe["slowk"] > dataframe["slowd"]) & (dataframe["slowk"] > dataframe["slowk"].shift(1))
        lp_alt_rs_osc = (dataframe["delta_rs"] > 0.015) & (dataframe["rsi"] >= 45.0) & (dataframe["rsi"] <= 67.0) & (dataframe["is_green"] == True)
        lp_alt_rs = lp_alt_rs_regime & lp_alt_rs_loc & lp_alt_rs_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70) & lag_gate_u & lp_rs_gate
        lp_alt_reclaim_regime = is_alt_u & bull_regime_ok & long_shields_ok & kauf_alt_ok & (adx_4h_s >= 23.0)
        lp_alt_reclaim_loc = (dataframe["ema_50"] > dataframe["ema_200"]) & ((dataframe["low"] <= dataframe["ema_50"] * 1.002) | (dataframe["low"] <= dataframe["bb_lowerband"] * 1.005)) & (dataframe["close"] <= dataframe["ema_21"] * 1.0135) & (dataframe["close"] >= dataframe["ema_200"] * 1.010) & (dataframe["slowk"] < 44.0)
        lp_alt_reclaim_osc = (dataframe["rsi"] >= 38.0) & (dataframe["rsi"] <= 54.0) & cross_up & (dataframe["is_green"] == True)
        lp_alt_reclaim = lp_alt_reclaim_regime & lp_alt_reclaim_loc & lp_alt_reclaim_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75) & (~is_struct_hype_lp) & lag_gate_u
        lp_alt_e50_regime = is_alt_u & bull_regime_ok & long_shields_ok & kauf_alt_ok & (adx_4h_s > 21.0)
        lp_alt_e50_loc = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["low"] <= dataframe["ema_50"] * 1.004) & (dataframe["close"] >= dataframe["ema_50"] * 0.990) & (dataframe["close"] <= dataframe["ema_21"] * 1.010) & (dataframe["slowk"] < 42.0)
        lp_alt_e50_osc = (dataframe["rsi"] >= 39.5) & (dataframe["rsi"] < 43.0) & cross_up & (dataframe["is_green"] == True)
        lp_alt_e50_flow = (rv_u >= 1.45) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
        lp_alt_e50 = lp_alt_e50_regime & lp_alt_e50_loc & lp_alt_e50_osc & lp_alt_e50_flow & (~is_struct_hype_lp) & lag_gate_u
        adx_e9_gate = np.where(is_hype_dip & (dataframe["ema168_slope24"] >= 0.010) & (fz168 <= 1.10), 18.5, 23.0)
        lp_alt_ema9_regime = is_alt_u & (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) & long_shields_ok & (adx_4h_s >= adx_e9_gate) & (dataframe["volume"] > dataframe["volume_mean_20"] * np.where(is_hype_dip, 0.68, 0.70))
        lp_alt_ema9_loc = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["low"] <= dataframe["ema_9"] * 1.002) & (dataframe["close"] >= dataframe["ema_21"]) & (dataframe["close"] <= dataframe["ema_9"] * 1.006) & (dataframe["slowk"] < 56.0)
        lp_alt_ema9_osc = (dataframe["rsi"] >= 46.0) & (dataframe["rsi"] <= 65.5) & cross_up & (dataframe["is_green"] == True)
        lp_alt_ema9 = lp_alt_ema9_regime & lp_alt_ema9_loc & lp_alt_ema9_osc & (rv_u >= 2.20) & (bc672_u < 0.70) & (dataframe["ema168_slope24"] >= 0.020)
        long_pullback_extra = lp_gold | lp_bench | lp_alt_base | lp_alt_rs | lp_alt_reclaim | lp_alt_e50
        long_pullback = long_pullback_extra | lp_alt_ema9

        # -------------------------------------------------------------
        # ENGINE 2+3: Universal Squeeze & RS Momentum Breakout
        # -------------------------------------------------------------
        sq_comp_u = dataframe["in_squeeze"].shift(1).rolling(6).sum() >= 5
        adx_sq_u = np.where(sq_comp_u & (rv_u < 1.65), 16.0, 20.0)
        im_sq_ok = pd.Series(True, index=dataframe.index) if (self.ABLATE_REGIME_RISK_SHIELDS or not use_im_entry) else ((er_12 >= 0.05) & (er_24 >= 0.06))
        long_squeeze_regime = is_alt_u & (~is_ultra_u) & bull_regime_ok & (~btc_velocity_shock) & (~ema50_rollover_4h) & (adx_4h_s >= adx_sq_u)
        long_squeeze_loc = (dataframe["squeeze_followthrough_3"] == True) & (dataframe["bb_width"] >= dataframe["bb_width_ema"] * 0.94) & (dataframe["close"] > dataframe["ema_21"]) & (dataframe["close"] < dataframe["bb_upperband"] * 1.005)
        long_squeeze_osc = uncrowded_squeeze_funding & (dataframe["mom_expanding_up"] == True) & (dataframe["rsi"] >= 50.0) & (dataframe["rsi"] <= 68.0) & (dataframe["is_green"] == True)
        long_squeeze = long_squeeze_regime & (dataframe["btc_rsi_1h_btc_1h"] <= 68.0) & long_squeeze_loc & long_squeeze_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.10) & im_sq_ok
        min_drs_u = np.where(is_ultra_u, 0.042, np.where((bc672_u >= 0.77) & (rv_u < 1.82), 0.036, np.where((bc672_u < 0.76) & (rv_u >= 1.80), 0.025, 0.028)))
        long_rs_regime = is_alt_u & bull_regime_ok & (~btc_velocity_shock) & (~ema50_rollover_4h) & (adx_4h_s >= 21.0) & (dataframe["btc_rsi_1h_btc_1h"] <= 69.0)
        long_rs_loc = (dataframe["close"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_21"]) & (dataframe["close"] <= dataframe["bb_upperband"] * 1.005) & (dataframe["close"] >= dataframe["ema_50"] * 0.995)
        long_rs_osc = ((er_24 >= 0.09) | (dataframe["delta_rs"] >= 0.060)) & (dataframe["delta_rs"] > min_drs_u) & (dataframe["delta_rs"] > dataframe["delta_rs_ema"]) & (dataframe["rsi"] >= 48.0) & (dataframe["rsi"] <= 67.0)
        long_rs_flow = uncrowded_rs_funding & (dataframe["cs_er12_mean"] >= 0.16) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.06)
        long_rs = long_rs_regime & long_rs_loc & long_rs_osc & (dataframe["is_green"] == True) & long_rs_flow

        # -------------------------------------------------------------
        # ENGINE 4A: Universal Short Resistance Fade + Exhaustion BB Fade (Faithful K1 Translation)
        # -------------------------------------------------------------
        ema_50_lt_200 = dataframe["ema_50"] < dataframe["ema_200"]
        is_ultra_sf_blk = is_leader_lp | ((bc672_u < 0.67) & (rv_u >= 1.95))
        fr_sf_min = np.where((adx_4h_s >= 28.0) & (rv_u >= 1.65), -0.00035, -0.00010)
        short_shields_ok = (~btc_up_velocity_shock) & (~dying_bear_recovery_4h) & (True if self.ABLATE_FUNDING_REGIME else (dataframe["funding_rate"] > fr_sf_min))
        wyckoff_bear_vol_ok = (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70) | (
            use_im_entry & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (er_24 >= 0.15) & (ser_12 <= 0.20) & (dataframe["volume"] >= dataframe["volume_mean_20"] * 0.18)
        )
        res_touched_3b = ((dataframe["high"] >= dataframe["ema_21"] * 0.996) | (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)).rolling(3).max() > 0
        high_posfr_rej_regime = (~is_ultra_sf_blk) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True)
        high_posfr_rej_loc = (dataframe["high"] >= dataframe["ema_21"] * 0.996) & (dataframe["close"] <= dataframe["ema_50"] + 0.40 * dataframe["atr"]) & (dataframe["slowk"] > 55.0)
        high_posfr_rej = high_posfr_rej_regime & high_posfr_rej_loc & (dataframe["is_red"] == True) & (rv_u >= 1.78) & (dataframe["funding_rate"] >= 0.00008)
        adx_sf_gate = np.where(is_gold_u, 18.0, np.where((bc672_u >= 0.78) & (rv_u >= 1.50), 23.0, 22.0))
        ema50_cap_mult = np.where((adx_4h_s >= 28.0) & (rv_u >= 1.65), 0.75, 0.50)
        sf_base_u_regime = (~is_bench_u) & (~is_ultra_sf_blk) & bear_regime_ok & short_shields_ok & (adx_4h_s > adx_sf_gate)
        sf_base_u_loc = (dataframe["close"] < dataframe["ema_200"]) & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & res_touched_3b & (dataframe["close"] <= dataframe["ema_50"] + ema50_cap_mult * dataframe["atr"]) & (dataframe["bb_width"] >= 0.012)
        sf_base_u_loc_2 = ((cross_dn & (dataframe["slowk"] > 54.0)) | high_posfr_rej)
        sf_base_u_osc = (dataframe["rsi"] >= np.where(rv_u < 1.50, 54.5, 53.0)) & (dataframe["rsi"] <= 69.5) & ((dataframe["is_red"] == True) | (dataframe["upper_wick"] > dataframe["body_size"] * 0.70))
        sf_base_u = sf_base_u_regime & sf_base_u_loc & sf_base_u_loc_2 & sf_base_u_osc & wyckoff_bear_vol_ok
        sf_curl_u = (
            is_alt_u & (~is_ultra_sf_blk) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & short_shields_ok & (adx_4h_s >= 23.0) & (er_24 >= 0.16)
            & ema_50_lt_200 & (dataframe["close"] < dataframe["ema_200"]) & (dataframe["funding_rate"] >= 0.00002)
            & ((dataframe["high"] >= dataframe["ema_21"] * 0.996) | (dataframe["high"] >= dataframe["bb_middleband"] * 0.998))
            & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & (dataframe["close"] <= dataframe["ema_50"])
            & (dataframe["rsi"] >= 53.0) & (dataframe["rsi"] <= 67.0) & (dataframe["slowk"] > 56.0)
            & (dataframe["slowk"] < dataframe["slowd"]) & (dataframe["slowk"] < dataframe["slowk"].shift(1))
            & (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
        ) if use_im_entry else pd.Series(False, index=dataframe.index)
        sf_zcurl_u = (
            is_alt_u & (~is_ultra_sf_blk) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & short_shields_ok & (adx_4h_s >= 22.0)
            & ema_50_lt_200 & (dataframe["close"] < dataframe["ema_200"])
            & (dataframe["ema168_slope24"] <= -0.008) & (dataframe["dc72_pos"] <= 0.68) & (fz168 >= 0.15) & (dataframe["funding_rate"] >= 0.0)
            & ((dataframe["high"] >= dataframe["ema_21"] * 0.996) | (dataframe["high"] >= dataframe["bb_middleband"] * 0.998))
            & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & (dataframe["close"] <= dataframe["ema_50"] + 0.35 * dataframe["atr"])
            & (dataframe["rsi"] >= 52.0) & (dataframe["rsi"] <= 68.0) & (dataframe["slowk"] > 53.0)
            & (dataframe["slowk"] < dataframe["slowd"]) & (dataframe["slowk"] < dataframe["slowk"].shift(1))
            & (dataframe["is_red"] == True) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
        ) if use_zscore else pd.Series(False, index=dataframe.index)
        sf_rs_u_regime = is_alt_u & (~is_ultra_sf_blk) & bear_regime_ok & (~btc_up_velocity_shock) & (~ema50_recovery_4h) & (adx_4h_s >= 22.0)
        sf_rs_u_loc = (dataframe["close"] < dataframe["ema_21"] * 0.994) & (dataframe["close"] >= dataframe["bb_lowerband"] * 0.995) & (dataframe["close"] <= dataframe["ema_50"]) & (dataframe["close"] < dataframe["ema_200"])
        sf_rs_u_osc = (dataframe["delta_rs"] < -0.028) & (dataframe["delta_rs"] < dataframe["delta_rs_ema"]) & (dataframe["rsi"] >= 37.0) & (dataframe["rsi"] <= 51.5) & (er_24 >= 0.12) & (dataframe["is_red"] == True)
        sf_rs_u_flow = (bc672_u <= 0.81) & (dataframe["ema168_slope24"] <= -0.010) & (dataframe["funding_rate"] >= -0.00010) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05)
        sf_rs_u = sf_rs_u_regime & sf_rs_u_loc & sf_rs_u_osc & sf_rs_u_flow
        sf_e9_tight = pd.Series(False, index=dataframe.index)
        sf_posfr_deep_regime = (~is_bench_u) & (~is_ultra_sf_blk) & bear_regime_ok & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & short_shields_ok & (adx_4h_s >= 24.0)
        sf_posfr_deep_loc = ema_50_lt_200 & (dataframe["close"] < dataframe["ema_200"]) & (dataframe["high"] < dataframe["bb_upperband"] * 0.996) & res_touched_3b & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & (dataframe["close"] <= dataframe["ema_50"] + 0.45 * dataframe["atr"])
        sf_posfr_deep_loc_2 = (dataframe["slowk"] > 50.0) & (dataframe["slowk"] < dataframe["slowd"]) & (dataframe["slowk"] < dataframe["slowk"].shift(1))
        sf_posfr_deep_osc = dataframe["rsi"].between(46.0, 68.5) & (dataframe["is_red"] == True)
        sf_posfr_deep_flow = (rv_u >= 1.50) & (dataframe["funding_rate"] >= 0.00004)
        sf_posfr_deep = sf_posfr_deep_regime & sf_posfr_deep_loc & sf_posfr_deep_loc_2 & sf_posfr_deep_osc & sf_posfr_deep_flow & wyckoff_bear_vol_ok
        short_fade_flow = (sf_base_u | sf_curl_u | sf_zcurl_u | sf_rs_u | sf_posfr_deep | sf_e9_tight)
        short_fade = short_fade_flow & (dataframe["cs_er12_mean"] < 0.50)

        # SBB: Restore H1 proven zero-coin SBB (WR 86.4%, %PF 2.18, drop sbb_bear_ultra, keep BTC + calm ETH + bear alt)
        sbb_mod_vol_regime = (~is_bench_u) & bear_regime_ok
        sbb_mod_vol_loc = (dataframe["close"] < dataframe["ema_200"] * 0.996) & (dataframe["slowk"] > 64.0)
        sbb_mod_vol_osc = (er_24 <= 0.42) & (dataframe["rsi"] >= 58.5)
        sbb_mod_vol = sbb_mod_vol_regime & sbb_mod_vol_loc & sbb_mod_vol_osc & (rv_u < 1.85) & (bc672_u >= 0.65) & (dataframe["funding_rate"] > 0.0)
        sbb_bear_alt_regime = (~is_bench_u) & (~is_ultra_sf_blk) & bear_regime_ok
        sbb_bear_alt_loc = (dataframe["close"] < dataframe["ema_200"] * 0.994) & (dataframe["slowk"] > 66.0) & ema_50_lt_200
        sbb_bear_alt = sbb_bear_alt_regime & sbb_bear_alt_loc & (dataframe["delta_rs"] <= 0.025) & (dataframe["rsi"] >= 60.0) & (er_24 >= 0.09) & (dataframe["funding_rate"] > 0.0)
        sbb_bench = is_bench_u & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & ema_50_lt_200 & (adx_4h_s >= 20.0) & (er_24 <= 0.28) & (dataframe["funding_rate"] > 0.0) & (dataframe["rsi"] >= 59.0) & (dataframe["slowk"] > 65.0) & (dataframe["slowk"] < dataframe["slowk"].shift(1)) if use_im_entry else pd.Series(False, index=dataframe.index)
        short_bb_fade_regime = (~is_gold_u) & (~btc_up_velocity_shock)
        short_bb_fade_loc = (sbb_mod_vol | sbb_bear_alt | sbb_bench) & (dataframe["close"] < dataframe["ema_200"]) & (dataframe["high"] >= dataframe["bb_upperband"] * 0.996)
        short_bb_fade = short_bb_fade_regime & short_bb_fade_loc & (dataframe["is_red"] == True) & (dataframe["cs_er12_mean"] <= 0.61) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)

        # ENGINE 4B: Breakdown (Restore H1/M1 exact vol>1.75 & s168<=0 quality -> +629.7 C %PF 1.91!)
        bd_core = (dataframe["funding_rate"] >= -0.00040) & (dataframe["funding_rate"] <= 0.00010) & (adx_4h_adx < 35.5) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.75)
        bd_tb = (dataframe["funding_rate"] >= -0.00012) & (dataframe["funding_rate"] <= 0.00030) & (dataframe["close"] >= dataframe["ema_21"] * 0.955) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.75)
        short_breakdown_regime = is_alt_u & (~is_ultra_sf_blk) & (~btc_up_velocity_shock) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (adx_4h_s >= 21.0)
        short_breakdown_loc = (dataframe["close"] < dataframe["ema_200"]) & (dataframe["close"] < dataframe["ema_21"] * 0.985) & (dataframe["close"] < dataframe["ema_50"]) & (dataframe["slowk"] < dataframe["slowd"]) & (dataframe["close"] < dataframe["support_level_20"].shift(1) * 0.999)
        short_breakdown_osc = (dataframe["rsi"] >= 32.0) & (dataframe["rsi"] <= 48.0) & (dataframe["taker_sell_volume_surge"] == True) & (er_12 < 0.50)
        short_breakdown_flow = (dataframe["ema168_slope24"] <= 0.0) & (dataframe["cs_er12_mean"] <= 0.48)
        short_breakdown = short_breakdown_regime & short_breakdown_loc & short_breakdown_osc & short_breakdown_flow & (bd_core | bd_tb)

        # ENGINE 4C: Universal Negative/Neutral-Funding Bear Squeeze Expansion
        sq_comp_dn_u = dataframe["in_squeeze"].shift(1).rolling(6).sum() >= 4
        adx_sq_dn_u = np.where(sq_comp_dn_u, 17.5, 20.5)
        short_squeeze_regime = is_alt_u & (~is_ultra_sf_blk) & (~btc_up_velocity_shock) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (adx_4h_s >= adx_sq_dn_u)
        short_squeeze_loc = (dataframe["squeeze_followthrough_down_3"] == True) & (dataframe["bb_width"] >= dataframe["bb_width_ema"] * 0.94) & (dataframe["close"] < dataframe["ema_21"]) & (dataframe["close"] > dataframe["bb_lowerband"] * 0.995) & (dataframe["close"] <= dataframe["ema_50"] * 1.005)
        short_squeeze_osc = (dataframe["mom_expanding_down"] == True) & dataframe["rsi"].between(33.0, 54.0) & (dataframe["is_red"] == True)
        short_squeeze_flow = (dataframe["cs_er12_mean"] <= 0.52) & (dataframe["funding_rate"] >= -0.00030) & (dataframe["funding_rate"] <= 0.00003) & (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05)
        short_squeeze = short_squeeze_regime & short_squeeze_loc & short_squeeze_osc & short_squeeze_flow

        # -------------------------------------------------------------
        # PARADIGM P1: Truncation-Safe 8-Pair Cross-Sectional Breadth, Rank & Funding Routing
        # -------------------------------------------------------------
        if not self.ABLATE_CROSS_SECTIONAL:
            cs_bull_b = dataframe["cs_bull_breadth"]
            cs_bear_b = dataframe["cs_bear_breadth"]
            cs_bull_4 = dataframe["cs_bull_4h"]
            cs_bear_4 = dataframe["cs_bear_4h"]
            cs_mom_rk = dataframe["cs_mom_rank"]
            cs_act_n = dataframe["cs_active_n"]
            fr_div_s = dataframe["fr_div"]
            dir_er_s = dataframe["dir_er_12"]

            if not self.ABLATE_REGIME_RISK_SHIELDS:
                gold_vol_active = dataframe["atr_pct"] >= (dataframe["atr_pct_med168"] * 1.20)
                safe_route_ok = (~is_gold_u) | (~((cs_bull_b >= 0.85) & (cs_bull_4 >= 0.85) & (~gold_vol_active)))
                long_pullback = long_pullback & safe_route_ok
                short_fade = short_fade & safe_route_ok
                long_pullback = long_pullback & ((~is_bench_u) | (dir_er_s > -0.24))
                long_bb_pb = long_bb_pb & ((~is_alt_u) | is_ultra_u | (fr_div_s < 0.00002))
                bull_breadth_trap = is_alt_u & (~is_ultra_sf_blk) & (cs_bull_b >= 0.71) & (fr_div_s < 0.00005)
                short_fade = short_fade & (~bull_breadth_trap)

            cs_mtf_washout_curl_regime = bull_regime_ok & long_shields_ok & ((~is_bench_u) | (dir_er_s > -0.24)) & (~is_gold_u) & (~is_ultra_u)
            cs_mtf_washout_curl_loc = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_200"]) & ((dataframe["low"] <= dataframe["ema_21"] * 1.006) | (dataframe["low"] <= dataframe["bb_middleband"])) & (dataframe["close"] <= dataframe["ema_21"] * 1.0135) & (dataframe["close"] >= dataframe["ema_50"] * 0.992)
            cs_mtf_washout_curl_loc_2 = (dataframe["slowk"] < 50.0) & (dataframe["slowk"] > dataframe["slowd"]) & (dataframe["slowk"] > dataframe["slowk"].shift(1))
            cs_mtf_washout_curl_osc = (dataframe["rsi"] >= 42.0) & (dataframe["rsi"] <= 64.0) & (dataframe["is_green"] == True)
            cs_mtf_washout_curl_flow = (cs_bull_4 >= 0.57) & (cs_bear_b >= 0.43) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
            cs_mtf_washout_curl = cs_mtf_washout_curl_regime & (adx_4h_s >= 20.0) & cs_mtf_washout_curl_loc & cs_mtf_washout_curl_loc_2 & cs_mtf_washout_curl_osc & cs_mtf_washout_curl_flow
            long_pullback = long_pullback | cs_mtf_washout_curl

            if not self.ABLATE_FUNDING_REGIME:
                cs_underfund_regime = is_alt_u & (~btc_velocity_shock) & (dataframe["btc_ret_6h"] > -0.025)
                cs_underfund_loc = (dataframe["close"] >= dataframe["ema_200"] * 0.94) & (dataframe["low"] <= dataframe["bb_lowerband"] * 1.003) & (dataframe["slowk"] < 28.0) & (dataframe["slowk"] > dataframe["slowk"].shift(1))
                cs_underfund_osc = (dataframe["rsi"] <= 36.0) & (dataframe["is_green"] == True)
                cs_underfund_flow = (dataframe["funding_rate"] <= -0.00005) & (fr_div_s <= -0.00008) & (cs_bear_4 < 0.85) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.85)
                cs_underfund = cs_underfund_regime & cs_underfund_loc & cs_underfund_osc & cs_underfund_flow
                long_bb_pb = long_bb_pb | cs_underfund

                cs_posfr_div_regime = is_alt_u & (~is_ultra_sf_blk) & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & short_shields_ok & (adx_4h_s > 23.0)
                cs_posfr_div_loc = (dataframe["close"] < dataframe["ema_200"]) & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & (dataframe["high"] >= dataframe["ema_21"] * 0.996) & (dataframe["close"] <= dataframe["ema_50"] + 0.40 * dataframe["atr"]) & (dataframe["slowk"] > 55.0)
                cs_posfr_div_osc = (dataframe["rsi"] >= 53.0) & (dataframe["rsi"] <= 68.5) & (dataframe["is_red"] == True)
                cs_posfr_div = cs_posfr_div_regime & cs_posfr_div_loc & cs_posfr_div_osc & (dataframe["funding_rate"] >= 0.00008) & (fr_div_s >= 0.00002) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
                short_fade = short_fade | cs_posfr_div

            cs_sync_bear_sf_regime = is_alt_u & (~is_ultra_sf_blk) & bear_regime_ok & short_shields_ok & (adx_4h_s >= 22.0)
            cs_sync_bear_sf_loc = (dataframe["close"] < dataframe["ema_200"]) & (dataframe["close"] >= dataframe["ema_21"] * 0.989) & (dataframe["bb_width"] >= 0.018) & ((dataframe["high"] >= dataframe["ema_21"] * 0.996) | (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)) & (dataframe["close"] <= dataframe["ema_50"] + 0.45 * dataframe["atr"])
            cs_sync_bear_sf_osc = (cs_mom_rk >= (cs_act_n - 3.0)) & (dataframe["rsi"] >= 55.0) & (dataframe["rsi"] <= 68.5) & cross_dn & ((dataframe["is_red"] == True) | (dataframe["upper_wick"] > dataframe["body_size"] * 0.70))
            cs_sync_bear_sf_flow = (cs_bear_b >= 0.71) & (cs_bear_4 >= 0.57) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
            cs_sync_bear_sf = cs_sync_bear_sf_regime & cs_sync_bear_sf_loc & (dataframe["slowk"] > 54.0) & cs_sync_bear_sf_osc & cs_sync_bear_sf_flow
            short_fade = short_fade | cs_sync_bear_sf

        if self.ABLATE_UPPER_BB_FADES:
            short_bb_fade = pd.Series(False, index=dataframe.index)
            short_crowded_fade = pd.Series(False, index=dataframe.index)
        if self.ABLATE_SQUEEZE_AND_RS:
            long_squeeze = pd.Series(False, index=dataframe.index)
            short_squeeze = pd.Series(False, index=dataframe.index)
            long_rs = pd.Series(False, index=dataframe.index)

        # -------------------------------------------------------------
        # ENGINE 5 (P6): Orthogonal Non-Trending Range Liquidity-Sweep & Funding-Pin Mean-Reversion
        # Zero-cannibalization invariant: fires strictly when directional 4H trend engines are OFF
        # ((~bull_regime_ok & ~bear_regime_ok) | (adx_4h < 20.5 & er_24 < 0.22)) AND no directional signal fired!
        # -------------------------------------------------------------
        long_range_sweep = pd.Series(False, index=dataframe.index)
        short_range_sweep = pd.Series(False, index=dataframe.index)

        if not self.ABLATE_RANGE_SWEEP:
            non_trending_gate = ((~bull_regime_ok) & (~bear_regime_ok)) | (
                (adx_4h_adx < 20.5) & (dataframe["er_24"] < 0.22)
            )
            close_4h_col = dataframe[f"close_{self.informative_timeframe}"].fillna(dataframe["close"])
            ema50_4h_col = dataframe[f"ema_50_{self.informative_timeframe}"].fillna(dataframe["ema_50"])
            ema200_4h_col = dataframe[f"ema_200_{self.informative_timeframe}"].fillna(dataframe["ema_200"])
            sup20_prev = dataframe["support_level_20"].shift(1)

            l_sup_bb_sweep_regime = non_trending_gate & (~btc_velocity_shock) & (dataframe[f"ema50_rollover_{self.informative_timeframe}"] != True) & (dataframe["btc_ret_6h"] > -0.020) & (True if self.ABLATE_FUNDING_REGIME else ((dataframe["funding_rate"] <= 0.00010) & (dataframe["funding_rate"] >= -0.00025))) & (adx_4h_s >= 19.0)
            l_sup_bb_sweep_loc = ((dataframe["low"] <= dataframe["bb_lowerband"] * 1.002) | (dataframe["low"] <= sup20_prev * 1.001)) & (dataframe["close"] > dataframe["bb_lowerband"]) & (dataframe["close"] >= sup20_prev) & (dataframe["slowk"] >= 11.0) & (dataframe["slowk"] < 25.0)
            l_sup_bb_sweep_loc_2 = ((dataframe["slowk"] > dataframe["slowk"].shift(1)) | cross_up) & (dataframe["close"] > ema200_4h_col)
            l_sup_bb_sweep = l_sup_bb_sweep_regime & l_sup_bb_sweep_loc & l_sup_bb_sweep_loc_2 & (dataframe["is_green"] == True) & (dataframe["rsi"] < 38.0) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)

            l_negfr_reaccum_regime = non_trending_gate & (~btc_velocity_shock) & (dataframe["btc_ret_6h"] > -0.020) & (dataframe[f"ema50_recovery_{self.informative_timeframe}"] == True) & (dataframe["btc_strong_bull_btc_4h"] == True) & (True if self.ABLATE_FUNDING_REGIME else ((dataframe["funding_rate"] <= -0.00004) & (dataframe["funding_rate"] >= -0.00025)))
            l_negfr_reaccum_loc = (close_4h_col > ema50_4h_col) & (dataframe["close"] > dataframe["ema_200"]) & (dataframe["close"] > ema200_4h_col) & ((dataframe["low"] <= dataframe["ema_21"] * 1.006) | (dataframe["low"] <= dataframe["bb_lowerband"] * 1.008)) & (dataframe["close"] <= dataframe["ema_21"] * 1.012)
            l_negfr_reaccum_loc_2 = (dataframe["close"] >= dataframe["ema_50"] * 0.992) & (dataframe["slowk"] < 46.0)
            l_negfr_reaccum_osc = (dataframe["is_green"] == True) & (dataframe["rsi"] >= 40.0) & (dataframe["rsi"] <= 56.0) & cross_up & (dataframe["er_24"] >= 0.08)
            l_negfr_reaccum = l_negfr_reaccum_regime & l_negfr_reaccum_loc & l_negfr_reaccum_loc_2 & l_negfr_reaccum_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
            long_range_sweep = (l_sup_bb_sweep | l_negfr_reaccum) & is_alt_u & (~is_ultra_u)

            s_distrib_ema21_regime = non_trending_gate & (~btc_up_velocity_shock) & (dataframe["btc_ret_6h"] < 0.020) & (dataframe[f"ema50_rollover_{self.informative_timeframe}"] == True) & (dataframe["btc_strong_bull_btc_4h"] != True) & (True if self.ABLATE_FUNDING_REGIME else (dataframe["funding_rate"] >= 0.00005))
            s_distrib_ema21_loc = (close_4h_col < ema50_4h_col) & (dataframe["close"] < dataframe["ema_200"]) & (dataframe["high"] < dataframe["bb_upperband"] * 0.995) & (dataframe["high"] >= dataframe["ema_21"] * 0.994) & (dataframe["close"] >= dataframe["ema_21"] * 0.988) & (dataframe["close"] <= dataframe["ema_50"] * 1.008)
            s_distrib_ema21_loc_2 = (dataframe["slowk"] > 50.0) & (dataframe["close"] <= ema200_4h_col * 1.06)
            s_distrib_ema21_osc = (dataframe["is_red"] == True) & (dataframe["rsi"] >= 45.0) & (dataframe["rsi"] <= 58.5) & cross_dn
            s_distrib_ema21 = s_distrib_ema21_regime & s_distrib_ema21_loc & s_distrib_ema21_loc_2 & s_distrib_ema21_osc & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)

            s_crowded_bb_sweep_regime = non_trending_gate & (~btc_up_velocity_shock) & (dataframe[f"ema50_recovery_{self.informative_timeframe}"] != True) & (dataframe["btc_ret_6h"] < 0.020) & (True if self.ABLATE_FUNDING_REGIME else (dataframe["funding_rate"] >= 0.00010)) & (adx_4h_s >= 20.0)
            s_crowded_bb_sweep_loc = (dataframe["high"] >= dataframe["bb_upperband"] * 0.996) & (dataframe["close"] < dataframe["bb_upperband"]) & (dataframe["slowk"] > 68.0) & ((dataframe["slowk"] < dataframe["slowk"].shift(1)) | cross_dn) & (dataframe["close"] <= ema200_4h_col * 1.06)
            s_crowded_bb_sweep = s_crowded_bb_sweep_regime & s_crowded_bb_sweep_loc & (dataframe["is_red"] == True) & (dataframe["rsi"] >= 58.0) & (dataframe["er_24"] >= 0.20) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
            short_range_sweep = (s_distrib_ema21 | s_crowded_bb_sweep) & is_alt_u & (~is_ultra_u) & (~((dataframe["cs_er12_mean"] >= 0.52) | (er_12 >= 0.45)))

        long_er_cont_regime = is_alt_u & (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) & long_shields_ok & (adx_4h_s >= 22.0)
        long_er_cont_loc = (dataframe["ema_9"] > dataframe["ema_21"]) & (dataframe["ema_21"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["low"] <= dataframe["ema_9"] * 1.004) & (dataframe["close"] > dataframe["ema_9"])
        long_er_cont_osc = (er_12 >= 0.48) & (dataframe["delta_rs"] > 0.010) & dataframe["rsi"].between(48.0, 69.0) & (dataframe["is_green"] == True)
        long_er_cont = long_er_cont_regime & long_er_cont_loc & long_er_cont_osc & (dataframe["cs_er12_mean"] >= 0.24) & (dataframe["volume"] > dataframe["volume_mean_20"] * 0.76)
        long_ema9_pb = pd.Series(False, index=dataframe.index)
        long_breakout = long_squeeze | long_rs
        # Keep short/long squeeze/rs limit logic aware of squeeze_followthrough_3 vs rs
        # Priority-ordered tag assignment:
        # long_pullback_alpha FIRST so EMA21 value fills are NEVER displaced up to EMA9!
        dir_long = long_pullback | long_bb_pb | long_ema9_pb | long_squeeze | long_rs | long_er_cont
        dir_short = short_fade | short_crowded_fade | short_bb_fade | short_breakdown | short_squeeze
        no_dir_signal = (~dir_long) & (~dir_short)

        any_long = dir_long | (long_range_sweep & no_dir_signal)
        dataframe.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[long_bb_pb & ~long_pullback, ["enter_long", "enter_tag"]] = (1, "long_bb_pullback")
        dataframe.loc[long_ema9_pb & ~long_pullback & ~long_bb_pb, ["enter_long", "enter_tag"]] = (1, "long_ema9_pullback")
        dataframe.loc[long_breakout & ~long_pullback & ~long_bb_pb, ["enter_long", "enter_tag"]] = (1, "long_breakout_expansion")
        dataframe.loc[long_er_cont & ~long_pullback & ~long_bb_pb & ~long_breakout, ["enter_long", "enter_tag"]] = (1, "long_breakout_expansion")
        dataframe.loc[long_range_sweep & no_dir_signal, ["enter_long", "enter_tag"]] = (1, "range_sweep_alpha")

        dataframe.loc[short_fade & ~any_long, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")
        dataframe.loc[short_crowded_fade & ~any_long & ~short_fade, ["enter_short", "enter_tag"]] = (1, "short_crowded_fade_alpha")
        dataframe.loc[short_bb_fade & ~any_long & ~short_fade & ~short_crowded_fade, ["enter_short", "enter_tag"]] = (1, "short_bb_fade")
        dataframe.loc[short_breakdown & ~any_long & ~short_fade & ~short_crowded_fade & ~short_bb_fade, ["enter_short", "enter_tag"]] = (1, "short_breakdown_alpha")
        dataframe.loc[short_squeeze & ~any_long & ~short_fade & ~short_crowded_fade & ~short_bb_fade & ~short_breakdown, ["enter_short", "enter_tag"]] = (1, "short_breakdown_alpha")
        dataframe.loc[short_range_sweep & no_dir_signal & ~any_long, ["enter_short", "enter_tag"]] = (1, "range_sweep_alpha")

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

        if self.ABLATE_LIFECYCLE_ENGINE:
            dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "rsi_overbought_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "rsi_oversold_tp")

        return dataframe
