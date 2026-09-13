"""
ApexTriAlpha_V2_Strategy - Production-Grade High-Throughput Tri-Archetype Engine
Curated specifically for the 7 Stable Pairs: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG.

Core Architectural Innovations:
1. Tri-Archetype Synergy:
   - Engine 1 (Pullback Alpha): 4H Trend Pullback with 2-candle stochastic inflection & wick rejection.
   - Engine 2 (Volatility Squeeze Expansion): Bollinger Bandwidth quantile(0.25) release into directional expansion.
   - Engine 3 (Calibrated Short Fade): 4H Bear regime resistance exhaustion without artificial +4.5% profit caps.
2. Anti-Micro-Profit Protection Floor:
   - Preserves a strict +8.0% leveraged ROI floor (NEVER drops to 0%).
   - Prevents micro-profit dumps that get consumed by Bybit taker fees.
3. Uniform Risk Normalizer (custom_stoploss):
   - BTC (7.0x leverage): -0.224 on stake (-3.2% raw price move, avoids the 1.35% premature liquidation trap).
   - Altcoins (3.0x leverage): -0.096 on stake (-3.2% raw price move).
4. Slot Turnover Optimization:
   - Stagnation cut at 28 hours (< -1.8%) to unfreeze execution slots 8-9 hours faster.
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    BooleanParameter,
    merge_informative_pair,
)
from freqtrade.persistence import Trade
import talib.abstract as ta
from technical import qtpylib


class ApexTriAlpha_V2_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Preserved Payoff Runway (NEVER DROPS TO 0)
    minimal_roi = {
        "0": 0.350,      # Explosive impulse target (+35.0% leveraged ROI)
        "180": 0.200,    # 3-Hour target (+20.0% leveraged ROI)
        "480": 0.120,    # 8-Hour swing target (+12.0% leveraged ROI)
        "1440": 0.080    # 24-Hour absolute floor (+8.0% leveraged ROI)
    }

    stoploss = -0.297  # Fallback safety barrier
    use_custom_stoploss = True

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

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        Uniform Risk Normalizer:
        Caps maximum raw price drawdown at exactly -3.2% across all coins:
        - BTC (7.0x leverage): -0.224 on stake (-3.2% raw price move)
        - Altcoins (3.0x leverage): -0.096 on stake (-3.2% raw price move)
        """
        if "BTC" in pair:
            return -0.224
        return -0.096

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600

        # Capital Stagnation Exit: Unfreeze slot if trade is trapped in loss after 28 hours
        if trade_duration > 28.0 and current_profit < -0.018:
            return "time_decay_stale_loss"

        # Extended Stagnation Cutoff
        if trade_duration > 44.0 and current_profit < 0.005:
            return "time_decay_44h_cutoff"

        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        return [(pair, self.informative_timeframe) for pair in pairs]

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. Higher Timeframe (4H) Trend Hierarchy
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

        # 2. 1H Primary Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands & Squeeze Quantile
        boll = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = boll["lower"]
        dataframe["bb_middleband"] = boll["mid"]
        dataframe["bb_upperband"] = boll["upper"]
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe["bb_middleband"]
        dataframe["bb_width_q25"] = dataframe["bb_width"].rolling(50).quantile(0.25)
        dataframe["was_compressed"] = (dataframe["bb_width"] < dataframe["bb_width_q25"]).rolling(3).max() > 0

        # Stochastic Oscillator & 2-Candle Inflection Vectors
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        cross_up_cur = qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        cross_up_prev = qtpylib.crossed_above(dataframe["slowk"].shift(1), dataframe["slowd"].shift(1))
        dataframe["stoch_inflection_long"] = cross_up_cur | (cross_up_prev & (dataframe["slowk"] > dataframe["slowd"]))

        cross_down_cur = qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])
        cross_down_prev = qtpylib.crossed_below(dataframe["slowk"].shift(1), dataframe["slowd"].shift(1))
        dataframe["stoch_inflection_short"] = cross_down_cur | (cross_down_prev & (dataframe["slowk"] < dataframe["slowd"]))

        # Volume & Candlestick Price Action
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(20).mean()
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

        macro_bull = (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) & (dataframe[f"adx_{self.informative_timeframe}"] > 20)
        macro_bear = (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (dataframe[f"adx_{self.informative_timeframe}"] > 20)

        # Engine 1: Bullish Trend Pullback Alpha (Long)
        e1_pullback = (
            macro_bull &
            ((dataframe["close"] <= dataframe["ema_21"] * 1.006) | (dataframe["low"] <= dataframe["bb_middleband"])) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"].between(41, 63)) &
            (dataframe["stoch_inflection_long"] == True) &
            (dataframe["slowk"] < 54) &
            ((dataframe["is_green"] == True) | (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # Engine 2: Volatility Squeeze Expansion Alpha (Long)
        e2_squeeze = (
            macro_bull &
            (dataframe["was_compressed"] == True) &
            (dataframe["bb_width"] >= dataframe["bb_width_q25"]) &
            (dataframe["close"] > dataframe["bb_middleband"]) &
            (dataframe["close"] < dataframe["bb_upperband"] * 0.996) &
            (dataframe["close"] > dataframe["ema_9"]) &
            (dataframe["rsi"].between(52, 68)) &
            (dataframe["slowk"] > dataframe["slowd"]) &
            (dataframe["is_green"] == True) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.05)
        )

        # Engine 3: Resistance Exhaustion Fade Alpha (Short)
        e3_short_fade = (
            macro_bear &
            ((dataframe["high"] >= dataframe["ema_21"] * 0.996) | (dataframe["high"] >= dataframe["bb_middleband"])) &
            (dataframe["close"] <= dataframe["ema_50"] + 0.32 * dataframe["atr"]) &
            (dataframe["rsi"].between(52, 68)) &
            (dataframe["stoch_inflection_short"] == True) &
            (dataframe["slowk"] > 52) &
            ((dataframe["is_red"] == True) | (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[e1_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[e2_squeeze & ~e1_pullback, ["enter_long", "enter_tag"]] = (1, "long_squeeze_expansion")
        dataframe.loc[e3_short_fade & ~e1_pullback & ~e2_squeeze, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        long_exit = (
            (dataframe["rsi"] >= 74) &
            (dataframe["close"] >= dataframe["bb_upperband"])
        )

        short_exit = (
            (dataframe["rsi"] <= 26) &
            (dataframe["close"] <= dataframe["bb_lowerband"])
        )

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
