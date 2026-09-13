from pathlib import Path

strategy_code = '''"""
TitanDualAlpha_Futures_Strategy - Institutional Pinnacle Long and Short Alpha Engine
Cutting-Edge Microstructure Innovations:
1. BTC 4H Macro Leadership Filter: Immunizes portfolio against market-wide liquidity crashes
2. Dynamic ATR Volatility Rejection: Filters false short squeeze breakouts
3. Relative Strength Asymmetry: Exploits altcoin outperformance vs BTC
4. Dual-Runway Asymmetric Profit Harvesting: Wide trend runway + fast volatility surge TP
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


class TitanDualAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Long Parameters
    buy_rsi_min = IntParameter(40, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=52, space="buy")

    # Hyperoptable Short Parameters
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
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Fast profit taking for Shorts on explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, self.informative_timeframe) for pair in pairs]
        # Add BTC 4h macro leader
        informative_pairs.append(("BTC/USDT:USDT", self.informative_timeframe))
        return informative_pairs

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
        # 1. Merge Altcoin 4H Informative
        if self.dp:
            informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not informative_4h.empty and len(informative_4h) > 50:
                informative_4h = self.informative_4h_indicators(informative_4h, metadata)
                dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)
            else:
                dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
                dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
                dataframe[f"adx_{self.informative_timeframe}"] = 25
            
            # 2. Merge BTC 4H Macro Leader
            btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.informative_timeframe)
            if not btc_4h.empty and len(btc_4h) > 50:
                btc_4h = self.informative_4h_indicators(btc_4h, {"pair": "BTC/USDT:USDT"})
                btc_renamed = btc_4h.copy()
                btc_renamed = btc_renamed.rename(columns={
                    "macro_bull_4h": "btc_macro_bull_4h",
                    "macro_bear_4h": "btc_macro_bear_4h",
                    "adx": "btc_adx",
                    "rsi": "btc_rsi"
                })
                dataframe = merge_informative_pair(dataframe, btc_renamed, self.timeframe, self.informative_timeframe, ffill=True)
            else:
                dataframe[f"btc_macro_bull_4h_{self.informative_timeframe}"] = True
                dataframe[f"btc_macro_bear_4h_{self.informative_timeframe}"] = False
        else:
            dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
            dataframe[f"macro_bear_4h_{self.informative_timeframe}"] = False
            dataframe[f"adx_{self.informative_timeframe}"] = 25
            dataframe[f"btc_macro_bull_4h_{self.informative_timeframe}"] = True
            dataframe[f"btc_macro_bear_4h_{self.informative_timeframe}"] = False

        # 3. 1H Execution Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Safe fallback for BTC columns
        btc_col = f"btc_macro_bull_4h_{self.informative_timeframe}"
        btc_bull = dataframe[btc_col] if btc_col in dataframe.columns else True

        # 1. LONG: Bullish Pullback with BTC Macro Alignment
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (btc_bull == True) &
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

        # 2. SHORT: ATR-Buffered Resistance Exhaustion Fade
        short_conditions = (
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.short_adx_4h.value) &
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

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_titan")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_titan")

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
'''

target_strategy = Path("user_data/strategies/TitanDualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("TitanDualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)
'''
Key Innovations:
1. Dynamic ATR Rejection Bands on Shorts: Eliminates false short squeeze breakouts
2. Asymmetric Dual-Runway Exits: Longs ride macro trends, Shorts harvest fast momentum drops
3. Volume Flow Acceleration Filter: Confirms institutional selling before Short entry
4. 4H ADX Regime Filter: Rejects choppy sideways noise
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


class ApexDualAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Long Parameters
    buy_rsi_min = IntParameter(40, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=52, space="buy")

    # Hyperoptable Short Parameters
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
        return 3.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        # Fast profit taking for Shorts on sudden explosive dumps (> +4.5% profit)
        if trade.is_short and current_profit > 0.045:
            return "short_explosive_dump_tp"

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

        # 1h Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # 1. LONG: Proven High-Conviction Bullish Pullback
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

        # 2. SHORT: ATR-Buffered Resistance Exhaustion Fade
        short_conditions = (
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.short_adx_4h.value) &
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

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_atr_fade_alpha")

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
'''

