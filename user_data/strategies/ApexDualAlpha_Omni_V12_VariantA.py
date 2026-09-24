"""
ApexDualAlpha_Omni_V12_VariantA - Calibrated Long + Short for LINK
Curated for Bybit Linear Futures 8-Pair Universe: BTC, ETH, SOL, ADA, DOGE, LINK, PAXG, HYPE.

LINK Specific Tuning:
- Long LINK: Tightened with ADX >= 20.0, RSI <= 58.0, BTC 4H > EMA50, BTC RSI > 48, Rel Strength >= 0.
- Short LINK: High-conviction Resistance Exhaustion Fade Short.
- Other Pairs (ADA, BTC, ETH, SOL, DOGE, PAXG, HYPE): 100% Identical.
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


class ApexDualAlpha_Omni_V12_VariantA(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # High-Yield Precision Profit Ladder
    minimal_roi = {
        "0": 0.526,
        "467": 0.161,
        "688": 0.090,
        "1173": 0.011
    }

    stoploss = -0.297

    trailing_stop = True
    trailing_stop_positive = 0.217
    trailing_stop_positive_offset = 0.249
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
        if "BTC" in pair:
            return 7.0
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # 1. Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # 2. Surgical Volatility Scalp for PAXG (Gold bullion low-volatility cycle)
        if "PAXG" in pair and trade_duration >= 4.0 and current_profit >= 0.0065:
            return "paxg_volatility_tp"

        # 3. Structural Profit Banking for ETH (protecting against macro ETH/BTC bleed)
        if "ETH" in pair and trade_duration >= 16.0 and current_profit >= 0.0080:
            return "eth_profit_bank_tp"

        # 4. Standard Stale decay cutoff at 36 hours for all pairs
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        # 5. Micro-profit closure at 48 hours
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        informative = [(pair, self.informative_timeframe) for pair in pairs]
        
        # Always include BTC 4h and 1h for macro informative coupling
        btc_pair = "BTC/USDT:USDT"
        if (btc_pair, "4h") not in informative:
            informative.append((btc_pair, "4h"))
        if (btc_pair, "1h") not in informative:
            informative.append((btc_pair, "1h"))
            
        return informative

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["macro_bull_4h"] = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"])
        dataframe["macro_bear_4h"] = (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair", "")

        # 1. Informative 4H for current pair
        if hasattr(self, "dp") and self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=pair, timeframe=self.informative_timeframe)
            if not inf_4h.empty:
                inf_4h = self.informative_4h_indicators(inf_4h, metadata)
                dataframe = merge_informative_pair(dataframe, inf_4h, self.timeframe, self.informative_timeframe, ffill=True)

            # 2. Informative BTC 4H and 1H for macro coupling
            btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="4h")
            if not btc_4h.empty:
                btc_4h["btc_ema_50"] = ta.EMA(btc_4h, timeperiod=50)
                btc_4h["btc_ema_200"] = ta.EMA(btc_4h, timeperiod=200)
                btc_4h["btc_rsi"] = ta.RSI(btc_4h, timeperiod=14)
                dataframe = merge_informative_pair(dataframe, btc_4h, self.timeframe, "4h", ffill=True, append_timeframe=False, suffix="btc_4h")

            btc_1h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="1h")
            if not btc_1h.empty:
                btc_1h["btc_ret_24h"] = btc_1h["close"].pct_change(24)
                dataframe = merge_informative_pair(dataframe, btc_1h, self.timeframe, "1h", ffill=True, append_timeframe=False, suffix="btc_1h")

        # Fallbacks for columns if not populated
        for col in [f"macro_bull_4h_{self.informative_timeframe}", f"macro_bear_4h_{self.informative_timeframe}"]:
            if col not in dataframe.columns:
                dataframe[col] = True if "bull" in col else False
        if f"adx_{self.informative_timeframe}" not in dataframe.columns:
            dataframe[f"adx_{self.informative_timeframe}"] = 25.0

        if "close_btc_4h" not in dataframe.columns:
            dataframe["close_btc_4h"] = dataframe["close"]
            dataframe["btc_ema_50_btc_4h"] = dataframe["close"]
            dataframe["btc_rsi_btc_4h"] = 50.0
        if "btc_ret_24h_btc_1h" not in dataframe.columns:
            dataframe["btc_ret_24h_btc_1h"] = 0.0

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

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
        dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)

        # 24H Return & Relative Strength
        dataframe["ret_24h"] = dataframe["close"].pct_change(24)
        dataframe["rel_strength_24h"] = dataframe["ret_24h"] - dataframe["btc_ret_24h_btc_1h"]

        # 24H Support Low (shifted by 1 to prevent lookahead)
        dataframe["low_24h"] = dataframe["low"].shift(1).rolling(24).min()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        pair = metadata.get("pair", "")

        # Long Configuration
        if "BTC" in pair:
            adx_long_gate = 24.0
        elif "ETH" in pair or "SOL" in pair:
            adx_long_gate = 22.5
        elif "LINK" in pair:
            adx_long_gate = 20.0
        else:
            adx_long_gate = 21.0

        rsi_min_long = 44.0 if "SOL" in pair else 43.0
        rsi_max_long = 58.0 if "LINK" in pair else 63.0
        pullback_threshold = 1.006

        # Base Long Conditions
        long_base = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] >= adx_long_gate) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * pullback_threshold) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= rsi_min_long) &
            (dataframe["rsi"] <= rsi_max_long) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 54.0) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.68)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # LINK Specialized Macro Gating (Varian A: ADX >= 20.0, RSI <= 58.0, BTC 4H > EMA50, BTC RSI > 48, Rel Strength >= 0)
        if "LINK" in pair:
            long_conditions = long_base & (
                (dataframe["close_btc_4h"] > dataframe["btc_ema_50_btc_4h"]) &
                (dataframe["btc_rsi_btc_4h"] > 48.0) &
                (dataframe["rel_strength_24h"] >= 0.0)
            )
        else:
            long_conditions = long_base

        # Short Configuration
        if "BTC" not in pair:
            if "SOL" in pair:
                adx_short_gate = 25.0
                rsi_min_short = 56.0
                require_below_ema200 = True
            else:
                adx_short_gate = 22.0
                rsi_min_short = 53.0
                require_below_ema200 = False

            # Base Short Resistance Exhaustion Fade
            short_fade_base = (
                (dataframe[f"adx_{self.informative_timeframe}"] > adx_short_gate) &
                (
                    (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                    (dataframe["high"] >= dataframe["bb_middleband"] * 0.998)
                ) &
                (dataframe["close"] <= dataframe["ema_50"] + 0.30 * dataframe["atr"]) &
                (dataframe["rsi"] >= rsi_min_short) &
                (dataframe["rsi"] <= 67.0) &
                (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
                (dataframe["slowk"] > 55.0) &
                (
                    (dataframe["is_red"] == True) |
                    (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
                ) &
                (dataframe["volume"] > dataframe["volume_mean_20"] * 0.72)
            )

            # For LINK: Allow shorting either on LINK 4H macro bear OR when BTC 4H < EMA 50 & LINK < EMA 50
            if "LINK" in pair:
                link_short_regime = (
                    (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) |
                    (
                        (dataframe["close_btc_4h"] < dataframe["btc_ema_50_btc_4h"]) &
                        (dataframe["close"] < dataframe["ema_50"])
                    )
                )
                short_conditions = short_fade_base & link_short_regime
            elif require_below_ema200:
                short_conditions = short_fade_base & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) & (dataframe["close"] < dataframe["ema_200"])
            else:
                short_conditions = short_fade_base & (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True)

            dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Bollinger Runner Exit (High Yield)
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
