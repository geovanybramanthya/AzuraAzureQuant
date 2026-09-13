"""
DualAlpha_Futures_Strategy - Bidirectional Long and Short Quantitative Alpha Engine
Architecture:
- Base Timeframe: 1h
- Informative Timeframe: 4h
- Pairs: ETH/USDT, ADA/USDT, LINK/USDT, SOL/USDT
- Leverage: 3.0x Isolated
- Direction: Long on 4H Bullish Dip + Short on 4H Bearish Rip
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


class DualAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Long Parameters
    buy_rsi_min = IntParameter(40, 52, default=45, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=62, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=20, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=45, space="buy")

    # Short Parameters
    short_rsi_min = IntParameter(48, 65, default=52, space="sell")
    short_rsi_max = IntParameter(60, 75, default=68, space="sell")
    short_stoch_min = IntParameter(45, 75, default=55, space="sell")

    # High-Expectancy ROI Runway (in minutes)
    minimal_roi = {
        "0": 0.085,
        "180": 0.052,
        "420": 0.035,
        "960": 0.020,
        "1440": 0.008
    }

    # Strict Protective Stoploss (-3.5%)
    stoploss = -0.035

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.028
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
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, self.informative_timeframe) for pair in pairs]

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Macro Trend Gates
        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        dataframe["macro_bear_4h"] = (dataframe["ema_50"] < dataframe["ema_200"]) & (dataframe["close"] < dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if self.dp:
            informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not informative_4h.empty and len(informative_4h) > 50:
                informative_4h = self.informative_4h_indicators(informative_4h, metadata)
                dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)
            else:
                dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
                dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
                dataframe[f"adx_{self.informative_timeframe}"] = 25
        else:
            dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
            dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
            dataframe[f"adx_{self.informative_timeframe}"] = 25

        # 1h Execution Indicators
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

        # Stochastic (14, 3, 3)
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

        # 1. Sinyal LONG: High-Conviction Bullish Pullback
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.006) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < self.buy_stoch_max.value) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # 2. Sinyal SHORT: High-Conviction Bearish Resistance Rejection (Sell the Rip)
        short_conditions = (
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (dataframe["close"] >= dataframe["ema_21"] * 0.994) |
                (dataframe["high"] >= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] <= dataframe["ema_50"] * 1.010) &
            (dataframe["rsi"] >= self.short_rsi_min.value) &
            (dataframe["rsi"] <= self.short_rsi_max.value) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > self.short_stoch_min.value) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_rip_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Exit Long on Upper BB Expansion + RSI Overbought
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 74)
        )

        # Exit Short on Lower BB Expansion + RSI Oversold
        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 26)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
