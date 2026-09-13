"""
AlphaRegime_Futures_Strategy - Quantitative Multi-Timeframe Trend & Volatility Strategy
Hyperoptable parameters for institutional parameter tuning
Base Timeframe: 1h
Informative Timeframe: 4h
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
import talib.abstract as ta
from technical import qtpylib


class AlphaRegime_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Entry Parameters
    buy_adx_4h = IntParameter(14, 30, default=18, space="buy")
    buy_adx_1h = IntParameter(12, 28, default=16, space="buy")
    buy_rsi_min = IntParameter(45, 58, default=50, space="buy")
    buy_rsi_max = IntParameter(60, 75, default=68, space="buy")

    # Hyperoptable Sell / Short Parameters
    sell_adx_4h = IntParameter(14, 30, default=18, space="sell")
    sell_adx_1h = IntParameter(12, 28, default=16, space="sell")
    sell_rsi_min = IntParameter(25, 40, default=32, space="sell")
    sell_rsi_max = IntParameter(42, 55, default=50, space="sell")

    # Dynamic Profit Harvesting
    minimal_roi = {
        "0": 0.12,
        "240": 0.08,
        "720": 0.05,
        "1440": 0.03,
        "2880": 0.015
    }

    # Stoploss
    stoploss = -0.040

    # Trailing Stop
    trailing_stop = True
    trailing_stop_positive = 0.020
    trailing_stop_positive_offset = 0.035
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

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        dataframe["macro_bull"] = (dataframe["ema_20"] > dataframe["ema_50"]) & (dataframe["close"] > dataframe["ema_200"])
        dataframe["macro_bear"] = (dataframe["ema_20"] < dataframe["ema_50"]) & (dataframe["close"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)

        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        dataframe["donchian_high"] = dataframe["high"].rolling(window=20).max()
        dataframe["donchian_low"] = dataframe["low"].rolling(window=20).min()

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        long_conditions = (
            (dataframe[f"macro_bull_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (qtpylib.crossed_above(dataframe["ema_9"], dataframe["ema_21"])) |
                (dataframe["close"] > dataframe["donchian_high"].shift(1))
            ) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (dataframe["adx"] > self.buy_adx_1h.value) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.95)
        )

        short_conditions = (
            (dataframe[f"macro_bear_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.sell_adx_4h.value) &
            (
                (qtpylib.crossed_below(dataframe["ema_9"], dataframe["ema_21"])) |
                (dataframe["close"] < dataframe["donchian_low"].shift(1))
            ) &
            (dataframe["rsi"] <= self.sell_rsi_max.value) &
            (dataframe["rsi"] >= self.sell_rsi_min.value) &
            (dataframe["adx"] > self.sell_adx_1h.value) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.95)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_alpha_trend")
        dataframe.loc[short_conditions, ["enter_short", "enter_tag"]] = (1, "short_alpha_trend")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        exit_long_conditions = (
            (qtpylib.crossed_below(dataframe["ema_9"], dataframe["ema_50"])) |
            (dataframe["rsi"] > 80)
        )

        exit_short_conditions = (
            (qtpylib.crossed_above(dataframe["ema_9"], dataframe["ema_50"])) |
            (dataframe["rsi"] < 20)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_long_reversal")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_reversal")

        return dataframe
