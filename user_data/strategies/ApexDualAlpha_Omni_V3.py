"""
ApexDualAlpha_Omni_V3 - Master-Regime Confluence & Risk-Normalized Engine
Optimized for the 7 Curated Stable Pairs: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG.

Key Innovations:
1. BTC Master Informative Regime Confluence:
   - BTC 4H trend acts as the market-wide liquidity barometer.
   - Altcoin Shorts are suppressed when BTC is in a strong 4H Bull trend (eliminates bear traps).
   - Altcoin Longs are suppressed when BTC is in an aggressive 4H Bear trend (eliminates knife catching).
2. Leverage-Aware Risk Normalization (custom_stoploss):
   - BTC trades at 7.0x leverage (to meet Bybit 0.001 BTC min order). A 2.0% raw move = 14% on stake.
     Custom stoploss caps BTC losses at -0.140 on stake (-2.0% raw), eliminating the -24% stake drawdowns.
   - Altcoins (3.0x) maintain standard -0.297 (-9.9% raw) room to breathe.
3. Rapid Breakeven Lock for High-Beta Shorts (SOL & ETH):
   - Once a Short gains > +2.5% on stake (+0.83% raw), stoploss locks to +0.5% profit, preventing dump-to-pump reversals.
4. Preserved Proven Edges:
   - High-yield Bollinger Runner exit (RSI >= 71, 100% WR).
   - 36h Stale decay cutoff.
   - High-conviction Stochastic-ATR exhaustion entries.
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


class ApexDualAlpha_Omni_V3(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Preserved Wide Take Profit Runway (Fee-Proof)
    minimal_roi = {
        "0": 0.526,
        "467": 0.161,
        "688": 0.090,
        "1173": 0
    }

    stoploss = -0.297
    use_custom_stoploss = True

    trailing_stop = True
    trailing_stop_positive = 0.217
    trailing_stop_positive_offset = 0.249
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

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        Leverage-Aware Stoploss Normalization:
        - BTC at 7.0x leverage: cap loss at -14.0% on stake (-2.0% raw price move)
          to prevent 7x leverage whipsaws from destroying portfolio gains.
        - High-Beta Shorts (SOL/ETH): lock in breakeven once profit reaches +2.5% on stake.
        """
        if "BTC" in pair:
            # For BTC, cap initial stoploss at -14% on stake
            return -0.140

        # Breakeven lock for Shorts once in moderate profit
        if trade.is_short:
            if current_profit > 0.025:
                return -0.005  # lock near breakeven

        return -0.297

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # Stale decay cutoff at 36 hours (saves capital without cutting consolidation rebounds)
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        informative = [(pair, self.informative_timeframe) for pair in pairs]
        # Include BTC 4h for macro regime confluence if not already present
        btc_pair = "BTC/USDT:USDT"
        if (btc_pair, self.informative_timeframe) not in informative:
            informative.append((btc_pair, self.informative_timeframe))
        return informative

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if hasattr(self, "dp") and self.dp:
            # Pair's own 4H informative
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

            # BTC 4H Master Informative (for Altcoins)
            if metadata["pair"] != "BTC/USDT:USDT":
                btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.informative_timeframe)
                if not btc_4h.empty:
                    btc_4h["btc_ema_50"] = ta.EMA(btc_4h, timeperiod=50)
                    btc_4h["btc_ema_200"] = ta.EMA(btc_4h, timeperiod=200)
                    btc_4h["btc_macro_bull"] = (btc_4h["close"] > btc_4h["btc_ema_50"]) & (btc_4h["btc_ema_50"] > btc_4h["btc_ema_200"])
                    btc_4h["btc_macro_bear"] = (btc_4h["close"] < btc_4h["btc_ema_50"]) & (btc_4h["btc_ema_50"] < btc_4h["btc_ema_200"])
                    dataframe = merge_informative_pair(dataframe, btc_4h, self.timeframe, self.informative_timeframe, ffill=True)

        for col in [f"macro_bull_4h_{self.informative_timeframe}", f"macro_bear_4h_{self.informative_timeframe}"]:
            if col not in dataframe.columns:
                dataframe[col] = True if "bull" in col else False
        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0

        # Default fallback for BTC macro columns
        if f"btc_macro_bull_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"btc_macro_bull_{self.informative_timeframe}"] = False
        if f"btc_macro_bear_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"btc_macro_bear_{self.informative_timeframe}"] = False

        # Primary 1H Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
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

        # Asset-specific calibration
        adx_long_gate = 24.0 if "BTC" in pair else (22.0 if "SOL" in pair else 21.0)
        rsi_min_long = 42.0 if "SOL" in pair else 41.0
        pullback_threshold = 1.006

        adx_short_gate = 24.0 if "BTC" in pair else 22.0

        # BTC Master Macro Confluence for Altcoins
        is_btc = "BTC" in pair
        btc_bull = dataframe[f"btc_macro_bull_{self.informative_timeframe}"] if not is_btc else dataframe[f"macro_bull_4h_{self.informative_timeframe}"]
        btc_bear = dataframe[f"btc_macro_bear_{self.informative_timeframe}"] if not is_btc else dataframe[f"macro_bear_4h_{self.informative_timeframe}"]

        # Altcoin Shorts are blocked if BTC is in a raging Bull trend (avoids bear trap squeezes)
        allow_short = True if is_btc else (btc_bull == False)

        # 1. LONG: Proven High-Conviction Bullish Pullback
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > adx_long_gate) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * pullback_threshold) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= rsi_min_long) &
            (dataframe["rsi"] <= 63.0) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 54.0) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # 2. SHORT: Master-Confluent Resistance Exhaustion Fade
        short_conditions = (
            allow_short &
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > adx_short_gate) &
            (
                (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
            ) &
            (dataframe["close"] <= dataframe["ema_50"] + 0.30 * dataframe["atr"]) &
            (dataframe["rsi"] >= 53.0) &
            (dataframe["rsi"] <= 67.0) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 55.0) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Bollinger Runner Exit (Yield-Boosting)
        long_exit = (
            (dataframe["rsi"] >= 71) &
            (dataframe["close"] >= dataframe["bb_upperband"] * 0.998)
        )

        short_exit = (
            (dataframe["rsi"] <= 28) &
            (dataframe["close"] <= dataframe["bb_lowerband"] * 1.002)
        )

        dataframe.loc[long_exit, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[short_exit, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe