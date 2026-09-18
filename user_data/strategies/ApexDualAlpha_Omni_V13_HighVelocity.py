"""
ApexDualAlpha_Omni_V13_HighVelocity - High-Frequency Capital Velocity Engine
Curated for Bybit Linear Futures 7-Pair Universe: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG.

Key Architectural Advancements:
1. Fast-Harvest ROI Ladder:
   - Locks in gains earlier (3h @ +14%, 6h @ +8%, 12h @ +4%), freeing slots 2x faster.
2. Stagnation Slot Unfreezer:
   - Cuts trades stagnating in negative territory after 18 hours (< -1.5%) to prevent capital lockup.
3. 2-Candle Stochastic Inflection Window:
   - Eliminates single-candle timing misses by accepting confirmed crosses within 2 candles.
4. Dynamic Asymmetric Leverage:
   - 7.0x on BTC (low beta), 3.0x on Altcoins & Gold (high beta/volatility).
5. Strict 4H Trend Confluence with ATR Volatility Hurdle:
   - Rejects dead chop while riding high-probability momentum waves.
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


class ApexDualAlpha_Omni_V13_HighVelocity(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Fast-Harvest Capital Velocity Profit Ladder
    minimal_roi = {
        "0": 0.380,      # Immediate explosive impulse (+38% ROI on margin)
        "180": 0.140,    # 3-Hour harvest target (+14% ROI)
        "360": 0.080,    # 6-Hour swing target (+8% ROI)
        "720": 0.040,    # 12-Hour session target (+4% ROI)
        "1440": 0.010    # 24-Hour maturity target (+1% ROI)
    }

    stoploss = -0.297   # Wide protective barrier for isolated margin

    trailing_stop = True
    trailing_stop_positive = 0.160
    trailing_stop_positive_offset = 0.200
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

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600

        # 1. Fast profit taking for Shorts on sudden explosive dumps (> +4.0% profit)
        if trade.is_short and current_profit > 0.040:
            return "short_explosive_dump_tp"

        # 2. Surgical Volatility Scalp for PAXG
        if "PAXG" in pair and trade_duration >= 4.0 and current_profit >= 0.0065:
            return "paxg_volatility_tp"

        # 3. Structural Profit Banking for ETH
        if "ETH" in pair and trade_duration >= 14.0 and current_profit >= 0.0080:
            return "eth_profit_bank_tp"

        # 4. Rapid Stagnation Cutoff at 18 hours (unfreezes execution slots)
        if trade_duration > 18.0 and current_profit < -0.015:
            return "stagnation_18h_cutoff"

        # 5. Flat Time-Decay Cutoff at 32 hours
        if trade_duration > 32.0 and current_profit < 0.004:
            return "time_decay_32h_cutoff"

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

        # Streamlined 1H Indicators
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # 2-Candle Stochastic Inflection Window
        stoch_cross_up_0 = qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])
        stoch_cross_up_1 = qtpylib.crossed_above(dataframe["slowk"].shift(1), dataframe["slowd"].shift(1))
        dataframe["k_cross_up_window"] = stoch_cross_up_0 | (stoch_cross_up_1 & (dataframe["slowk"] > dataframe["slowd"]))

        stoch_cross_down_0 = qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])
        stoch_cross_down_1 = qtpylib.crossed_below(dataframe["slowk"].shift(1), dataframe["slowd"].shift(1))
        dataframe["k_cross_down_window"] = stoch_cross_down_0 | (stoch_cross_down_1 & (dataframe["slowk"] < dataframe["slowd"]))

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()
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

        pair = metadata.get("pair", "")

        # Long ADX Gates
        if "BTC" in pair:
            adx_long_gate = 20.0
        elif "ETH" in pair or "SOL" in pair:
            adx_long_gate = 19.0
        else:
            adx_long_gate = 18.0

        rsi_min_long = 43.0 if "SOL" in pair else 41.0
        pullback_threshold = 1.008

        # 1. LONG: High-Velocity Trend Pullback
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > adx_long_gate) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * pullback_threshold) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.988) &
            (dataframe["rsi"] >= rsi_min_long) &
            (dataframe["rsi"] <= 64.0) &
            (dataframe["k_cross_up_window"] == True) &
            (dataframe["slowk"] < 56.0) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.65)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.65)
        )

        # 2. SHORT: Resistance Exhaustion Fade
        # BTC is kept Long-Only for safety
        if "BTC" not in pair:
            adx_short_gate = 21.0 if "SOL" in pair else 19.0
            rsi_min_short = 54.0 if "SOL" in pair else 52.0

            short_base = (
                (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
                (dataframe[f"adx_{self.informative_timeframe}"] > adx_short_gate) &
                (
                    (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                    (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                ) &
                (dataframe["close"] <= dataframe["ema_50"] + 0.32 * dataframe["atr"]) &
                (dataframe["rsi"] >= rsi_min_short) &
                (dataframe["rsi"] <= 68.0) &
                (dataframe["k_cross_down_window"] == True) &
                (dataframe["slowk"] > 52.0) &
                (
                    (dataframe["is_red"] == True) |
                    (dataframe["upper_wick"] > dataframe["body_size"] * 0.65)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.68)
            )

            if "SOL" in pair:
                short_conditions = short_base & (dataframe["close"] < dataframe["ema_200"])
            else:
                short_conditions = short_base

            dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        long_exit = (
            (dataframe["rsi"] >= 72) &
            (dataframe["close"] >= dataframe["bb_upperband"] * 0.998)
        )

        short_exit = (
            (dataframe["rsi"] <= 28) &
            (dataframe["close"] <= dataframe["bb_lowerband"] * 1.002)
        )

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
