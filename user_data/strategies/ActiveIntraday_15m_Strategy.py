"""
ActiveIntraday_15m_Strategy - Active Intraday Trend & Pullback Engine
Architecture:
- Base: 15m (Active Intraday Execution)
- Informative: 4h (Macro Trend Gate)
- Asymmetric R:R: Targets +3.5% to +6.0% with tight -2.5% stop to eliminate fee drag.
- Frequency: ~1-2 trades per day with high winrate.
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


class ActiveIntraday_15m_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "15m"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Entry Parameters
    buy_rsi_min = IntParameter(36, 48, default=42, space="buy")
    buy_rsi_max = IntParameter(50, 64, default=58, space="buy")
    buy_stoch_max = IntParameter(25, 55, default=40, space="buy")
    buy_adx_4h = IntParameter(14, 26, default=18, space="buy")

    # High-Expectancy ROI Runway (in minutes)
    minimal_roi = {
        "0": 0.055,       # 5.5% instant target
        "60": 0.038,      # 3.8% after 1 hour
        "180": 0.024,     # 2.4% after 3 hours
        "360": 0.015,     # 1.5% after 6 hours
        "720": 0.008      # 0.8% after 12 hours
    }

    # Strict Protective Hard Stoploss
    stoploss = -0.026

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.012            # Lock +1.2% once +2.2% is reached
    trailing_stop_positive_offset = 0.022
    trailing_only_offset_is_reached = True

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
        return 2.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """
        Auto-cut trades held > 12 hours if stagnant to recycle margin.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration > 12.0 and current_profit < 0.004:
            return "intraday_time_cutoff_12h"
        return None

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Macro Trend Gate: 4h Bullish
        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 4h Informative
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)

        # 15m Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands (20, 2.0 std)
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        # Donchian 24-Period S/R Channel (6-hour Support/Resistance)
        dataframe["support_6h"] = dataframe["low"].rolling(window=24).min()
        dataframe["resistance_6h"] = dataframe["high"].rolling(window=24).max()

        # Stochastic (14, 3, 3)
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Price Action Candle Metrics
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Sinyal 1: Trend Pullback Engine (Saat 4H Bullish & ADX Kuat)
        trend_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.004) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.992) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < self.buy_stoch_max.value) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.60)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # Sinyal 2: Support-Bounce Engine (Saat 4H Sideways / Konsolidasi S/R)
        range_conditions = (
            (dataframe[f"adx_{self.informative_timeframe}"] <= self.buy_adx_4h.value) &
            (
                (dataframe["close"] <= dataframe["bb_lowerband"] * 1.003) |
                (dataframe["low"] <= dataframe["support_6h"] * 1.002)
            ) &
            (dataframe["rsi"] <= 38) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 40) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[trend_conditions, ["enter_long", "enter_tag"]] = (1, "trend_pullback")
        dataframe.loc[range_conditions & ~trend_conditions, ["enter_long", "enter_tag"]] = (1, "range_support_bounce")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Take Profit on Upper Bollinger Expansion + Overbought RSI
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 72)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")

        return dataframe
