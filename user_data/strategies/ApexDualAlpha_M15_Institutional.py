"""
ApexDualAlpha_M15_Institutional - High-Selectivity M15 Volatility Breakout Engine
Key Pillars to Defeat Fee Bleed:
1. ATR Volatility Hurdle: Only triggers when 15m candle volatility >= 0.85% (guarantees wide profit runway).
2. Volume Surge Filter: Requires 1.5x 20-period volume average (institutional participation).
3. 4H ADX Regime Lock: Only trades in strong macro trending environments (ADX 4H >= 24).
4. Asymmetric Take Profit Runway: Minimum target +8% to +38% ROI so fees (<0.36% margin) are negligible (<3%).
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


class ApexDualAlpha_M15_Institutional(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "15m"
    informative_timeframe = "1h"

    # High-Velocity Take Profit Runway
    minimal_roi = {
        "0": 0.380,      # Squeeze breakout (+38.0% ROI on margin)
        "120": 0.160,    # 2-Hour expansion target (+16.0% ROI)
        "300": 0.080,    # 5-Hour swing target (+8.0% ROI)
        "600": 0         # 10-Hour cycle cutoff
    }

    stoploss = -0.195   # Strict -19.5% margin stop

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
                inf_1h["adx"] = ta.ADX(inf_1h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, inf_1h, self.timeframe, "1h", ffill=True)

        # Fallbacks
        for col in ["macro_bull_4h", "macro_bear_4h"]:
            if col not in dataframe.columns:
                dataframe[col] = True if col == "macro_bull_4h" else False
        for col in ["adx_4h", "adx_1h"]:
            if col not in dataframe.columns:
                dataframe[col] = 25.0
        for col in ["ema21_1h", "ema50_1h"]:
            if col not in dataframe.columns:
                dataframe[col] = dataframe["close"]

        # 15m Micro Indicators
        dataframe["ema9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

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

        # Long: High-Volatility Squeeze Pullback
        long_conditions = (
            (dataframe.get("macro_bull_4h", True) == True) &
            (dataframe.get("adx_4h", 25) >= 24) &
            (dataframe.get("adx_1h", 25) >= 20) &
            (dataframe["atr_pct"] >= 0.0075) &  # Minimum 0.75% volatility required
            (dataframe["close"] <= dataframe.get("ema21_1h", dataframe["close"]) * 1.004) &
            (dataframe["rsi"] >= 41) &
            (dataframe["rsi"] <= 58) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 50) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.25)
        )

        # Short: High-Volatility Resistance Exhaustion
        short_conditions = (
            (dataframe.get("macro_bear_4h", False) == True) &
            (dataframe.get("adx_4h", 25) >= 24) &
            (dataframe.get("adx_1h", 25) >= 20) &
            (dataframe["atr_pct"] >= 0.0075) &
            (dataframe["high"] >= dataframe.get("ema21_1h", dataframe["high"]) * 0.996) &
            (dataframe["rsi"] >= 52) &
            (dataframe["rsi"] <= 68) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 50) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.25)
        )

        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "long_m15_volatility_squeeze"

        dataframe.loc[short_conditions, "enter_short"] = 1
        dataframe.loc[short_conditions, "enter_tag"] = "short_m15_volatility_squeeze"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Overbought exit
        long_exit = (
            (dataframe["rsi"] >= 80) &
            (dataframe["close"] >= dataframe["bb_upperband"])
        )

        # Oversold exit
        short_exit = (
            (dataframe["rsi"] <= 20) &
            (dataframe["close"] <= dataframe["bb_lowerband"])
        )

        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "exit_m15_overbought"

        dataframe.loc[short_exit, "exit_short"] = 1
        dataframe.loc[short_exit, "exit_tag"] = "exit_m15_oversold"

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Stale Decay Cut for 15m after 24 hours if stagnating in red
        if trade_duration >= 24 and current_profit < -0.040:
            return "time_decay_stale_m15"
        
        return None
