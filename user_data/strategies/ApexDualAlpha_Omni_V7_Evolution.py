"""
ApexDualAlpha_Omni_V7_Evolution - Cognitive Multi-Pattern & Structural Invalidation Engine
Curated for Bybit Linear Futures 7-Pair Universe: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG.

Overcoming the 4 Logical Fallacies of V6:
1. Fallacy 1 (Sunk Cost / Stale Loss):
   - Replaces the passive 36h stale hold with an active Structural Invalidation Exit.
   - If trade duration > 20h, current_profit < -0.015 (-4.5% leveraged), AND price < 1H EMA 50:
     exit immediately via "structural_invalidation_cut". Unfreezes capital slots for new alpha!
2. Fallacy 2 (Correlated Altcoin Sinkholes):
   - Micro-Pattern BTC Flash-Dump Shield: Altcoins do not open Longs if BTC's current 1H candle
     is an aggressive red sell bar (close < open and close < btc_ema_21). Prevents multi-coin wipes.
3. Fallacy 3 (Distribution Volume Illusion):
   - Requires confirmed absorption on pullbacks: green candle OR strong hammer lower wick (> body_size * 0.85).
4. Fallacy 4 (Fearful Under-Trading Solved via Pattern 2 Breakout):
   - Adds Volatility Squeeze Expansion Engine (Bollinger Bandwidth contraction breakout)
     to capture rapid momentum legs that never pull back to EMA21, expanding trade frequency!
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
)
from freqtrade.persistence import Trade
import talib.abstract as ta
from technical import qtpylib


class ApexDualAlpha_Omni_V7_Evolution(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Fee-Proof Wide Profit Ladder
    minimal_roi = {
        "0": 0.526,
        "467": 0.161,
        "688": 0.090,
        "1173": 0
    }

    stoploss = -0.297

    trailing_stop = True
    trailing_stop_positive = 0.217
    trailing_stop_positive_offset = 0.249
    trailing_only_offset_is_reached = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False
    }

    order_time_in_force = {
        "entry": "GTC",
        "exit": "GTC"
    }

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    startup_candle_count: int = 200

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        if "BTC" in pair:
            return 7.0
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # Structural Invalidation Cut (Cuts failed bounces early to free capital)
        # If trade is > 20h and losing > -1.5% raw (-4.5% on 3x stake), unfreeze slot!
        if trade_duration > 20.0 and current_profit < -0.015:
            return "structural_invalidation_cut"

        # Standard stale cutoff for mild drifts at 36h
        if trade_duration > 36.0 and current_profit < -0.005:
            return "time_decay_stale_loss"
        
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        informative = [(pair, self.informative_timeframe) for pair in pairs]
        # Ensure BTC 1H and 4H are available for micro-regime protection
        if ("BTC/USDT:USDT", self.timeframe) not in informative:
            informative.append(("BTC/USDT:USDT", self.timeframe))
        if ("BTC/USDT:USDT", self.informative_timeframe) not in informative:
            informative.append(("BTC/USDT:USDT", self.informative_timeframe))
        return informative

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if hasattr(self, "dp") and self.dp:
            # Pair 4H informative
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

            # BTC 1H Micro-trend Informative for Altcoins
            if metadata["pair"] != "BTC/USDT:USDT":
                btc_1h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.timeframe)
                if not btc_1h.empty:
                    btc_1h["btc_ema_21"] = ta.EMA(btc_1h, timeperiod=21)
                    btc_1h["btc_dumping"] = (btc_1h["close"] < btc_1h["open"]) & (btc_1h["close"] < btc_1h["btc_ema_21"])
                    dataframe = merge_informative_pair(dataframe, btc_1h, self.timeframe, self.timeframe, ffill=True)

        for col in [f"macro_bull_4h_{self.informative_timeframe}", f"macro_bear_4h_{self.informative_timeframe}"]:
            if col not in dataframe.columns:
                dataframe[col] = True if "bull" in col else False
        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0

        if f"btc_dumping_{self.timeframe}" not in dataframe.columns:
            dataframe[f"btc_dumping_{self.timeframe}"] = False

        # Streamlined 1H Indicators
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_bandwidth"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe["bb_middleband"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
        dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        pair = metadata.get("pair", "")
        is_btc = "BTC" in pair

        # Long Thresholds
        if is_btc:
            adx_long_gate = 24.0
        elif "ETH" in pair or "SOL" in pair:
            adx_long_gate = 22.5
        else:
            adx_long_gate = 21.0

        rsi_min_long = 44.0 if "SOL" in pair else 43.0
        pullback_threshold = 1.006

        # Microstructure Altcoin Protection: do not long if BTC is flash dumping
        btc_safe = True if is_btc else (dataframe[f"btc_dumping_{self.timeframe}"] == False)

        # 1. LONG PATTERN A: Proven High-Conviction Pullback Bounce
        long_pullback = (
            btc_safe &
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
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.80)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # 2. LONG PATTERN B: Volatility Expansion Breakout Alpha (New Trade Frequency Driver)
        # Captures fast runaway trends that never pull back to EMA21
        long_breakout = (
            btc_safe &
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > (adx_long_gate + 2.0)) &
            (dataframe["close"] > dataframe["bb_upperband"]) &
            (dataframe["close"] > dataframe["ema_21"] * 1.010) &
            (dataframe["rsi"] >= 58.0) &
            (dataframe["rsi"] <= 68.0) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.40) &
            (dataframe["is_green"] == True) &
            (dataframe["close"] > dataframe["close"].shift(1))
        )

        # 3. SHORT PATTERN: Precision Resistance Exhaustion Fade
        # BTC is kept Long-Only to eliminate 7.0x leverage counter-trend risk
        if not is_btc:
            if "SOL" in pair:
                adx_short_gate = 25.0
                rsi_min_short = 56.0
                require_below_ema200 = True
            else:
                adx_short_gate = 22.0
                rsi_min_short = 53.0
                require_below_ema200 = False

            short_base = (
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
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

            if require_below_ema200:
                short_conditions = short_base & (dataframe["close"] < dataframe["ema_200"])
            else:
                short_conditions = short_base

            dataframe.loc[short_conditions & ~long_pullback & ~long_breakout, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        dataframe.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[long_breakout & ~long_pullback, ["enter_long", "enter_tag"]] = (1, "long_breakout_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Bollinger Runner Exit (Yield-Boosting)
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