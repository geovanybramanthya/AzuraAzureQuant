"""
ScalpAlpha_5m_Strategy - High-Frequency Mean-Reversion Scalper
Architecture:
- Base Timeframe: 5m (Precision Dip Execution)
- Informative Timeframe: 1h (Macro Trend Gate)
- Logic: Buys oversold RSI & Bollinger Band extremes during 1h uptrend.
- Target Frequency: 1-3 trades per day with fast profit taking.
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


class ScalpAlpha_5m_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "5m"
    informative_timeframe = "1h"

    # Hyperoptable Buy / Entry Parameters
    buy_rsi_max = IntParameter(28, 42, default=35, space="buy")
    buy_stoch_max = IntParameter(20, 48, default=35, space="buy")
    buy_adx_1h = IntParameter(14, 28, default=18, space="buy")

    # Fast Scalp ROI Table (in minutes)
    minimal_roi = {
        "0": 0.022,       # 2.2% instant target
        "20": 0.015,      # 1.5% after 20 mins
        "45": 0.010,      # 1.0% after 45 mins
        "90": 0.006,      # 0.6% after 1.5 hours
        "180": 0.003      # 0.3% after 3 hours
    }

    # Strict Protective Stoploss (-2.0% max loss per scalp)
    stoploss = -0.020

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.006            # Lock +0.6% profit once +1.0% is reached
    trailing_stop_positive_offset = 0.010
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
        Auto-cut scalps if stagnant after 4 hours to free margin for fresh dips.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration > 4.0 and current_profit < 0.002:
            return "scalp_time_cutoff_4h"
        return None

    def informative_1h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Macro Trend Gate: 1h Bullish
        dataframe["macro_bull_1h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 1h Informative
        informative_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_1h = self.informative_1h_indicators(informative_1h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, self.informative_timeframe, ffill=True)

        # 5m Indicators
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
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # 5m Dip Scalp Rules:
        # 1. 1h Macro Trend is Bullish & 1h ADX > threshold
        # 2. 5m Price dips to or below Lower Bollinger Band
        # 3. 5m RSI in oversold zone (<= buy_rsi_max)
        # 4. Stochastic crosses upward from oversold
        # 5. Bullish bounce candle (Green OR lower wick rejection)
        long_conditions = (
            (dataframe[f"macro_bull_1h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_1h.value) &
            (
                (dataframe["close"] <= dataframe["bb_lowerband"] * 1.002) |
                (dataframe["low"] <= dataframe["bb_lowerband"])
            ) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < self.buy_stoch_max.value) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_dip_scalp")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Scalp Take Profit on Mean Reversion (Mid/Upper Bollinger + RSI > 65)
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_middleband"]) &
            (dataframe["rsi"] > 65)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_mean_reversion")

        return dataframe
