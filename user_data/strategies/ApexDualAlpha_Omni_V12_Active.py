"""
ApexDualAlpha_Omni_V12_Active - Pullback Paradox Resolution & Chop-Resilient Calibration
Curated for Bybit Linear Futures 7-Pair Universe: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG.

Key Structural Advancements:
1. Resolves the Pullback Paradox:
   - Macro trend orientation is defined structurally: EMA_50_4h > EMA_200_4h (macro bull)
   - Pullback tolerance: Price is permitted to test dynamic 4H EMA50 support zone (close >= EMA50_4h * 0.985)
2. 2-Candle Crossover Window:
   - Captures entries even if confirmation occurs 1 candle after instantaneous cross.
3. Adaptive ADX Regime:
   - Calibrated to > 14.0 (BTC > 15.0) to operate through consolidation compressions.
4. Preserves High-Yield ROI & Asymmetric Payoff:
   - Same Take Profit ladder, trailing stop, and dynamic leverage (7x BTC, 3x Altcoins).
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


class ApexDualAlpha_Omni_V12_Active(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # High-Yield Precision Profit Ladder
    minimal_roi = {
        "0": 0.526,
        "467": 0.161,
        "688": 0.090,
        "1173": 0.011
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
        
        # 1. Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # 2. Surgical Volatility Scalp for PAXG (Gold bullion low-volatility cycle)
        if "PAXG" in pair and trade_duration >= 4.0 and current_profit >= 0.0065:
            return "paxg_volatility_tp"

        # 3. Structural Profit Banking for ETH (protecting against macro ETH/BTC bleed)
        if "ETH" in pair and trade_duration >= 16.0 and current_profit >= 0.0080:
            return "eth_profit_bank_tp"

        # 4. Stale decay cutoff at 36 hours
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        # 5. Micro-profit closure at 48 hours
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        return [(pair, self.informative_timeframe) for pair in pairs]

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        
        # Structural macro trend orientation: decoupled from immediate close position
        dataframe["macro_bull_4h"] = dataframe["ema_50"] > dataframe["ema_200"]
        dataframe["macro_bear_4h"] = dataframe["ema_50"] < dataframe["ema_200"]
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if hasattr(self, "dp") and self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

        for col in [f"macro_bull_4h_{self.informative_timeframe}", f"macro_bear_4h_{self.informative_timeframe}"]:
            if col not in dataframe.columns:
                dataframe[col] = True if "bull" in col else False
        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0
        if f"ema_50_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"ema_50_{self.informative_timeframe}"] = dataframe["close"]

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

        # Long Configuration - Dynamic Chop Tolerance
        adx_long_gate = 15.0 if "BTC" in pair else 14.0
        rsi_min_long = 42.0 if "SOL" in pair else 40.0
        pullback_threshold = 1.008

        # 2-candle crossover window
        stoch_cross_up = qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        cross_window_up = stoch_cross_up | ((dataframe["slowk"].shift(1) > dataframe["slowd"].shift(1)) & (dataframe["slowk"].shift(2) <= dataframe["slowd"].shift(2)))

        # 1. LONG: Paradox-Resolved Bullish Pullback
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > adx_long_gate) &
            (dataframe["close"] >= dataframe[f"ema_50_{self.informative_timeframe}"] * 0.985) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * pullback_threshold) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.988) &
            (dataframe["rsi"] >= rsi_min_long) &
            (dataframe["rsi"] <= 64.0) &
            cross_window_up &
            (dataframe["slowk"] < 58.0) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.65)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.65)
        )

        # 2. SHORT: Resistance Exhaustion Fade
        if "BTC" not in pair:
            adx_short_gate = 16.0 if "SOL" in pair else 15.0
            rsi_min_short = 54.0 if "SOL" in pair else 52.0

            stoch_cross_down = qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])
            cross_window_down = stoch_cross_down | ((dataframe["slowk"].shift(1) < dataframe["slowd"].shift(1)) & (dataframe["slowk"].shift(2) >= dataframe["slowd"].shift(2)))

            short_base = (
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                (dataframe[f"adx_{self.informative_timeframe}"] > adx_short_gate) &
                (dataframe["close"] <= dataframe[f"ema_50_{self.informative_timeframe}"] * 1.015) &
                (
                    (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                    (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                ) &
                (dataframe["close"] <= dataframe["ema_50"] + 0.35 * dataframe["atr"]) &
                (dataframe["rsi"] >= rsi_min_short) &
                (dataframe["rsi"] <= 68.0) &
                cross_window_down &
                (dataframe["slowk"] > 52.0) &
                (
                    (dataframe["is_red"] == True) |
                    (dataframe["upper_wick"] > dataframe["body_size"] * 0.65)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.65)
            )

            dataframe.loc[short_base & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")

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
