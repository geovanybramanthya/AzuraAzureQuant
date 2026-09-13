"""
ApexDualAlpha_15m_Strategy - Institutional 15-Minute Sub-Alpha Engine
Designed for Intraday Opportunity Capture with Multi-Timeframe (15m + 1h + 4h) Confluence.
Fee-Protected: Targets high-velocity swings (+6% to +35% ROI) to render Bybit 0.12% roundtrip fees negligible.
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


class ApexDualAlpha_15m_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "15m"
    informative_timeframe = "1h"

    # Minimal ROI structured for 15m intraday swings
    minimal_roi = {
        "0": 0.280,      # Explosive impulse target (+28.0% ROI on margin)
        "180": 0.115,    # 3-Hour maturity target (+11.5% ROI)
        "360": 0.055,    # 6-Hour harvest target (+5.5% ROI)
        "720": 0         # 12-Hour session expiration
    }

    stoploss = -0.195   # Tight -19.5% margin stoploss for 15m

    trailing_stop = True
    trailing_stop_positive = 0.085
    trailing_stop_positive_offset = 0.125
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
            # 1. 4H Macro Informative
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
            if not inf_4h.empty:
                inf_4h["ema50"] = ta.EMA(inf_4h, timeperiod=50)
                inf_4h["ema200"] = ta.EMA(inf_4h, timeperiod=200)
                inf_4h["adx"] = ta.ADX(inf_4h, timeperiod=14)
                inf_4h["macro_bull"] = (inf_4h["close"] > inf_4h["ema50"]) & (inf_4h["ema50"] > inf_4h["ema200"])
                inf_4h["macro_bear"] = (inf_4h["close"] < inf_4h["ema50"]) & (inf_4h["ema50"] < inf_4h["ema200"])
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, "4h", ffill=True)
            
            # 2. 1H Intermediate Informative
            inf_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1h")
            if not inf_1h.empty:
                inf_1h["ema21"] = ta.EMA(inf_1h, timeperiod=21)
                inf_1h["ema50"] = ta.EMA(inf_1h, timeperiod=50)
                inf_1h["rsi"] = ta.RSI(inf_1h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, inf_1h, self.timeframe, "1h", ffill=True)

        # Fallback columns if merge missing
        for col in ["macro_bull_4h", "macro_bear_4h"]:
            if col not in dataframe.columns:
                dataframe[col] = True if col == "macro_bull_4h" else False
        for col in ["adx_4h", "rsi_1h"]:
            if col not in dataframe.columns:
                dataframe[col] = 25.0 if col == "adx_4h" else 50.0
        for col in ["ema21_1h", "ema50_1h"]:
            if col not in dataframe.columns:
                dataframe[col] = dataframe["close"]

        # 15m Micro Indicators
        dataframe["ema9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
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

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Long: 4H Bullish + 1H Discount Pullback + 15m Stochastic Trigger
        long_conditions = (
            (dataframe.get("macro_bull_4h", True) == True) &
            (dataframe.get("adx_4h", 25) >= 18) &
            (dataframe["close"] <= dataframe.get("ema21_1h", dataframe["close"]) * 1.008) &
            (dataframe["rsi"] >= 38) &
            (dataframe["rsi"] <= 62) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 58) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        # Short: 4H Bearish + 1H Resistance Rejection + 15m Stochastic Trigger
        short_conditions = (
            (dataframe.get("macro_bear_4h", False) == True) &
            (dataframe.get("adx_4h", 25) >= 18) &
            (dataframe["high"] >= dataframe.get("ema21_1h", dataframe["high"]) * 0.992) &
            (dataframe["rsi"] >= 45) &
            (dataframe["rsi"] <= 68) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 45) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "long_15m_confluence"

        dataframe.loc[short_conditions, "enter_short"] = 1
        dataframe.loc[short_conditions, "enter_tag"] = "short_15m_confluence"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Overbought exit for Longs on sudden spikes
        long_exit = (
            (dataframe["rsi"] >= 78) &
            (dataframe["close"] >= dataframe["bb_upperband"])
        )

        # Oversold exit for Shorts on sudden flash dumps
        short_exit = (
            (dataframe["rsi"] <= 24) &
            (dataframe["close"] <= dataframe["bb_lowerband"])
        )

        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "exit_15m_overbought"

        dataframe.loc[short_exit, "exit_short"] = 1
        dataframe.loc[short_exit, "exit_tag"] = "exit_15m_oversold"

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Stale Decay Cut for 15m after 18 hours if stagnating in red
        if trade_duration >= 18 and current_profit < -0.040:
            return "time_decay_stale_15m"
        
        return None
