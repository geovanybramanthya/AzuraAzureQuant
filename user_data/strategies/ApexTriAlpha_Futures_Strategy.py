"""
ApexTriAlpha_Futures_Strategy - Institutional Tri-Engine Bidirectional Alpha Engine
Target: Scaled Execution Frequency, Robust Winrate (>=60%), Capital Compounding

Architectural Framework:
1. Multi-Archetype Confluence:
   - Engine 1 (Long Pullback Alpha): High-conviction pullback entries into EMA 21 / BB Mid during 4H bull regimes.
   - Engine 2 (Volatility Squeeze Expansion): Bollinger-Keltner compression release with volume burst from BB Mid.
   - Engine 3 (Short ATR Resistance Fade): Precision resistance exhaustion fades during 4H macro bear regimes.
2. Asymmetric & Capital Preservation Exits:
   - Fast explosive dump TP for Shorts (> +4.5% profit).
   - Time decay stale loss unfreezing at 36h (< -1.0% profit).
   - Stagnation cutoff at 48h (< 0.5% profit).
   - Dynamic Upper BB Take Profit for extended Longs.
3. Risk Management & Execution:
   - Dynamic Isolated Leverage: 7.0x for BTC/USDT (satisfies $60 min notional on $25 accounts), 3.0x for prime altcoins.
   - Calibrated 3-slot maximum allocation for optimal leverage and drawdown resilience.
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


class ApexTriAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Parameters - Engine 1: Pullback Alpha
    buy_rsi_min = IntParameter(38, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 65, default=52, space="buy")

    # Hyperoptable Parameters - Engine 3: Short Exhaustion Fade
    short_rsi_min = IntParameter(50, 64, default=53, space="sell")
    short_rsi_max = IntParameter(60, 75, default=67, space="sell")
    short_adx_4h = IntParameter(18, 30, default=22, space="sell")
    short_stoch_min = IntParameter(48, 75, default=55, space="sell")

    minimal_roi = {
        "0": 0.526,
        "467": 0.161,
        "688": 0.09,
        "1173": 0
    }

    stoploss = -0.297

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
        """
        Dynamic Capital-Adaptive Isolated Leverage:
        - BTC/USDT scales to 7.0x leverage so micro-accounts ($25 USDT) satisfy min notional ($60).
        - All other altcoins maintain institutional 3.0x leverage.
        """
        if "BTC" in pair:
            return 7.0
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600

        # 1. Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        # 2. Time decay stale loss (preserves capital after 36h if stalled negative)
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"

        # 4. Long-tail stagnation cutoff at 48h
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"

        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if hasattr(self, "dp") and self.dp else []
        informative_pairs = [(pair, self.informative_timeframe) for pair in pairs]
        if "BTC/USDT:USDT" not in pairs:
            informative_pairs.append(("BTC/USDT:USDT", self.informative_timeframe))
        return list(set(informative_pairs))

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
        # 1. Informative 4H Pair Data
        if hasattr(self, "dp") and self.dp:
            informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not informative_4h.empty and len(informative_4h) > 50:
                informative_4h = self.informative_4h_indicators(informative_4h, metadata)
                dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)
            else:
                dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
                dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
                dataframe[f"adx_{self.informative_timeframe}"] = 25
                dataframe[f"rsi_{self.informative_timeframe}"] = 50

            # 2. Informative 4H BTC Macro Leader for Market-Wide Alignment
            if metadata["pair"] != "BTC/USDT:USDT":
                btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.informative_timeframe)
                if not btc_4h.empty and len(btc_4h) > 50:
                    btc_4h = self.informative_4h_indicators(btc_4h, {"pair": "BTC/USDT:USDT"})
                    btc_renamed = btc_4h[["date", "macro_bull_4h", "macro_bear_4h", "adx", "rsi"]].copy()
                    btc_renamed = btc_renamed.rename(columns={
                        "macro_bull_4h": "btc_macro_bull",
                        "macro_bear_4h": "btc_macro_bear",
                        "adx": "btc_adx",
                        "rsi": "btc_rsi"
                    })
                    dataframe = merge_informative_pair(dataframe, btc_renamed, self.timeframe, self.informative_timeframe, ffill=True)
                else:
                    dataframe[f"btc_macro_bull_{self.informative_timeframe}"] = True
                    dataframe[f"btc_macro_bear_{self.informative_timeframe}"] = False
            else:
                dataframe[f"btc_macro_bull_{self.informative_timeframe}"] = dataframe[f"macro_bull_4h_{self.informative_timeframe}"]
                dataframe[f"btc_macro_bear_{self.informative_timeframe}"] = dataframe[f"macro_bear_4h_{self.informative_timeframe}"]
        else:
            dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
            dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
            dataframe[f"adx_{self.informative_timeframe}"] = 25
            dataframe[f"rsi_{self.informative_timeframe}"] = 50
            dataframe[f"btc_macro_bull_{self.informative_timeframe}"] = True
            dataframe[f"btc_macro_bear_{self.informative_timeframe}"] = False

        # 3. 1H Execution Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands (20, 2.0 std)
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe["bb_middleband"]

        # Keltner Channel (20, 1.5 ATR)
        keltner = qtpylib.keltner_channel(dataframe, window=20, atrs=1.5)
        dataframe["kc_upperband"] = keltner["upper"]
        dataframe["kc_middleband"] = keltner["mid"]
        dataframe["kc_lowerband"] = keltner["lower"]

        # Engine 2: Volatility Squeeze Compression & Release
        bw_compressed = dataframe["bb_width"] < dataframe["bb_width"].rolling(50).quantile(0.25)
        kc_squeeze = (dataframe["bb_lowerband"] > dataframe["kc_lowerband"]) & (dataframe["bb_upperband"] < dataframe["kc_upperband"])
        dataframe["squeeze_on"] = bw_compressed | kc_squeeze
        dataframe["was_in_squeeze_3"] = dataframe["squeeze_on"].rolling(window=3).max() > 0

        # Stochastic Oscillator (14, 3, 3)
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Candle Microstructure
        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])


        # Volume Flow Metrics
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Macro Trend references
        macro_bull_col = f"macro_bull_4h_{self.informative_timeframe}"
        macro_bear_col = f"macro_bear_4h_{self.informative_timeframe}"
        adx_4h_col = f"adx_{self.informative_timeframe}"
        btc_macro_bull_col = f"btc_macro_bull_{self.informative_timeframe}"
        btc_macro_bear_col = f"btc_macro_bear_{self.informative_timeframe}"

        macro_bull = dataframe[macro_bull_col] if macro_bull_col in dataframe.columns else True
        macro_bear = dataframe[macro_bear_col] if macro_bear_col in dataframe.columns else False
        adx_4h = dataframe[adx_4h_col] if adx_4h_col in dataframe.columns else 25
        btc_bull = dataframe[btc_macro_bull_col] if btc_macro_bull_col in dataframe.columns else True
        btc_bear = dataframe[btc_macro_bear_col] if btc_macro_bear_col in dataframe.columns else False

        # =========================================================================
        # ENGINE 1 (LONG): Proven High-Conviction Bullish Pullback
        # Features: Single-candle crisp stochastic crossover + 4H Bullish Alignment
        # =========================================================================
        long_pullback_conditions = (
            (macro_bull == True) &
            (adx_4h > self.buy_adx_4h.value) &
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

        # =========================================================================
        # ENGINE 2 (LONG): Volatility Squeeze Expansion (Bollinger-Keltner Breakout)
        # Features: Compression release from middle band + institutional volume impulse
        # =========================================================================
        long_squeeze_conditions = (
            (dataframe["was_in_squeeze_3"] == True) &
            (~dataframe["squeeze_on"]) &
            (macro_bull == True) &
            (dataframe["close"] > dataframe["bb_middleband"]) &
            (dataframe["close"] < dataframe["bb_upperband"] * 0.998) &
            (dataframe["close"] > dataframe["ema_9"]) &
            (dataframe["rsi"] >= 50) &
            (dataframe["rsi"] <= 68) &
            (dataframe["slowk"] > dataframe["slowd"]) &
            (dataframe["is_green"] == True) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 1.0)
        )

        # =========================================================================
        # ENGINE 3 (SHORT): Calibrated Gentle Sniper Shorts
        # Features: Single-candle crisp stochastic crossover + ATR resistance rejection
        # =========================================================================
        short_fade_conditions = (
            (macro_bear == True) &
            (adx_4h > self.short_adx_4h.value) &
            (
                (dataframe["high"] >= dataframe["ema_21"] * 0.996) |
                (dataframe["high"] >= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] <= dataframe["ema_50"] + 0.30 * dataframe["atr"]) &
            (dataframe["rsi"] >= self.short_rsi_min.value) &
            (dataframe["rsi"] <= self.short_rsi_max.value) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > self.short_stoch_min.value) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        # Prioritize entries and tag them cleanly
        dataframe.loc[long_pullback_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[long_squeeze_conditions & (dataframe["enter_long"] == 0), ["enter_long", "enter_tag"]] = (1, "long_squeeze_expansion")
        dataframe.loc[short_fade_conditions & (dataframe["enter_long"] == 0), ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Take Profit
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 74)
        )

        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 28)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