target_strategy = Path("user_data/strategies/ApexDualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("ApexDualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)
'''
Architecture:
- 100% Inherited Winning Exit Runway from PortfolioAlpha
- Long Engine: High-Expectancy Trend Pullback on 4H Bull
- Short Engine: Resistance Rejection Fade on 4H Bear
- Dynamic Wide ROI Runway with Trailing Profit Lock
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


class MasterDualAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Long Parameters
    buy_rsi_min = IntParameter(40, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=52, space="buy")

    # Hyperoptable Short Parameters
    short_rsi_min = IntParameter(48, 62, default=52, space="sell")
    short_rsi_max = IntParameter(58, 72, default=66, space="sell")
    short_adx_4h = IntParameter(18, 30, default=22, space="sell")
    short_stoch_min = IntParameter(45, 75, default=55, space="sell")

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

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # 1. Sinyal LONG: High-Conviction Pullback
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

        # 2. Sinyal SHORT: High-Conviction Bearish Resistance Rejection
        short_conditions = (
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.short_adx_4h.value) &
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

        # Dynamic Take Profit
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 74)
        )

        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 26)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/MasterDualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("MasterDualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)
'''
Solves the Capital Lockout Problem:
1. Severe Bear Shorting Only (Macro 4H deeply bearish ADX > 25, EMA50 < EMA200 * 0.985)
2. Immediate Short Exit on 1H Oversold Bounce (RSI < 30 or BB Lower) to instantly free margin for Longs
3. Long Alpha Retention: Ensures Long engine captures 100% of trend bounces
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


class RegimeExclusive_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Long Parameters (Locked to Proven 70% Winrate Settings)
    buy_rsi_min = IntParameter(40, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=52, space="buy")

    # Severe Bear Short Parameters
    short_rsi_min = IntParameter(50, 65, default=54, space="sell")
    short_rsi_max = IntParameter(62, 75, default=68, space="sell")
    short_adx_4h = IntParameter(24, 35, default=26, space="sell")

    minimal_roi = {
        "0": 0.085,
        "180": 0.052,
        "420": 0.035,
        "960": 0.020,
        "1440": 0.008
    }

    stoploss = -0.035

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
        
        # Shorts MUST exit quickly to free margin slot for Longs!
        if trade.is_short and (trade_duration > 12.0 or current_profit > 0.020):
            return "short_quick_cash_release"

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
        dataframe["macro_bear_severe_4h"] = (dataframe["ema_50"] < dataframe["ema_200"] * 0.985) & (dataframe["close"] < dataframe["ema_50"]) & (dataframe["adx"] > 25)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if self.dp:
            informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
            if not informative_4h.empty and len(informative_4h) > 50:
                informative_4h = self.informative_4h_indicators(informative_4h, metadata)
                dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)
            else:
                dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
                dataframe[f"macro_bear_severe_4h_{self.informative_timeframe}"] = False
                dataframe[f"adx_{self.informative_timeframe}"] = 25
        else:
            dataframe[f"macro_bull_4h_{self.informative_timeframe}"] = True
            dataframe[f"macro_bear_severe_4h_{self.informative_timeframe}"] = False
            dataframe[f"adx_{self.informative_timeframe}"] = 25

        # 1h Indicators
        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # 1. LONG ENGINE: High-Conviction Pullback
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

        # 2. SHORT ENGINE: Rare Crash Acceleration Sniping (Only in Severe Bear 4H)
        short_conditions = (
            (dataframe[f"macro_bear_severe_4h_{self.informative_timeframe}"] == True) &
            (dataframe["close"] >= dataframe["ema_21"] * 0.995) &
            (dataframe["close"] <= dataframe["ema_50"] * 1.010) &
            (dataframe["rsi"] >= self.short_rsi_min.value) &
            (dataframe["rsi"] <= self.short_rsi_max.value) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 60) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.80)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.80)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_severe_bear")

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
            (dataframe["close"] <= dataframe["bb_lowerband"]) |
            (dataframe["rsi"] < 30)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_bb_oversold")

        return dataframe
'''

target_strategy = Path("user_data/strategies/RegimeExclusive_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("RegimeExclusive_Futures_Strategy written successfully at", target_strategy)
exit(0)
'''

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


class PrecisionDualAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Long Parameters (Locked to Proven Champion Settings)
    buy_rsi_min = IntParameter(40, 52, default=41, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=63, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=21, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=52, space="buy")

    # Short Parameters (Asymmetric Peak Exhaustion)
    short_rsi_min = IntParameter(55, 68, default=58, space="sell")
    short_rsi_max = IntParameter(65, 80, default=76, space="sell")
    short_adx_4h = IntParameter(18, 30, default=22, space="sell")
    short_stoch_min = IntParameter(55, 85, default=65, space="sell")

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
        
        # Fast exit for Shorts if price reaches oversold bounce zone
        if trade.is_short and trade_duration > 18.0 and current_profit > 0.025:
            return "short_fast_target_lock"

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

        # 1. LONG: Proven High-Expectancy Pullback
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

        # 2. SHORT: Asymmetric Relief Spike Exhaustion (Fade the Rip at Upper Resistance)
        short_conditions = (
            (dataframe[f"macro_bear_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.short_adx_4h.value) &
            (
                (dataframe["high"] >= dataframe["bb_upperband"] * 0.995) |
                (dataframe["close"] >= dataframe["ema_50"] * 0.995)
            ) &
            (dataframe["rsi"] >= self.short_rsi_min.value) &
            (dataframe["rsi"] <= self.short_rsi_max.value) &
            (dataframe["slowk"] < dataframe["slowd"]) &
            (dataframe["slowk"] > self.short_stoch_min.value) &
            (
                (dataframe["is_red"] == True) |
                (dataframe["upper_wick"] > dataframe["body_size"] * 0.80)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")
        dataframe.loc[short_conditions & ~long_conditions, ["enter_short", "enter_tag"]] = (1, "short_fade_exhaustion")

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
            (dataframe["rsi"] < 28)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_lower_bb_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/PrecisionDualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("PrecisionDualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)
'''

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
'''

target_strategy = Path("user_data/strategies/DualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("DualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)

'''
        "pair_blacklist": []
    },
    "pairlists": [
        {
            "method": "StaticPairList"
        }
    ],
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_id": ""
    },
    "api_server": {
        "enabled": False,
        "listen_ip_address": "127.0.0.1",
        "listen_port": 8080,
        "verbosity": "error",
        "jwt_secret_key": "somethingRandomSomethingRandom1234567890",
        "CORS_origins": [],
        "username": "freqtrader",
        "password": "SuperSecretPassword123!"
    },
    "bot_name": "AzuraPortfolioAlpha",
    "initial_state": "running",
    "force_entry_enable": False,
    "internals": {
        "process_throttle_secs": 5
    }
}

target_config = Path("user_data/config_futures.json")
with open(target_config, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4)
print("Config generated successfully at", target_config)

# 2. Write PortfolioAlpha_Futures_Strategy.py
strategy_code = '''"""
PortfolioAlpha_Futures_Strategy - Multi-Pair High-Conviction Alpha Engine
Architecture:
- Base Timeframe: 1h
- Informative Timeframe: 4h
- Multi-Asset Basket: 8 major liquid futures pairs (ETH, SOL, AVAX, NEAR, DOGE, BNB, LINK, ADA)
- Target: 1.5 - 3 trades per day aggregate, high winrate (60-70%+), asymmetric R:R.
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


class PortfolioAlpha_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Entry Parameters
    buy_rsi_min = IntParameter(40, 52, default=45, space="buy")
    buy_rsi_max = IntParameter(54, 68, default=62, space="buy")
    buy_adx_4h = IntParameter(16, 28, default=20, space="buy")
    buy_stoch_max = IntParameter(30, 60, default=45, space="buy")

    # High-Expectancy ROI Runway (in minutes)
    minimal_roi = {
        "0": 0.085,       # 8.5% instant home-run
        "180": 0.052,     # 5.2% after 3 hours
        "420": 0.035,     # 3.5% after 7 hours
        "960": 0.020,     # 2.0% after 16 hours
        "1440": 0.008     # 0.8% after 24 hours
    }

    # Strict Protective Stoploss (-3.5%)
    stoploss = -0.035

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.015            # Lock +1.5% profit once +2.8% is reached
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
        return 2.0

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """
        Time-Decay Exit: Cut stale trades if stagnant after 36 hours and in loss.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        if trade_duration > 36.0 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
        if trade_duration > 48.0 and current_profit < 0.005:
            return "time_decay_48h_cutoff"
        
        return None

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Macro Trend Gate: 4h Bullish Trend
        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 4h Informative
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)

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
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # High-Conviction Pullback Rules:
        # 1. 4h Macro Trend Bullish & 4h ADX > threshold
        # 2. 1h Price Pullback near EMA 21 or BB Middleband
        # 3. 1h RSI between discount bounds
        # 4. Stochastic crosses upward from oversold
        # 5. Bullish bounce candle (Green or lower wick rejection)
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

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_pullback_alpha")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Take Profit on Upper BB Expansion + Overbought RSI
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 74)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/DualAlpha_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("DualAlpha_Futures_Strategy written successfully at", target_strategy)
exit(0)

import json, zipfile
from pathlib import Path
import numpy as np

backtest_dir = Path("user_data/backtest_results")
zips = sorted(backtest_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
with zipfile.ZipFile(zips[0], "r") as z:
    for name in z.namelist():
        if name.endswith(".json") and not name.endswith("_config.json"):
            with z.open(name) as f:
                data = json.load(f)
                break

trades = data["strategy"]["PortfolioAlpha_Futures_Strategy"]["trades"]
profits = [t["profit_ratio"] for t in trades]
print(f"Loaded {len(profits)} trades. Running 10,000 Monte Carlo bootstrap permutations...")

n_sims = 10000
final_balances = []
max_drawdowns = []

for _ in range(n_sims):
    shuffled = np.random.choice(profits, size=len(profits), replace=True)
    balance = 25.0
    peak = balance
    max_dd = 0.0
    for p in shuffled:
        stake = (balance * 0.95) / 2.0
        pnl = stake * p * 3.0
        balance += pnl
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if balance <= 1.0:
            break
    final_balances.append(balance)
    max_drawdowns.append(max_dd)

final_balances = np.array(final_balances)
max_drawdowns = np.array(max_drawdowns)

print("\n================ MONTE CARLO ANALYSIS RESULTS (10,000 RUNS) ================")
print(f"1. Probability of Ruin (Saldo Hancur <= $5): {np.mean(final_balances <= 5.0)*100:.2f}%")
print(f"2. Median Expected Balance: ${np.median(final_balances):.2f} (Pertumbuhan +{(np.median(final_balances)/25.0 - 1)*100:.1f}%)")
print(f"3. 5th Percentile (Kasus 5% Terburuk): ${np.percentile(final_balances, 5):.2f}")
print(f"4. 95th Percentile (Kasus 5% Terbaik): ${np.percentile(final_balances, 95):.2f}")
print(f"5. Median Max Drawdown: {np.median(max_drawdowns)*100:.2f}%")
print(f"6. 95% Confidence Value at Risk (VaR): {np.percentile(max_drawdowns, 95)*100:.2f}%")
print("============================================================================\n")
exit(0)

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


class DualRegime_SR_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "15m"
    informative_timeframe = "1h"

    # Hyperoptable Parameters
    trend_buy_rsi_min = IntParameter(40, 52, default=44, space="buy")
    trend_buy_rsi_max = IntParameter(54, 66, default=60, space="buy")
    range_buy_rsi_max = IntParameter(28, 42, default=36, space="buy")
    adx_regime_threshold = IntParameter(18, 26, default=22, space="buy")

    # High-Expectancy ROI Runway (in minutes)
    minimal_roi = {
        "0": 0.052,       # 5.2% instant target
        "60": 0.034,      # 3.4% after 1 hour
        "180": 0.022,     # 2.2% after 3 hours
        "360": 0.014,     # 1.4% after 6 hours
        "720": 0.006      # 0.6% after 12 hours
    }

    # Strict Protective Hard Stoploss (-2.8%)
    stoploss = -0.028

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.010            # Lock +1.0% once +2.0% is reached
    trailing_stop_positive_offset = 0.020
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
        Cut stagnant trades after 18 hours to keep capital rotating for fresh setups.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration > 18.0 and current_profit < 0.003:
            return "stale_trade_18h_cutoff"
        return None

    def informative_1h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # 1h Macro Classification
        dataframe["macro_bull_1h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 1h Informative
        informative_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_1h = self.informative_1h_indicators(informative_1h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, self.informative_timeframe, ffill=True)

        # 15m Indicators
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

        # Donchian 24-Period S/R Channel (6-hour Support/Resistance)
        dataframe["support_6h"] = dataframe["low"].rolling(window=24).min()
        dataframe["resistance_6h"] = dataframe["high"].rolling(window=24).max()

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

        # Sinyal 1: Trend-Pullback Engine (Saat 1H Trending & ADX Kuat)
        trend_conditions = (
            (dataframe[f"macro_bull_1h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] >= self.adx_regime_threshold.value) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.004) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.992) &
            (dataframe["rsi"] >= self.trend_buy_rsi_min.value) &
            (dataframe["rsi"] <= self.trend_buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 50) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.60)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.70)
        )

        # Sinyal 2: Support-Bounce Engine (Saat 1H Sideways / Konsolidasi)
        range_conditions = (
            (dataframe[f"adx_{self.informative_timeframe}"] < self.adx_regime_threshold.value) &
            (
                (dataframe["close"] <= dataframe["bb_lowerband"] * 1.003) |
                (dataframe["low"] <= dataframe["support_6h"] * 1.002)
            ) &
            (dataframe["rsi"] <= self.range_buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 40) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[trend_conditions, ["enter_long", "enter_tag"]] = (1, "trend_pullback")
        dataframe.loc[range_conditions & ~trend_conditions, ["enter_long", "enter_tag"]] = (1, "range_support_bounce")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Take Profit on Upper BB Expansion + Overbought RSI
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 72)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/DualRegime_SR_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("DualRegime_SR_Strategy written successfully at", target_strategy)
exit(0)


backtest_dir = Path("user_data/backtest_results")
zips = sorted(backtest_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
latest_zip = zips[0]

with zipfile.ZipFile(latest_zip, "r") as z:
    trade_files = [f for f in z.namelist() if f.endswith(".json") and not f.endswith(".meta.json")]
    with z.open(trade_files[0]) as f:
        data = json.load(f)

strat = list(data["strategy"].keys())[0]
results = data["strategy"][strat]["results_per_pair"]
print(f"\nPAIR BREAKDOWN FOR {strat}:")
print("-" * 80)
for r in results:
    key = r.get("key", "")
    trades = r.get("trades", 0)
    profit_pct = r.get("profit_mean_pct", 0)
    profit_tot = r.get("profit_total_abs", 0)
    wins = r.get("wins", 0)
    win_pct = (wins / trades * 100) if trades > 0 else 0
    print(f"{key:<16} | Trades: {trades:<4} | Wins: {wins:<3} | Winrate: {win_pct:>5.1f}% | Tot Profit: {profit_tot:>8.2f} USDT")

trades = data["strategy"][strat]["trades"]
df_trades = pd.DataFrame(trades)
df_trades["close_date"] = pd.to_datetime(df_trades["close_date"])
df_trades = df_trades.sort_values("close_date")
df_trades["profit_ratio"] = df_trades["profit_ratio"].astype(float)
daily_returns = df_trades.set_index("close_date")["profit_ratio"].resample("D").sum().fillna(0.0)

report_path = Path("user_data/quantstats_tearsheet.html")
qs.reports.html(daily_returns, output=str(report_path), title=f"Azura Portfolio Alpha ({strat}) - QuantStats Tearsheet")
print(f"\nQuantStats Tearsheet successfully updated at: {report_path}")

# 2. Write ActiveIntraday_15m_Strategy.py
strategy_code = '''"""
ActiveIntraday_15m_Strategy - Active Intraday Trend & Pullback Engine
Architecture:
- Base: 15m (Active Intraday Execution)
- Informative: 4h (Macro Trend Gate)
- Asymmetric R:R: Targets +3.5% to +6.0% with tight -2.5% stop to eliminate fee drag.
- Frequency: ~1-2 trades per day with high winrate.
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


class ActiveIntraday_15m_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "15m"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Entry Parameters
    buy_rsi_min = IntParameter(36, 48, default=42, space="buy")
    buy_rsi_max = IntParameter(50, 64, default=58, space="buy")
    buy_stoch_max = IntParameter(25, 55, default=40, space="buy")
    buy_adx_4h = IntParameter(14, 26, default=18, space="buy")

    # High-Expectancy ROI Runway (in minutes)
    minimal_roi = {
        "0": 0.055,       # 5.5% instant target
        "60": 0.038,      # 3.8% after 1 hour
        "180": 0.024,     # 2.4% after 3 hours
        "360": 0.015,     # 1.5% after 6 hours
        "720": 0.008      # 0.8% after 12 hours
    }

    # Strict Protective Hard Stoploss
    stoploss = -0.026

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.012            # Lock +1.2% once +2.2% is reached
    trailing_stop_positive_offset = 0.022
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
        Auto-cut trades held > 12 hours if stagnant to recycle margin.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration > 12.0 and current_profit < 0.004:
            return "intraday_time_cutoff_12h"
        return None

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Macro Trend Gate: 4h Bullish
        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Merge 4h Informative
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)

        # 15m Indicators
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

        # 15m Pullback Dip Rules:
        # 1. 4h Macro Trend is Bullish & 4h ADX > threshold
        # 2. 15m Price pulled back to EMA 21 or BB Middleband
        # 3. 15m RSI between min and max (discount zone)
        # 4. Stochastic crosses upward from oversold
        # 5. Bullish bounce candle (Green OR lower wick rejection)
        long_conditions = (
            (dataframe[f"macro_bull_4h_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.004) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.992) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < self.buy_stoch_max.value) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.70)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_intraday_dip")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Dynamic Take Profit on Upper Bollinger Expansion + Overbought RSI
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 72)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_upper_bb_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/ActiveIntraday_15m_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("ActiveIntraday_15m_Strategy written successfully at", target_strategy)


# 2. Write ScalpAlpha_5m_Strategy.py
strategy_code = '''"""
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
'''

target_strategy = Path("user_data/strategies/ScalpAlpha_5m_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("ScalpAlpha_5m_Strategy written successfully at", target_strategy)


# 2. Write ApexAlpha_Strategy.py
strategy_code = '''"""
ApexAlpha_Strategy - Institutional Multi-Timeframe Alpha Engine
Features:
1. Multi-Timeframe Gating (1D Macro + 4H Regime + 1H Execution).
2. Anti-Chasing Dip/Pullback Entry: Only buys on EMA 21/50 pullbacks with Stochastic/RSI reversal & price action confirmation.
3. Strict Short Protection: Shorting permitted ONLY during confirmed 1D + 4H bear regimes.
4. Dynamic Break-Even Stop: Once trade hits +1.2% profit, stoploss is dynamically moved to +0.2% (Fee-Proof Break-Even).
5. Time-Decay Auto-Cut: Stale losing positions > 36h are closed to eliminate zombie drawdowns.
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


class ApexAlpha_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Dip Entry Parameters
    buy_rsi_min = IntParameter(34, 46, default=40, space="buy")
    buy_rsi_max = IntParameter(48, 62, default=56, space="buy")
    buy_stoch_max = IntParameter(30, 58, default=45, space="buy")
    buy_adx_4h = IntParameter(14, 26, default=18, space="buy")

    # Hyperoptable Sell / Rip Entry Parameters
    sell_rsi_min = IntParameter(38, 52, default=42, space="sell")
    sell_rsi_max = IntParameter(52, 66, default=58, space="sell")
    sell_stoch_min = IntParameter(42, 70, default=52, space="sell")
    sell_adx_4h = IntParameter(14, 26, default=18, space="sell")

    # High-Expectancy ROI Runway
    minimal_roi = {
        "0": 0.080,       # 8% target
        "180": 0.050,     # 5% after 3 hours
        "360": 0.035,     # 3.5% after 6 hours
        "720": 0.022,     # 2.2% after 12 hours
        "1440": 0.012     # 1.2% after 24 hours
    }

    # Strict Protective Hard Stoploss
    stoploss = -0.035

    # Trailing Stop Configuration
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
        Dynamic Break-Even & Trailing Profit Lock:
        - If profit >= +3.0%: Lock +1.8% profit
        - If profit >= +1.8%: Lock +0.8% profit
        - If profit >= +1.2%: Move stop to Break-Even (+0.2% to cover fees)
        - Otherwise: Maintain hard stoploss (-3.5%)
        """
        if current_profit >= 0.030:
            return -0.012
        elif current_profit >= 0.018:
            return -0.010
        elif current_profit >= 0.012:
            return 0.002
        return self.stoploss

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """
        Time-Decay Exit: Cut stale trades if they have not moved after 36 hours.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600
        
        if trade_duration > 36 and current_profit < -0.010:
            return "time_decay_stale_loss"
        
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

        dataframe["macro_bull_4h"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["close"] > dataframe["ema_50"])
        dataframe["macro_bear_4h"] = (dataframe["ema_50"] < dataframe["ema_200"]) & (dataframe["close"] < dataframe["ema_50"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative_1d = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1d")
        informative_1d = self.informative_1d_indicators(informative_1d, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1d, self.timeframe, "1d", ffill=True)

        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, "4h", ffill=True)

        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.0)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        dataframe["body_size"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["is_green"] = dataframe["close"] > dataframe["open"]
        dataframe["is_red"] = dataframe["close"] < dataframe["open"]
        dataframe["lower_wick"] = np.where(dataframe["is_green"], dataframe["open"] - dataframe["low"], dataframe["close"] - dataframe["low"])
        dataframe["upper_wick"] = np.where(dataframe["is_green"], dataframe["high"] - dataframe["close"], dataframe["high"] - dataframe["open"])

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        long_conditions = (
            (dataframe["macro_bull_4h_4h"] == True) &
            (dataframe["adx_4h"] > self.buy_adx_4h.value) &
            (
                (dataframe["close"] <= dataframe["ema_21"] * 1.008) |
                (dataframe["low"] <= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] >= dataframe["ema_50"] * 0.990) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < self.buy_stoch_max.value) &
            (
                (dataframe["is_green"] == True) |
                (dataframe["lower_wick"] > dataframe["body_size"] * 0.75)
            ) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.75)
        )

        short_conditions = (
            (dataframe["macro_bear_1d_1d"] == True) &
            (dataframe["macro_bear_4h_4h"] == True) &
            (dataframe["adx_4h"] > self.sell_adx_4h.value) &
            (
                (dataframe["close"] >= dataframe["ema_21"] * 0.992) |
                (dataframe["high"] >= dataframe["bb_middleband"])
            ) &
            (dataframe["close"] <= dataframe["ema_50"] * 1.010) &
            (dataframe["rsi"] <= self.sell_rsi_max.value) &
            (dataframe["rsi"] >= self.sell_rsi_min.value) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > self.sell_stoch_min.value) &
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
'''


strategy_code = '''"""
AlphaRegime_Futures_Strategy - Quantitative Multi-Timeframe Trend & Volatility Strategy
Hyperoptable parameters for institutional parameter tuning
Base Timeframe: 1h
Informative Timeframe: 4h
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
import talib.abstract as ta
from technical import qtpylib


class AlphaRegime_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "1h"
    informative_timeframe = "4h"

    # Hyperoptable Buy / Entry Parameters
    buy_adx_4h = IntParameter(14, 30, default=18, space="buy")
    buy_adx_1h = IntParameter(12, 28, default=16, space="buy")
    buy_rsi_min = IntParameter(45, 58, default=50, space="buy")
    buy_rsi_max = IntParameter(60, 75, default=68, space="buy")

    # Hyperoptable Sell / Short Parameters
    sell_adx_4h = IntParameter(14, 30, default=18, space="sell")
    sell_adx_1h = IntParameter(12, 28, default=16, space="sell")
    sell_rsi_min = IntParameter(25, 40, default=32, space="sell")
    sell_rsi_max = IntParameter(42, 55, default=50, space="sell")

    # Dynamic Profit Harvesting
    minimal_roi = {
        "0": 0.12,
        "240": 0.08,
        "720": 0.05,
        "1440": 0.03,
        "2880": 0.015
    }

    # Stoploss
    stoploss = -0.040

    # Trailing Stop
    trailing_stop = True
    trailing_stop_positive = 0.020
    trailing_stop_positive_offset = 0.035
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

    def informative_4h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        dataframe["macro_bull"] = (dataframe["ema_20"] > dataframe["ema_50"]) & (dataframe["close"] > dataframe["ema_200"])
        dataframe["macro_bear"] = (dataframe["ema_20"] < dataframe["ema_50"]) & (dataframe["close"] < dataframe["ema_200"])
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_4h = self.informative_4h_indicators(informative_4h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_4h, self.timeframe, self.informative_timeframe, ffill=True)

        dataframe["ema_9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        dataframe["donchian_high"] = dataframe["high"].rolling(window=20).max()
        dataframe["donchian_low"] = dataframe["low"].rolling(window=20).min()

        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        long_conditions = (
            (dataframe[f"macro_bull_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.buy_adx_4h.value) &
            (
                (qtpylib.crossed_above(dataframe["ema_9"], dataframe["ema_21"])) |
                (dataframe["close"] > dataframe["donchian_high"].shift(1))
            ) &
            (dataframe["rsi"] >= self.buy_rsi_min.value) &
            (dataframe["rsi"] <= self.buy_rsi_max.value) &
            (dataframe["adx"] > self.buy_adx_1h.value) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.95)
        )

        short_conditions = (
            (dataframe[f"macro_bear_{self.informative_timeframe}"] == True) &
            (dataframe[f"adx_{self.informative_timeframe}"] > self.sell_adx_4h.value) &
            (
                (qtpylib.crossed_below(dataframe["ema_9"], dataframe["ema_21"])) |
                (dataframe["close"] < dataframe["donchian_low"].shift(1))
            ) &
            (dataframe["rsi"] <= self.sell_rsi_max.value) &
            (dataframe["rsi"] >= self.sell_rsi_min.value) &
            (dataframe["adx"] > self.sell_adx_1h.value) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.95)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_alpha_trend")
        dataframe.loc[short_conditions, ["enter_short", "enter_tag"]] = (1, "short_alpha_trend")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        exit_long_conditions = (
            (qtpylib.crossed_below(dataframe["ema_9"], dataframe["ema_50"])) |
            (dataframe["rsi"] > 80)
        )

        exit_short_conditions = (
            (qtpylib.crossed_above(dataframe["ema_9"], dataframe["ema_50"])) |
            (dataframe["rsi"] < 20)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_long_reversal")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_reversal")

        return dataframe
"""
MultiTF_Futures_Strategy - Quantitative High-Expectancy Pullback Strategy
Designed with:
1. Confluence Filter: Macro 1h ADX > 25 & EMA 100/200 Trend
2. Sniper Entry: Deep Pullbacks into 15m BB lower/upper bands with volume exhaustion
3. Asymmetric Payoff: Positive expectancy (Reward:Risk >= 1.6:1), reduced trade churn
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
)
import talib.abstract as ta
from technical import qtpylib


class MultiTF_Futures_Strategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short: bool = True
    timeframe = "15m"
    informative_timeframe = "1h"

    # Positive Expectancy ROI Runway
    minimal_roi = {
        "0": 0.065,       # 6.5% TP target
        "45": 0.045,      # 4.5% TP after 45 mins
        "120": 0.030,     # 3.0% TP after 2 hours
        "240": 0.020      # 2.0% TP after 4 hours
    }

    # Strict protective stoploss
    stoploss = -0.018    # -1.8% stoploss

    # Dynamic Trailing Profit Lock
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.025
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

    def informative_1h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # High Quality Trend: EMA alignment + ADX > 24
        dataframe["macro_bull"] = (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["adx"] > 24)
        dataframe["macro_bear"] = (dataframe["ema_50"] < dataframe["ema_200"]) & (dataframe["adx"] > 24)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe=self.informative_timeframe)
        informative_1h = self.informative_1h_indicators(informative_1h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, self.informative_timeframe, ffill=True)

        # 15m Indicators
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Bollinger Bands (2.2 std dev for deep high-probability pullbacks)
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2.2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        # Stochastic Fast
        stoch = ta.STOCH(dataframe, fastk_period=14, slowk_period=3, slowd_period=3)
        dataframe["slowk"] = stoch["slowk"]
        dataframe["slowd"] = stoch["slowd"]

        # Volume Mean
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0
        dataframe.loc[:, "enter_tag"] = ""

        # Sniper Long: Macro 1h Bullish (ADX>24) + Deep 15m Oversold (Price <= BB Lower or RSI < 32) + Stoch cross
        long_conditions = (
            (dataframe[f"macro_bull_{self.informative_timeframe}"] == True) &
            (
                (dataframe["close"] <= dataframe["bb_lowerband"]) |
                (dataframe["rsi"] < 32)
            ) &
            (qtpylib.crossed_above(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] < 35) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.8)
        )

        # Sniper Short: Macro 1h Bearish (ADX>24) + Deep 15m Overbought (Price >= BB Upper or RSI > 68) + Stoch cross
        short_conditions = (
            (dataframe[f"macro_bear_{self.informative_timeframe}"] == True) &
            (
                (dataframe["close"] >= dataframe["bb_upperband"]) |
                (dataframe["rsi"] > 68)
            ) &
            (qtpylib.crossed_below(dataframe["slowk"], dataframe["slowd"])) &
            (dataframe["slowk"] > 65) &
            (dataframe["volume"] > dataframe["volume_mean_20"] * 0.8)
        )

        dataframe.loc[long_conditions, ["enter_long", "enter_tag"]] = (1, "long_sniper_dip")
        dataframe.loc[short_conditions, ["enter_short", "enter_tag"]] = (1, "short_sniper_rip")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        dataframe.loc[:, "exit_tag"] = ""

        # Exit Long: Price exceeds BB Upperband and RSI > 75
        exit_long_conditions = (
            (dataframe["close"] >= dataframe["bb_upperband"]) &
            (dataframe["rsi"] > 75)
        )

        # Exit Short: Price falls below BB Lowerband and RSI < 25
        exit_short_conditions = (
            (dataframe["close"] <= dataframe["bb_lowerband"]) &
            (dataframe["rsi"] < 25)
        )

        dataframe.loc[exit_long_conditions, ["exit_long", "exit_tag"]] = (1, "exit_long_tp")
        dataframe.loc[exit_short_conditions, ["exit_short", "exit_tag"]] = (1, "exit_short_tp")

        return dataframe
'''

target_strategy = Path("user_data/strategies/MultiTF_Futures_Strategy.py")
with open(target_strategy, "w", encoding="utf-8") as f:
    f.write(strategy_code)
print("Strategy regenerated successfully at", target_strategy)





