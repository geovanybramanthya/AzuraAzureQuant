"""
PrecisionAlpha_Strategy - Institutional Multi-Timeframe Engine
Architecture:
- Base: 1h (Execution & Price Action Bounce on EMA 21/50)
- Informative: 4h (Trend Momentum) & 1d (Macro Cycle Gating)
- Short Protection: Shorts are ONLY permitted during confirmed 1d & 4h macro bear markets to eliminate short squeeze losses.
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


class PrecisionAlpha_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # High-Expectancy ROI Runway
    minimal_roi = {
        "0": 0.080,       # 8% target
        "180": 0.050,     # 5% after 3 hours
        "360": 0.035,     # 3.5% after 6 hours
        "720": 0.022,     # 2.2% after 12 hours
        "1440": 0.012     # 1.2% after 24 hours
    }

    # Strict Protective Stoploss
    stoploss = -0.035

    # Dynamic Break-Even & Profit Lock Stoploss
    trailing_stop = False
    use_custom_stoploss = True

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

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        Dynamic Break-Even Stop:
        - If profit >= +2.8%: Lock +1.6% profit
        - If profit >= +1.2%: Move stop to Break-Even (+0.2% to cover fees)
        - Otherwise: Maintain hard stoploss (-3.5%)
        """
        if current_profit >= 0.028:
            return -0.012  # Lock +1.6%
        elif current_profit >= 0.012:
            return 0.002   # Break-Even (+0.2%)
        return self.stoploss

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """
        Time-Decay Exit: Cut stale trades if they haven't moved after 36 hours and are in negative.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        if trade_duration > 36 and current_profit < -0.010:
            return "time_decay_stale_exit"
        
        if trade_duration > 48 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_1d_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["macro_bear_1d"] = dataframe["close"] < dataframe["ema_50"]
        dataframe["macro_bull_1d"] = dataframe["close"] > dataframe["ema_50"]
        return dataframe

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"]) & (dataframe["adx"] > 16)
        dataframe["macro_bear_4h"] = (dataframe["ema_50"] < dataframe["ema_200"]) & (dataframe["close"] < dataframe["ema_50"]) & (dataframe["adx"] > 18)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 1d Informative
        informative_1d = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1d")
        informative_1d = self.informative_1d_indicators(informative_1d, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1d, self.timeframe, "1d", ffill=True)

        # Merge 4h Informative
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, "4h", ffill=True)

        # 1h Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        # Stochastic Momentum Index
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Price Action Candle Metrics
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Precision Long:
        long_conditions = (
            (dataframe["macro_bull_4h_4h"] == True) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.008) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= 40) &
            (dataframe["rsi"] <= 58) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 50) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.75)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        # Precision Short (Requires 1d Macro Bear + 4h Bear):
        short_conditions = (
            (dataframe["macro_bear_1d_1d"] == True) &
            (dataframe["macro_bear_4h_4h"] == True) &
            (
                (dataframe["close"] >= dataframe["ema_21"] * 0.992) |
                (dataframe["high"] >= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] <= dataframe["ema_50"] * 1.010) &
            (dataframe["rsi"] <= 58) &
            (dataframe["rsi"] >= 40) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 50) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.75)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_precision_dip")
        dataframe.loc[short_conditions, ["enter_short", "enter_tag"]] = (1, "short_precision_rip")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 74)
        )

        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 26)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_long_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_tp")

        return dataframe

