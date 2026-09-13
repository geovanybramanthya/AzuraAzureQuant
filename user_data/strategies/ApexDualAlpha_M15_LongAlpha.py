"""
ApexDualAlpha_M15_LongAlpha - Pure High-Conviction Long M15 Engine
Eliminates 15m short squeeze whipsaws and focuses 100% on 4H Bullish Squeeze Pullbacks.
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


class ApexDualAlpha_M15_LongAlpha(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "15m"
    informative_timeframe = "1h"

    minimal_roi = {
        "0": 0.380,
        "120": 0.160,
        "300": 0.080,
        "600": 0
    }

    stoploss = -0.195

    trailing_stop = True
    trailing_stop_positive = 0.120
    trailing_stop_positive_offset = 0.160
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

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if (hasattr(self, "dp") and self.dp) else []
        informative_pairs = []
        for pair in pairs:
            informative_pairs.append((pair, self.informative_timeframe))
            informative_pairs.append((pair, "4h"))
        return informative_pairs

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if hasattr(self, "dp") and self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
            if not inf_4h.empty:
                inf_4h["ema50"] = ta.EMA(inf_4h, timeperiod=50)
                inf_4h["ema200"] = ta.EMA(inf_4h, timeperiod=200)
                inf_4h["adx"] = ta.ADX(inf_4h, timeperiod=14)
                inf_4h["macro_bull"] = (inf_4h["close"] > inf_4h["ema50"]) & (inf_4h["ema50"] > inf_4h["ema200"])
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, "4h", ffill=True)
            
            inf_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1h")
            if not inf_1h.empty:
                inf_1h["ema21"] = ta.EMA(inf_1h, timeperiod=21)
                inf_1h["ema50"] = ta.EMA(inf_1h, timeperiod=50)
                inf_1h["adx"] = ta.ADX(inf_1h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, inf_1h, self.timeframe, "1h", ffill=True)

        if "macro_bull_4h" not in dataframe.columns:
            dataframe["macro_bull_4h"] = True
        if "adx_4h" not in dataframe.columns:
            dataframe["adx_4h"] = 25.0
        if "ema21_1h" not in dataframe.columns:
            dataframe["ema21_1h"] = dataframe["close"]

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        long_conditions = (
            (dataframe.get("macro_bull_4h", True) == True) &
            (dataframe.get("adx_4h", 25) >= 22) &
            (dataframe["atr_pct"] >= 0.0070) &
            (dataframe["close"] <= dataframe.get("ema21_1h", dataframe["close"]) * 1.006) &
            (dataframe["rsi"] >= 41) &
            (dataframe["rsi"] <= 60) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 55) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.15)
        )

        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "long_m15_breakout"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        long_exit = (
            (dataframe["rsi"] >= 80) &
            (dataframe["close"] >= dataframe["bb_upperband"])
        )

        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "exit_m15_overbought"

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration >= 24 and current_profit < -0.040:
            return "time_decay_stale_m15"
        return None
