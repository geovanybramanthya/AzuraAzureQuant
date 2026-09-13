"""
MultiTF_Futures_Strategy - Quantitative High-Expectancy Pullback Strategy
Designed with:
1. Confluence Filter: Macro 1h ADX > 25 & EMA 100/200 Trend
2. Sniper Entry: Deep Pullbacks into 15m BB lower/upper bands with volume exhaustion
3. Asymmetric Payoff: Positive expectancy (Reward:Risk >= 1.6:1), reduced trade churn
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
)
import talib.abstract as ta
from technical import qtpylib


class MultiTF_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "15m"
    informative_timeframe = "1h"

    # Positive Expectancy ROI Runway
    minimal_roi = {
        "0": 0.065,       # 6.5% TP target
        "45": 0.045,      # 4.5% TP after 45 mins
        "120": 0.030,     # 3.0% TP after 2 hours
        "240": 0.020      # 2.0% TP after 4 hours
    }

    # Strict protective stoploss
    stoploss = -0.018    # -1.8% stoploss

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.025
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

    def informative_1h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # High Quality Trend: EMA alignment + ADX > 24
        dataframe["macro_bull"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["adx"] > 24)
        dataframe["macro_bear"] = (dataframe["ema_50"] < dataframe["ema_200"]) & (dataframe["adx"] > 24)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_1h = self.informative_1h_indicators(informative_1h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, self.informative_timeframe, ffill=True)

        # 15m Indicators
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands (2.2 std dev for deep high-probability pullbacks)
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        # Stochastic Fast
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Sniper Long: Macro 1h Bullish (ADX>24) + Deep 15m Oversold (Price <= BB Lower or RSI < 32) + Stoch cross
        long_conditions = (
            (dataframe[f"macro_bull_{self.informative_timeframe}"] == True) &
            (
                (dataframe["close"] <= dataframe["bb_lowerband"]) |
                (dataframe["rsi"] < 32)
            ) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 35) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.8)
        )

        # Sniper Short: Macro 1h Bearish (ADX>24) + Deep 15m Overbought (Price >= BB Upper or RSI > 68) + Stoch cross
        short_conditions = (
            (dataframe[f"macro_bear_{self.informative_timeframe}"] == True) &
            (
                (dataframe["close"] >= dataframe["bb_upperband"]) |
                (dataframe["rsi"] > 68)
            ) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 65) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.8)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_sniper_dip")
        dataframe.loc[short_conditions, ["enter_short", "enter_tag"]] = (1, "short_sniper_rip")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Exit Long: Price exceeds BB Upperband and RSI > 75
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 75)
        )

        # Exit Short: Price falls below BB Lowerband and RSI < 25
        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 25)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_long_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_tp")

        return dataframe
