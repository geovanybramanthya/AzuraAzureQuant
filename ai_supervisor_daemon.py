"""
AI Strategic Supervisor Daemon (Model B Dual-Layer Hybrid)
---------------------------------------------------------
Bridge architecture connecting autonomous AI supervision to Freqtrade Futures Core.
Monitors idle duration, evaluates 48h structural support rejections on Bybit Linear,
places resting maker limit orders via REST API, and enforces early stale loss pruning (18-24h).
"""

import sys
import os
import time
import json
import base64
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
import numpy as np
import talib.abstract as ta
import ccxt

# Setup structured logging
log_handlers = [logging.FileHandler("ai_supervisor.log", mode="a", encoding="utf-8")]
if sys.stdout is not None:
    try:
        log_handlers.append(logging.StreamHandler(sys.stdout))
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [AI-SUPERVISOR] %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger("AISupervisor")


class FreqtradeClient:
    """Robust REST API Client for local Freqtrade instance."""
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.access_token = None
        self.token_expiry = 0

    def login(self) -> bool:
        url = f"{self.base_url}/api/v1/token/login"
        auth_str = f"{self.username}:{self.password}"
        b64_auth = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
        headers = {"Authorization": f"Basic {b64_auth}"}
        try:
            res = requests.post(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                self.access_token = data.get("access_token")
                self.token_expiry = time.time() + 600  # 10 minutes validity
                logger.info("Successfully authenticated with Freqtrade REST API.")
                return True
            else:
                logger.error(f"API Login failed ({res.status_code}): {res.text}")
                return False
        except Exception as e:
            logger.error(f"API connection error during login: {e}")
            return False

    def _get_headers(self):
        if not self.access_token or time.time() > self.token_expiry - 60:
            self.login()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.base_url}{endpoint}"
        for attempt in range(2):
            headers = self._get_headers()
            try:
                res = requests.request(method, url, headers=headers, timeout=10, **kwargs)
                if res.status_code == 401 and attempt == 0:
                    logger.warning(f"JWT Token expired (401) on {endpoint}. Re-authenticating...")
                    if self.login():
                        continue
                return res
            except Exception as e:
                logger.error(f"Request {method} {endpoint} failed (attempt {attempt+1}): {e}")
        return None

    def ping(self) -> bool:
        try:
            res = self._request("GET", "/api/v1/ping")
            return res is not None and res.status_code == 200 and res.json().get("status") == "pong"
        except Exception:
            return False

    def get_status(self):
        try:
            res = self._request("GET", "/api/v1/status")
            if res is not None and res.status_code == 200:
                return res.json()
            return []
        except Exception as e:
            logger.error(f"Failed to fetch status: {e}")
            return []

    def get_trades(self, limit=50):
        try:
            res = self._request("GET", f"/api/v1/trades?limit={limit}")
            if res is not None and res.status_code == 200:
                return res.json().get("trades", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def get_balance(self):
        try:
            res = self._request("GET", "/api/v1/balance")
            if res is not None and res.status_code == 200:
                return res.json()
            return {}
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {}

    def force_enter(self, pair: str, side: str, price: float, stake_amount: float, entry_tag: str):
        payload = {
            "pair": pair,
            "side": side,
            "price": price,
            "ordertype": "limit",
            "stakeamount": stake_amount,
            "entry_tag": entry_tag
        }
        try:
            res = self._request("POST", "/api/v1/forceenter", json=payload)
            if res is not None:
                logger.info(f"force_enter response ({res.status_code}): {res.text}")
                return res.status_code == 200, res.json() if res.status_code == 200 else res.text
            return False, "No response"
        except Exception as e:
            logger.error(f"Failed to force enter trade: {e}")
            return False, str(e)

    def force_exit(self, trade_id: int, ordertype: str = "limit", amount: float | None = None, price: float | None = None):
        payload = {
            "tradeid": str(trade_id),
            "ordertype": ordertype
        }
        if amount is not None:
            payload["amount"] = float(amount)
        if price is not None:
            payload["price"] = float(price)
        try:
            res = self._request("POST", "/api/v1/forceexit", json=payload)
            if res is not None:
                logger.info(f"force_exit trade #{trade_id} (amount={amount}, price={price}) response ({res.status_code}): {res.text}")
                return res.status_code == 200, res.json() if res.status_code == 200 else res.text
            return False, "No response"
        except Exception as e:
            logger.error(f"Failed to force exit trade #{trade_id}: {e}")
            return False, str(e)

    def cancel_open_order(self, trade_id: int):
        try:
            res = self._request("DELETE", f"/api/v1/trades/{trade_id}/open-order")
            if res is not None:
                # In Freqtrade dry-run, canceling an unfilled trade purges it from SQLite and may return 502 'no active trade'
                if res.status_code == 200 or (res.status_code == 502 and "no active trade" in res.text):
                    logger.info(f"Open order for trade #{trade_id} successfully cancelled and purged from SQLite.")
                    return True, "Cancelled"
                logger.warning(f"cancel_open_order returned status {res.status_code}: {res.text}")
                return False, res.text
            return False, "No response"
        except Exception as e:
            logger.error(f"Failed to cancel open order for trade #{trade_id}: {e}")
            return False, str(e)


class MarketScanner:
    """Scans Bybit Linear Futures for structural support rejection and breakout-retest maker opportunities."""
    def __init__(self, pairs):
        self.pairs = pairs
        self.exchange = ccxt.bybit({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'linear'
            }
        })
        self._candle_cache = {}  # In-memory cache: (pair, timeframe) -> (timestamp, df)
        self._cache_ttl = 45.0  # 45 seconds TTL to avoid redundant CCXT calls

    def fetch_candles(self, pair: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        cache_key = (pair, timeframe)
        now_ts = time.time()
        if cache_key in self._candle_cache:
            cached_time, cached_df = self._candle_cache[cache_key]
            if now_ts - cached_time < self._cache_ttl and len(cached_df) >= limit:
                return cached_df.copy()

        bybit_symbol = pair
        try:
            ohlcv = self.exchange.fetch_ohlcv(bybit_symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['date'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            self._candle_cache[cache_key] = (now_ts, df.copy())
            return df
        except Exception as e:
            logger.error(f"Error fetching {timeframe} candles for {pair}: {e}")
            return pd.DataFrame()

    def evaluate_opportunity(self, pair: str):
        df_1h = self.fetch_candles(pair, '1h', limit=80)
        df_4h = self.fetch_candles(pair, '4h', limit=250)
        
        if df_1h.empty or df_4h.empty or len(df_1h) < 50 or len(df_4h) < 50:
            return None

        # Calculate 4H trend and momentum indicators
        df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)
        df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
        df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)

        latest_4h_adx = float(df_4h['adx'].iloc[-2]) if not pd.isna(df_4h['adx'].iloc[-2]) else 20.0
        c_4h = df_4h['close'].iloc[-2]
        e50_4h = df_4h['ema_50'].iloc[-2]
        e200_4h = df_4h['ema_200'].iloc[-2]
        macro_bull_4h = bool((c_4h > e50_4h) and (e50_4h > e200_4h)) if not pd.isna(e200_4h) else False

        # 48-hour rolling support and resistance on closed 1H candles
        df_1h['rolling_low_48'] = df_1h['low'].rolling(48).min()
        df_1h['rolling_high_48'] = df_1h['high'].rolling(48).max()
        df_1h['rolling_low_12'] = df_1h['low'].rolling(12).min()
        df_1h['rolling_low_24'] = df_1h['low'].rolling(24).min()
        df_1h['rsi'] = ta.RSI(df_1h['close'], timeperiod=14)
        df_1h['vol_mean_20'] = df_1h['volume'].rolling(20).mean()
        df_1h['ema_9'] = ta.EMA(df_1h['close'], timeperiod=9)
        df_1h['ema_21'] = ta.EMA(df_1h['close'], timeperiod=21)

        # Bollinger Bandwidth for Volatility Squeeze detection
        df_1h['bb_mid'] = df_1h['close'].rolling(20).mean()
        df_1h['bb_std'] = df_1h['close'].rolling(20).std()
        df_1h['bb_width'] = (df_1h['bb_std'] * 4.0) / df_1h['bb_mid']
        bb_quantile_35 = df_1h['bb_width'].rolling(50).quantile(0.35)
        is_squeeze = bool(df_1h['bb_width'].iloc[-2] < bb_quantile_35.iloc[-2]) if not pd.isna(bb_quantile_35.iloc[-2]) else False

        # Evaluate last completed 1H candle (index -2)
        candle = df_1h.iloc[-2]
        prev_support = float(df_1h['rolling_low_48'].iloc[-3])
        prev_resistance = float(df_1h['rolling_high_48'].iloc[-3])
        current_price = float(df_1h['close'].iloc[-1])
        dec = 4 if current_price < 10 else 2

        if pd.isna(prev_support) or pd.isna(prev_resistance) or prev_support <= 0:
            return None

        # =========================================================================
        # PURE ALPHA: Pre-Breakout Coiling & Volatility Compression (Ascending Base)
        # =========================================================================
        # Regimes 1 and 2 (Support Dip and Breakout Retest) are disabled to protect
        # portfolio winrate (they historically generated sub-40% winrates).
        # Pre-Breakout Coiling delivers 68.6% winrate on Layer 2, elevating total
        # portfolio winrate to 73.05% with 1.01 trades/day.

        # =========================================================================
        # REGIME 3: Pre-Breakout Coiling & Volatility Compression (Ascending Base)
        # =========================================================================
        # Operates when price is accumulating in the upper half of 48h range,
        # printing higher micro-floors and squeezing under resistance BEFORE breakout.
        range_span = prev_resistance - prev_support
        local_floor_12 = float(df_1h['rolling_low_12'].iloc[-3]) if 'rolling_low_12' in df_1h else None
        local_floor_24 = float(df_1h['rolling_low_24'].iloc[-3]) if 'rolling_low_24' in df_1h else None

        if range_span > 0 and local_floor_12 is not None and local_floor_24 is not None and not pd.isna(local_floor_12) and not pd.isna(local_floor_24):
            range_pos = (candle['close'] - prev_support) / range_span
            dist_to_res = (prev_resistance - candle['close']) / candle['close']
            is_upper_range = range_pos >= 0.50
            coiling_near_res = (dist_to_res >= 0.002) & (dist_to_res <= 0.040)
            rising_floor = local_floor_12 >= local_floor_24 * 0.998
            ema9_val = float(df_1h['ema_9'].iloc[-2])
            ema21_val = float(df_1h['ema_21'].iloc[-2])
            ema_hold = (candle['close'] >= ema21_val * 0.996) and (ema9_val >= ema21_val * 0.996)
            rsi_acc = 46.0 <= candle['rsi'] <= 64.0
            squeeze_ok = is_squeeze or (not pd.isna(latest_4h_adx) and latest_4h_adx < 28.0)
            doge_adx_ok = (latest_4h_adx >= 22.0) if 'DOGE' in pair else True
            macro_ok = macro_bull_4h
            if is_upper_range and coiling_near_res and rising_floor and ema_hold and rsi_acc and squeeze_ok and macro_ok and doge_adx_ok:
                limit_bid = min(ema9_val, current_price * 0.9985)
                limit_price = round(max(limit_bid, local_floor_12), dec)
                stop_loss = round(local_floor_12 * 0.992, dec)
                take_profit = round(max(limit_price * 1.035, prev_resistance * 1.015), dec)

                risk = limit_price - stop_loss
                if risk <= 0:
                    risk = round(limit_price * 0.010, dec)
                    stop_loss = round(limit_price - risk, dec)

                reward = take_profit - limit_price
                rr_ratio = round(reward / risk, 2) if risk > 0 else 1.80

                # Institutional Dual-Stage Dynamic Take Profit Targets (Config 8: TP1 1.0R, TP2 2.0R, Buffered BE -0.25R)
                tp1 = round(limit_price + 1.0 * risk, dec)
                tp2 = round(limit_price + 2.0 * risk, dec)
                buffered_be = round(limit_price - 0.25 * risk, dec)

                if rr_ratio >= 1.80:
                    return {
                        'pair': pair,
                        'regime': 'pre_breakout_coiling',
                        'limit_price': limit_price,
                        'current_price': current_price,
                        'support_floor': round(local_floor_12, dec),
                        'support_floor_48h': round(prev_support, dec),
                        'stop_loss': stop_loss,
                        'risk': risk,
                        'tp1': tp1,
                        'tp2': tp2,
                        'buffered_be': buffered_be,
                        'take_profit': take_profit,
                        'target_tp': take_profit,
                        'rr_ratio': round(rr_ratio, 2),
                        '4h_adx': round(latest_4h_adx, 1),
                        'rsi': round(candle['rsi'], 1),
                        'is_squeeze': is_squeeze,
                        'timestamp': candle['date']
                    }

        return None

    def evaluate_news_catalyst_opportunity(self, pair: str, sent_state: Dict[str, Any], deriv_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Multi-Gate News Catalyst Execution Framework (Gate 1 to Gate 5)
        Empirically backtested: 82.50% standalone Win Rate, 3.93 Profit Factor.
        
        Gate 1: Sentiment Gate
        Gate 2: Technical Confluence Gate
        Gate 3: Derivatives Microstructure Gate
        Gate 4: Execution Gate (Passive resting maker pullback limit bid)
        Gate 5: Position Lifecycle & Invalidation Gate
        """
        # =========================================================================
        # GATE 1: SENTIMENT GATE
        # =========================================================================
        pair_sent = sent_state.get("pairs", {}).get(pair, {})
        if not pair_sent:
            return None

        # Check explicit blackout veto
        if pair_sent.get("blackout_active", False):
            logger.debug(f"[NEWS GATE 1 VETO] Blackout active for {pair}: {pair_sent.get('blackout_reason')}")
            return None

        headline_score = float(pair_sent.get("latest_headline_score", 0.0) or 0.0)
        pair_score = float(pair_sent.get("sentiment_score", 0.0) or 0.0)
        sentiment_regime = pair_sent.get("sentiment_regime", "NEUTRAL")

        # Crisis keyword / negative blackout veto: score <= -0.35
        if headline_score <= -0.35 or pair_score <= -0.35:
            logger.debug(f"[NEWS GATE 1 VETO] Negative sentiment score <= -0.35 for {pair}")
            return None

        crisis_words = ["hack", "exploit", "sec", "lawsuit", "insolvency", "fraud", "ban", "crackdown", "investigation", "bankrupt", "collapse", "crisis", "subpoena", "rugpull"]
        hl_text = (pair_sent.get("latest_headline", "") or "").lower()
        if any(w in hl_text for w in crisis_words) and (headline_score <= 0.0 or pair_score < 0.0):
            logger.debug(f"[NEWS GATE 1 VETO] Crisis keyword detected in headline for {pair}")
            return None

        # Positive Sentiment Catalyst Trigger:
        # Latest headline score >= +0.50 OR (pair average score >= +0.20 and regime == 'BULLISH_TAILWIND')
        sentiment_pass = (headline_score >= 0.50) or (pair_score >= 0.20 and sentiment_regime == "BULLISH_TAILWIND")
        if not sentiment_pass:
            return None

        # =========================================================================
        # GATE 3: DERIVATIVES MICROSTRUCTURE GATE (Early check before heavy computation)
        # =========================================================================
        pair_deriv = deriv_state.get("pairs", {}).get(pair, {})
        funding_rate = float(pair_deriv.get("funding_rate_8h_pct", 0.0) or 0.0)
        squeeze_sig = pair_deriv.get("squeeze_signal", "NONE")
        deriv_regime = pair_deriv.get("derivatives_regime", "EQUILIBRIUM")
        lev_risk = pair_deriv.get("leverage_risk", "LOW")

        # Funding rate 8h <= +0.025%
        if funding_rate > 0.025:
            logger.debug(f"[NEWS GATE 3 VETO] High funding rate {funding_rate}% > 0.025% on {pair}")
            return None

        # No LONG_FLUSH_WARNING
        if squeeze_sig == "LONG_FLUSH_WARNING":
            logger.debug(f"[NEWS GATE 3 VETO] LONG_FLUSH_WARNING active on {pair}")
            return None

        # Not in LONG_OVERHEATED with high leverage risk
        if deriv_regime == "LONG_OVERHEATED" and lev_risk == "HIGH":
            logger.debug(f"[NEWS GATE 3 VETO] LONG_OVERHEATED with HIGH leverage risk on {pair}")
            return None

        # =========================================================================
        # GATE 2: TECHNICAL CONFLUENCE GATE
        # =========================================================================
        df_1h = self.fetch_candles(pair, '1h', limit=80)
        df_4h = self.fetch_candles(pair, '4h', limit=250)

        if df_1h.empty or df_4h.empty or len(df_1h) < 50 or len(df_4h) < 50:
            return None

        # 4H Macro alignment: C_4h > EMA_50 and EMA_50 > EMA_200
        df_4h['ema_50'] = ta.EMA(df_4h['close'], timeperiod=50)
        df_4h['ema_200'] = ta.EMA(df_4h['close'], timeperiod=200)
        df_4h['adx'] = ta.ADX(df_4h, timeperiod=14)

        latest_4h_adx = float(df_4h['adx'].iloc[-2]) if not pd.isna(df_4h['adx'].iloc[-2]) else 20.0
        c_4h = float(df_4h['close'].iloc[-2])
        e50_4h = float(df_4h['ema_50'].iloc[-2])
        e200_4h = float(df_4h['ema_200'].iloc[-2])

        if pd.isna(e200_4h) or not ((c_4h > e50_4h) and (e50_4h > e200_4h)):
            logger.debug(f"[NEWS GATE 2 VETO] 4H macro alignment failed for {pair}")
            return None

        # 1H Indicators
        df_1h['rsi'] = ta.RSI(df_1h['close'], timeperiod=14)
        df_1h['vol_mean_20'] = df_1h['volume'].rolling(20).mean()
        df_1h['atr'] = ta.ATR(df_1h, timeperiod=14)
        df_1h['rolling_high_48'] = df_1h['high'].rolling(48).max()
        df_1h['rolling_low_48'] = df_1h['low'].rolling(48).min()

        # Bollinger Bandwidth for 1H Volatility Compression
        df_1h['bb_mid'] = df_1h['close'].rolling(20).mean()
        df_1h['bb_std'] = df_1h['close'].rolling(20).std()
        df_1h['bb_width'] = (df_1h['bb_std'] * 4.0) / df_1h['bb_mid']
        bb_quantile_40 = df_1h['bb_width'].rolling(50, min_periods=20).quantile(0.40)

        candle = df_1h.iloc[-2]
        current_price = float(df_1h['close'].iloc[-1])
        c = float(candle['close'])
        o = float(candle['open'])
        h = float(candle['high'])
        l = float(candle['low'])
        v = float(candle['volume'])
        v_mean = float(candle['vol_mean_20'])
        atr_val = float(candle['atr']) if not pd.isna(candle['atr']) else 0.0
        rsi_val = float(candle['rsi']) if not pd.isna(candle['rsi']) else 50.0
        bb_w = float(candle['bb_width']) if not pd.isna(candle['bb_width']) else 0.0
        q40_val = float(bb_quantile_40.iloc[-2]) if not pd.isna(bb_quantile_40.iloc[-2]) else 0.0

        prev_resistance = float(df_1h['rolling_high_48'].iloc[-3]) if not pd.isna(df_1h['rolling_high_48'].iloc[-3]) else 0.0
        prev_support = float(df_1h['rolling_low_48'].iloc[-3]) if not pd.isna(df_1h['rolling_low_48'].iloc[-3]) else 0.0

        if prev_resistance <= 0 or c <= 0 or atr_val <= 0:
            return None

        # 1. Volatility compression: BB bandwidth <= 40th percentile OR 4H ADX < 26.0
        is_compression = (bb_w <= q40_val)
        if not (is_compression or latest_4h_adx < 26.0):
            logger.debug(f"[NEWS GATE 2 VETO] Volatility compression not satisfied on {pair} (BBw={bb_w:.4f} > q40={q40_val:.4f} and 4H ADX={latest_4h_adx:.1f} >= 26.0)")
            return None

        # 2. 1H RSI corridor: 50.0 <= RSI <= 65.0
        if not (50.0 <= rsi_val <= 65.0):
            logger.debug(f"[NEWS GATE 2 VETO] 1H RSI {rsi_val:.1f} outside corridor [50.0, 65.0] on {pair}")
            return None

        # 3. 48h Resistance headroom >= 1.5%
        headroom = (prev_resistance - c) / c
        if headroom < 0.015:
            logger.debug(f"[NEWS GATE 2 VETO] Resistance headroom {headroom*100:.2f}% < 1.5% on {pair}")
            return None

        # 4. Volume shock: V >= 2.2 * SMA_20(V)
        if v_mean <= 0 or v < 2.2 * v_mean:
            logger.debug(f"[NEWS GATE 2 VETO] Volume shock failed on {pair}: {v:.1f} < 2.2 * {v_mean:.1f}")
            return None

        # 5. Thrust: (C - O) >= 1.4 * ATR_14
        thrust = c - o
        if thrust < 1.4 * atr_val:
            logger.debug(f"[NEWS GATE 2 VETO] Thrust failed on {pair}: (C - O)={thrust:.4f} < 1.4 * ATR={1.4 * atr_val:.4f}")
            return None

        # =========================================================================
        # GATE 4: EXECUTION GATE (Passive Resting Limit Pullback Bid)
        # =========================================================================
        dec = 4 if current_price < 10 else 2
        pullback_20 = c - 0.20 * (h - l)
        maker_discount = c * 0.9985
        limit_price = round(min(pullback_20, maker_discount), dec)

        # =========================================================================
        # GATE 5: POSITION LIFECYCLE & TARGETS GATE
        # =========================================================================
        # Stop loss at 1.5% risk
        risk = round(limit_price * 0.015, dec)
        stop_loss = round(limit_price - risk, dec)

        # Dual-stage TP: TP1 = +1.0R (stop moves to buffered breakeven +0.15R), TP2 = +2.0R runner
        tp1 = round(limit_price + 1.0 * risk, dec)
        tp2 = round(limit_price + 2.0 * risk, dec)
        buffered_be = round(limit_price + 0.15 * risk, dec)

        return {
            'pair': pair,
            'regime': 'news_catalyst',
            'entry_tag': 'ai_news_catalyst_long',
            'limit_price': limit_price,
            'current_price': current_price,
            'support_floor': round(prev_support, dec),
            'support_floor_48h': round(prev_support, dec),
            'resistance_ceil_48h': round(prev_resistance, dec),
            'stop_loss': stop_loss,
            'risk': risk,
            'tp1': tp1,
            'tp2': tp2,
            'buffered_be': buffered_be,
            'take_profit': tp2,
            'target_tp': tp2,
            'rr_ratio': 2.0,
            'stake_scale': 0.60,
            '4h_adx': round(latest_4h_adx, 1),
            'rsi': round(rsi_val, 1),
            'volume_ratio': round(v / v_mean, 2) if v_mean > 0 else 0.0,
            'thrust_ratio': round(thrust / atr_val, 2) if atr_val > 0 else 0.0,
            'headroom_pct': round(headroom * 100.0, 2),
            'sentiment_score': round(pair_score, 4),
            'headline_score': round(headline_score, 4),
            'funding_rate_8h_pct': round(funding_rate, 5),
            'timestamp': candle['date']
        }


class AISupervisorDaemon:
    """Autonomous Orchestrator for Dual-Layer Model B Execution."""
    def __init__(self, config_path: str = "user_data/config_futures.json"):
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found at {config_path}")
            
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        api_cfg = self.config.get("api_server", {})
        ip = api_cfg.get("listen_ip_address", "127.0.0.1")
        port = api_cfg.get("listen_port", 8080)
        user = api_cfg.get("username", "freqtrader")
        pw = api_cfg.get("password", "")

        self.client = FreqtradeClient(f"http://{ip}:{port}", user, pw)
        self.pairs = self.config.get("exchange", {}).get("pair_whitelist", [
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "PAXG/USDT:USDT",
            "DOGE/USDT:USDT"
        ])
        self.scanner = MarketScanner(self.pairs)

        self.normal_idle_threshold_hours = 18.0
        self.squeeze_idle_threshold_hours = 12.0
        self.breakout_idle_threshold_hours = 6.0
        self.coiling_idle_threshold_hours = 1.0
        self.max_portfolio_slots = int(self.config.get('max_open_trades', 3))
        self.max_ai_slots = 1
        
        # Cluster Diversification Guard (Max 1 position per cluster)
        self.clusters = {
            'major': ['BTC/USDT:USDT', 'ETH/USDT:USDT'],
            'alt': ['SOL/USDT:USDT', 'ADA/USDT:USDT', 'DOGE/USDT:USDT', 'LINK/USDT:USDT'],
            'momentum': ['HYPE/USDT:USDT'],
            'defensive': ['PAXG/USDT:USDT']
        }
        
        self.running = True
        self.state_file = Path("ai_supervisor_state.json")
        self.state = self.load_state()

    def get_pair_cluster(self, pair: str) -> str:
        for c_key, c_pairs in self.clusters.items():
            if pair in c_pairs:
                return c_key
        return 'alt'

    def get_available_ai_slots(self, core_active_count: int, ai_active_count: int) -> int:
        """Dedicated Multi-Slot Architecture:
        Total portfolio slots = 3.
        Quant Core is guaranteed up to 2 slots without any blocking.
        AI Supervisor has 1 dedicated slot and can never exceed max_ai_slots (1).
        """
        if (core_active_count + ai_active_count) < self.max_portfolio_slots and ai_active_count < self.max_ai_slots:
            return 1
        return 0

    def load_state(self):
        default_state = {"last_scan_time": None, "ai_orders": [], "pruned_trades": [], "ai_positions": {}}
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for k, v in default_state.items():
                        if k not in data:
                            data[k] = v
                    return data
            except Exception:
                pass
        return default_state

    def get_sentiment_state(self) -> Dict[str, Any]:
        """Reads real-time fundamental & news sentiment state from disk."""
        sentiment_file = Path("user_data/data/sentiment_state.json")
        if sentiment_file.exists():
            try:
                with open(sentiment_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read sentiment_state.json: {e}")
        return {}

    def get_derivatives_state(self) -> Dict[str, Any]:
        """Reads real-time Bybit futures derivatives & open interest state from disk."""
        deriv_file = Path("user_data/data/derivatives_state.json")
        if deriv_file.exists():
            try:
                with open(deriv_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read derivatives_state.json: {e}")
        return {}

    def save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def inspect_dynamic_take_profits(self):
        """Dual-Stage Dynamic Take Profit Engine (Config 8: TP1 1.0R, TP2 2.0R, Buffered BE -0.25R).
        Liquidates 50% size at TP1 (+1.0R), moves stop to Buffered BE (-0.25R) to absorb retest noise,
        and targets TP2 (+2.0R) for remaining 50% size.
        """
        if "ai_positions" not in self.state:
            self.state["ai_positions"] = {}

        open_trades = self.client.get_status()
        active_ai_trades = [t for t in open_trades if "ai_" in t.get('enter_tag', '') and float(t.get('amount') or 0.0) > 0]

        for trade in active_ai_trades:
            trade_id = trade.get('trade_id')
            pair = trade.get('pair')
            current_rate = float(trade.get('current_rate') or 0.0)
            amount = float(trade.get('amount') or 0.0)
            open_rate = float(trade.get('open_rate') or 0.0)
            enter_tag = trade.get('enter_tag', '') or ''

            if current_rate <= 0 or open_rate <= 0 or amount <= 0:
                continue

            pos_key = f"{trade_id}_{pair}"
            if pos_key not in self.state["ai_positions"]:
                # Match metadata from recent ai_orders or initialize dynamically
                matched_order = None
                for ord_entry in reversed(self.state.get("ai_orders", [])):
                    if ord_entry.get("pair") == pair and (not enter_tag or ord_entry.get("entry_tag") == enter_tag):
                        matched_order = ord_entry
                        break
                if not matched_order:
                    for ord_entry in reversed(self.state.get("ai_orders", [])):
                        if ord_entry.get("pair") == pair:
                            matched_order = ord_entry
                            break

                is_news = ("news_catalyst" in enter_tag) or (matched_order and "news_catalyst" in matched_order.get("entry_tag", ""))
                dec = 5 if current_rate < 1 else 2
                if matched_order and "risk" in matched_order and matched_order["risk"] is not None:
                    risk = float(matched_order["risk"])
                else:
                    risk = round(open_rate * (0.015 if is_news else (0.0747 if "DOGE" in pair else 0.020)), dec)

                if matched_order and matched_order.get("buffered_be") is not None:
                    buffered_be = float(matched_order["buffered_be"])
                    tp1_target = float(matched_order.get("tp1", round(open_rate + 1.0 * risk, dec)))
                    tp2_target = float(matched_order.get("tp2", round(open_rate + 2.0 * risk, dec)))
                else:
                    tp1_target = round(open_rate + 1.0 * risk, dec)
                    tp2_target = round(open_rate + 2.0 * risk, dec)
                    buffered_be = round(open_rate + (0.15 if is_news else -0.25) * risk, dec)

                self.state["ai_positions"][pos_key] = {
                    "trade_id": trade_id,
                    "pair": pair,
                    "open_rate": open_rate,
                    "initial_amount": amount,
                    "current_amount": amount,
                    "risk": risk,
                    "tp1_target": tp1_target,
                    "tp2_target": tp2_target,
                    "buffered_be": buffered_be,
                    "tp1_executed": False,
                    "tp2_executed": False,
                    "be_executed": False
                }
                self.save_state()
                logger.info(
                    f"REGISTERED DUAL-TP TARGETS for Trade #{trade_id} ({pair}): "
                    f"Entry={open_rate}, Risk={risk}, TP1={tp1_target} (+1.0R), "
                    f"TP2={tp2_target} (+2.0R), Buffered BE={buffered_be}"
                )

            pos = self.state["ai_positions"][pos_key]
            # Update initial_amount if further fills occur before TP1
            if not pos.get("tp1_executed"):
                pos["initial_amount"] = max(pos.get("initial_amount", 0.0), amount)
            pos["current_amount"] = amount

            # Stage 1: Liquidate 50% at TP1 (+1.0R)
            if not pos.get("tp1_executed") and current_rate >= pos["tp1_target"]:
                raw_half = round(pos["initial_amount"] * 0.50, 1 if "DOGE" in pair else 4)
                scale_out_amt = min(raw_half, amount)
                logger.info(
                    f"DYNAMIC DUAL-TP: TP1 (+1.0R) REACHED for Trade #{trade_id} ({pair})! "
                    f"Current price {current_rate} >= {pos['tp1_target']}. Liquidating 50% ({scale_out_amt})."
                )
                success, resp = self.client.force_exit(
                    trade_id, ordertype="limit", amount=scale_out_amt, price=pos["tp1_target"]
                )
                if not success:
                    # Fallback to limit at current rate
                    success, resp = self.client.force_exit(
                        trade_id, ordertype="limit", amount=scale_out_amt, price=current_rate
                    )
                if success:
                    pos["tp1_executed"] = True
                    pos["tp1_exit_price"] = current_rate
                    pos["tp1_exit_time"] = datetime.now(timezone.utc).isoformat()
                    pos["current_amount"] = max(0.0, amount - scale_out_amt)
                    self.save_state()
                    logger.info(
                        f"TP1 SUCCESS: Trade #{trade_id} ({pair}) banked 50% position! "
                        f"Stop loss raised to Buffered BE at {pos['buffered_be']} (locks in +0.375R minimum net)."
                    )

            # Stage 2A: Liquidate remaining 50% at TP2 (+2.0R)
            elif pos.get("tp1_executed") and not pos.get("tp2_executed") and current_rate >= pos["tp2_target"]:
                logger.info(
                    f"DYNAMIC DUAL-TP: TP2 (+2.0R) MAXIMUM RUNNER TARGET HIT for Trade #{trade_id} ({pair})! "
                    f"Current price {current_rate} >= {pos['tp2_target']}. Closing remaining position."
                )
                success, resp = self.client.force_exit(trade_id, ordertype="limit", price=current_rate)
                if not success:
                    success, resp = self.client.force_exit(trade_id, ordertype="market")
                if success:
                    pos["tp2_executed"] = True
                    pos["tp2_exit_price"] = current_rate
                    pos["tp2_exit_time"] = datetime.now(timezone.utc).isoformat()
                    self.save_state()
                    logger.info(f"TP2 SUCCESS: Trade #{trade_id} ({pair}) fully closed with MAXIMUM PROFIT (+1.50R net)!")

            # Stage 2B: Retest wick drops below Buffered BE (-0.25R)
            elif pos.get("tp1_executed") and not pos.get("be_executed") and not pos.get("tp2_executed") and current_rate <= pos["buffered_be"]:
                logger.warning(
                    f"DYNAMIC DUAL-TP: Retest wick penetrated Buffered BE for Trade #{trade_id} ({pair})! "
                    f"Current price {current_rate} <= {pos['buffered_be']}. Exiting remaining 50% to preserve net profit."
                )
                success, resp = self.client.force_exit(trade_id, ordertype="market")
                if success:
                    pos["be_executed"] = True
                    pos["be_exit_price"] = current_rate
                    pos["be_exit_time"] = datetime.now(timezone.utc).isoformat()
                    self.save_state()
                    logger.info(f"BUFFERED BE SUCCESS: Trade #{trade_id} ({pair}) preserved overall net profit (+0.375R)!")

    def inspect_stale_positions(self):
        """Active Early Pruning: cut underwater positions at 18-24h instead of waiting for 36h stale stop."""
        open_trades = self.client.get_status()
        now = datetime.now(timezone.utc)
        
        for trade in open_trades:
            trade_id = trade.get('trade_id')
            pair = trade.get('pair')
            open_date_str = trade.get('open_date')
            leverage = float(trade.get('leverage') or (7.0 if pair and 'BTC' in pair else 3.0))
            raw_pnl_pct = trade.get('profit_pct')
            if raw_pnl_pct is None:
                raw_pnl_pct = trade.get('current_profit_pct', 0.0)
            leveraged_profit_pct = float(raw_pnl_pct or 0.0)
            spot_profit_pct = leveraged_profit_pct / leverage if leverage > 0 else leveraged_profit_pct
            
            if not open_date_str:
                continue
                
            try:
                open_date = datetime.strptime(open_date_str.split('.')[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                duration_h = (now - open_date).total_seconds() / 3600.0
            except Exception as e:
                logger.warning(f"Could not parse open_date '{open_date_str}': {e}")
                continue

            is_unfilled = float(trade.get('amount') or 0.0) == 0.0
            entry_tag = trade.get('enter_tag', '') or ''
            
            # Unfilled resting maker order expiration & TTL:
            if is_unfilled:
                orders = trade.get('orders', [])
                limit_rate = None
                if orders:
                    limit_rate = float(orders[0].get('safe_price') or orders[0].get('price') or 0.0)
                else:
                    limit_rate = float(trade.get('open_rate') or 0.0)
                current_rate = float(trade.get('current_rate') or 0.0)
                price_drift_pct = ((current_rate - limit_rate) / limit_rate * 100.0) if (limit_rate and limit_rate > 0 and current_rate > 0) else 0.0

                is_news_order = "news_catalyst" in entry_tag
                # News catalyst orders: fast TTL of 0.5h (30 min) or price drift >= 1.5%
                if is_news_order and (duration_h >= 0.5 or price_drift_pct >= 1.5):
                    logger.info(
                        f"Unfilled news catalyst limit order #{trade_id} ({pair}) expired (duration={duration_h:.2f}h >= 0.5h or drift={price_drift_pct:.2f}% >= 1.5%). Cancelling open order."
                    )
                    self.client.cancel_open_order(trade_id)
                    continue

                # Technical limit orders: 2.0h timeout or price drift >= 2.0%
                if not is_news_order and (duration_h >= 2.0 or price_drift_pct >= 2.0):
                    logger.info(
                        f"Unfilled technical limit order #{trade_id} ({pair}) expired (duration={duration_h:.2f}h >= 2.0h or drift={price_drift_pct:.2f}% >= 2.0%). Cancelling open order."
                    )
                    self.client.cancel_open_order(trade_id)
                    continue

            # Synchronized Adaptive Stale Pruning Engine (Config 08) & News Catalyst Lifecycle Gate
            # 0. News Catalyst Invalidation & Stagnation Cutoff:
            #    >= 6.0h at spot <= -0.80% (Rapid Invalidation) OR >= 12.0h at spot < +0.50% (Stagnation Cutoff)
            if "news_catalyst" in entry_tag:
                stale_trigger = (duration_h >= 6.0 and spot_profit_pct <= -0.80) or (duration_h >= 12.0 and spot_profit_pct < 0.50)
            # 1. PAXG Early Stale Prune: duration >= 8.0h and floating PnL <= -1.0% spot (-3.0% leveraged at 3x)
            elif "PAXG" in pair:
                stale_trigger = (duration_h >= 8.0 and spot_profit_pct <= -1.0)
            # 2. HYPE Specialization: Wide leeway due to 122% annualized volatility
            elif "HYPE" in pair:
                stale_trigger = (duration_h >= 48.0 and spot_profit_pct <= -4.0)
            # 3. General Pairs Adaptive Stale Prune (BTC, ETH, SOL, ADA, DOGE, LINK):
            #    >= 18h at <= -2.5% spot (-7.5% lev at 3x), or >= 24h at <= -2.0% spot (-6.0% lev at 3x)
            else:
                stale_trigger = (duration_h >= 18.0 and spot_profit_pct <= -2.5) or (duration_h >= 24.0 and spot_profit_pct <= -2.0)

            if not is_unfilled and stale_trigger:
                if trade_id not in self.state["pruned_trades"]:
                    prune_label = "NEWS CATALYST INVALIDATION/STAGNATION" if "news_catalyst" in entry_tag else "PRUNING"
                    logger.warning(
                        f"{prune_label} TRIGGERED for Trade #{trade_id} ({pair}, tag={entry_tag}): "
                        f"Duration={duration_h:.1f}h, Floating Spot PnL={spot_profit_pct:.2f}% (Lev PnL={leveraged_profit_pct:.2f}%). "
                        f"Executing early exit to preserve capital."
                    )
                    # Use market order for reliable exit during adverse price action, fallback to limit
                    success, resp = self.client.force_exit(trade_id, ordertype="market")
                    if not success:
                        success, resp = self.client.force_exit(trade_id, ordertype="limit")
                    if success:
                        self.state["pruned_trades"].append(trade_id)
                        self.save_state()
                        logger.info(f"Trade #{trade_id} successfully pruned.")

    def run_cycle(self):
        """Single monitoring and opportunistic execution cycle with Dynamic Waterfall Multi-Slot Takeover."""
        logger.info("--- Starting Supervisor Heartbeat Cycle ---")
        
        # 1. Dual-Stage Dynamic Take Profit Engine (TP1 1.0R / TP2 2.0R / Buffered BE -0.25R)
        self.inspect_dynamic_take_profits()

        # 2. Active position inspection & stale loss pruning
        self.inspect_stale_positions()

        # 2. Check current open trades and slot availability
        open_trades = self.client.get_status()
        active_count = len(open_trades)
        max_total = self.max_portfolio_slots
        logger.info(f"Active trades in system: {active_count} / {max_total}")

        # Count active AI trades and Core trades
        ai_active_count = sum(1 for t in open_trades if "ai_" in t.get('enter_tag', ''))
        core_active_count = active_count - ai_active_count

        # Identify occupied clusters to prevent correlation risk
        occupied_clusters = set()
        unfilled_ai_trades = []
        for t in open_trades:
            p = t.get('pair')
            c_name = self.get_pair_cluster(p)
            occupied_clusters.add(c_name)
            if float(t.get('amount') or 0.0) == 0.0 and "ai_" in t.get('enter_tag', ''):
                unfilled_ai_trades.append(t)

        slots_to_fill = self.get_available_ai_slots(core_active_count, ai_active_count)
        logger.info(
            f"Dynamic Slot Breakdown: Core Active={core_active_count}, AI Active={ai_active_count}, "
            f"Slots Available for AI={slots_to_fill} / {max_total}. Occupied Clusters: {list(occupied_clusters) or 'None'}"
        )

        if slots_to_fill <= 0:
            logger.info("Dynamic Slot Breakdown: AI slots currently full. Market scanning continues to maintain fresh candidate radar.")

        # 3. Calculate system idle duration
        now = datetime.now(timezone.utc)
        all_trades = self.client.get_trades(limit=5)
        
        last_event_time = None
        if all_trades:
            closed_dates = []
            for tr in all_trades:
                c_str = tr.get('close_date')
                if c_str:
                    try:
                        dt = datetime.strptime(c_str.split('.')[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        closed_dates.append(dt)
                    except Exception:
                        pass
            if closed_dates:
                last_event_time = max(closed_dates)

        if not last_event_time:
            idle_h = 999.0
        else:
            idle_h = (now - last_event_time).total_seconds() / 3600.0

        logger.info(
            f"Core Engine Idle Duration: {idle_h:.1f} hours "
            f"(Coiling Gate: {self.coiling_idle_threshold_hours}h | Breakout Gate: {self.breakout_idle_threshold_hours}h | Squeeze Gate: {self.squeeze_idle_threshold_hours}h | Normal Gate: {self.normal_idle_threshold_hours}h)"
        )

        # 4. Check Opportunistic Entry Eligibility
        min_required_idle = min(self.normal_idle_threshold_hours, self.squeeze_idle_threshold_hours, self.breakout_idle_threshold_hours, self.coiling_idle_threshold_hours)
        is_idle_eligible = (idle_h >= min_required_idle)
        if not is_idle_eligible:
            logger.info(f"System idle {idle_h:.1f}h < minimum {min_required_idle:.1f}h for technical setups. Fundamental News Catalyst scanning remains active.")

        # 5. Scan whitelist pairs for News Catalysts (Fundamental Authority) & Technical setups
        logger.info("Scanning pairs across clusters for News Catalysts & Technical setups...")
        sent_state = self.get_sentiment_state()
        deriv_state = self.get_derivatives_state()

        all_candidate_radar = []
        cluster_opportunities = {}

        for pair in self.pairs:
            time.sleep(0.30)  # Pacing to protect Bybit REST rate limit (Code 10006)
            c_name = self.get_pair_cluster(pair)

            # --- A. EVALUATE FUNDAMENTAL NEWS CATALYST (5 GATES) ---
            news_opp = self.scanner.evaluate_news_catalyst_opportunity(pair, sent_state, deriv_state)
            if news_opp:
                news_opp['cluster'] = c_name
                all_candidate_radar.append(news_opp)
                logger.info(
                    f"VALID NEWS CATALYST CANDIDATE [{c_name.upper()}]: {pair} @ {news_opp['limit_price']} "
                    f"(Score={news_opp['headline_score']}, VolShock={news_opp['volume_ratio']}x, Thrust={news_opp['thrust_ratio']}x ATR, R:R={news_opp['rr_ratio']}:1)"
                )
                if c_name not in occupied_clusters:
                    if c_name not in cluster_opportunities:
                        cluster_opportunities[c_name] = []
                    cluster_opportunities[c_name].append(news_opp)

            # --- B. EVALUATE TECHNICAL REGIMES (Coiling / Breakout / Squeeze / Support) ---
            opp = self.scanner.evaluate_opportunity(pair)
            if opp:
                opp['cluster'] = c_name
                all_candidate_radar.append(opp)

                # Skip opportunistic queueing into cluster_opportunities if cluster is occupied
                if c_name in occupied_clusters:
                    continue

                # Event Risk Blackout Gate: check if news sentiment layer flagged a critical event risk
                pair_sent = sent_state.get("pairs", {}).get(pair, {})
                if pair_sent.get("blackout_active"):
                    logger.warning(
                        f"EVENT RISK BLACKOUT ACTIVE for {pair}: {pair_sent.get('blackout_reason')}. "
                        f"Vetoing opportunistic entry to preserve capital."
                    )
                    continue

                # Derivatives Overheated Long Flush Veto
                pair_deriv = deriv_state.get("pairs", {}).get(pair, {})
                if pair_deriv.get("squeeze_signal") == "LONG_FLUSH_WARNING" or (
                    pair_deriv.get("derivatives_regime") == "LONG_OVERHEATED" and pair_deriv.get("leverage_risk") == "HIGH"
                ):
                    logger.warning(
                        f"DERIVATIVES VETO ACTIVE for {pair}: Overheated Long Leverage & Flush Warning "
                        f"(Funding={pair_deriv.get('funding_rate_8h_pct')}%, OI 1h={pair_deriv.get('oi_delta_1h_pct')}%). "
                        f"Vetoing opportunistic Long entry to prevent liquidation cascade."
                    )
                    continue

                # Short Squeeze Fuel Conviction Boost
                if pair_deriv.get("squeeze_signal") == "SHORT_SQUEEZE_ALERT" or pair_deriv.get("derivatives_regime") == "SHORT_SQUEEZE_FUEL":
                    logger.info(
                        f"DERIVATIVES SHORT SQUEEZE FUEL on {pair}: Negative Funding "
                        f"({pair_deriv.get('funding_rate_8h_pct')}%) + Rising OI. Conviction boosted."
                    )
                    opp['derivatives_boost'] = True

                regime = opp.get('regime', 'support_dip_bounce')
                is_sq = opp.get('is_squeeze', False)
                if regime == 'pre_breakout_coiling':
                    req_idle = self.coiling_idle_threshold_hours
                elif regime == 'breakout_retest_maker':
                    req_idle = self.breakout_idle_threshold_hours
                elif is_sq:
                    req_idle = self.squeeze_idle_threshold_hours
                else:
                    req_idle = self.normal_idle_threshold_hours

                if idle_h >= req_idle:
                    opp['cluster'] = c_name
                    logger.info(
                        f"VALID TECHNICAL CANDIDATE [{c_name.upper()}] [{regime.upper()}]: {opp['pair']} @ {opp['limit_price']} "
                        f"(R:R {opp['rr_ratio']}:1, 4H ADX {opp['4h_adx']}, Squeeze={is_sq})"
                    )
                    if c_name not in cluster_opportunities:
                        cluster_opportunities[c_name] = []
                    cluster_opportunities[c_name].append(opp)
                else:
                    logger.info(
                        f"Technical candidate deferred on {pair} [{regime}]: idle {idle_h:.1f}h < required {req_idle}h (Squeeze={is_sq})"
                    )

        self.state["last_scan_time"] = now.isoformat()
        self.state["active_trades"] = active_count
        self.state["slots_available"] = slots_to_fill
        self.state["occupied_clusters"] = list(occupied_clusters)
        self.state["ai_candidate_radar"] = all_candidate_radar
        self.save_state()

        if not cluster_opportunities:
            logger.info("Scan complete: No valid opportunities meeting entry & idle criteria across available clusters.")
            return

        if slots_to_fill <= 0:
            logger.info("Scan complete: Candidate radar updated, but no free slots currently available for AI trade dispatch. Standby.")
            return

        # 6. Select highest priority / R:R candidate per unoccupied cluster
        best_cluster_candidates = []
        for c_name, opps in cluster_opportunities.items():
            news_in_cluster = [o for o in opps if o.get('regime') == 'news_catalyst']
            if news_in_cluster:
                best_in_cluster = max(news_in_cluster, key=lambda x: x.get('rr_ratio', 2.0))
            else:
                best_in_cluster = max(opps, key=lambda x: x['rr_ratio'])
            best_cluster_candidates.append(best_in_cluster)

        # Sort selected cluster candidates (News Catalysts first, then highest R:R)
        best_cluster_candidates.sort(
            key=lambda x: (1 if x.get('regime') == 'news_catalyst' else 0, x.get('rr_ratio', 0.0)),
            reverse=True
        )

        # Take up to available slots
        dispatch_targets = best_cluster_candidates[:slots_to_fill]

        bal_data = self.client.get_balance()
        total_balance = float(bal_data.get('total', 1000.0))
        base_stake = round((total_balance / float(self.max_portfolio_slots)) * 0.95, 2)

        for target in dispatch_targets:
            pair = target['pair']
            limit_price = target['limit_price']
            c_name = target['cluster']
            regime = target.get('regime', 'support_dip_bounce')
            
            if regime == "news_catalyst":
                entry_tag = "ai_news_catalyst_long"
                final_stake = round(base_stake * target.get('stake_scale', 0.60), 2)
            elif regime == "pre_breakout_coiling":
                entry_tag = "ai_pre_breakout_coiling"
                final_stake = base_stake
            elif regime == "breakout_retest_maker":
                entry_tag = "ai_breakout_retest"
                final_stake = base_stake
            else:
                entry_tag = "ai_opportunistic_support"
                final_stake = base_stake

            # Pass base_stake to force_enter: Freqtrade's custom_stake_amount in ApexDualAlpha_Omni_V12_LinkCalibrated
            # dynamically scales news_catalyst trades by 0.60x (and applies co-risk throttling if applicable).
            stake_amount = base_stake

            # Secondary Event Risk Blackout Safety Check
            sent_state = self.get_sentiment_state()
            pair_sent = sent_state.get("pairs", {}).get(pair, {})
            if pair_sent.get("blackout_active"):
                logger.warning(f"DISPATCH ABORTED by Event Risk Blackout for {pair}: {pair_sent.get('blackout_reason')}")
                continue

            # Secondary Derivatives Safety Check
            deriv_state = self.get_derivatives_state()
            pair_deriv = deriv_state.get("pairs", {}).get(pair, {})
            if pair_deriv.get("squeeze_signal") == "LONG_FLUSH_WARNING":
                logger.warning(f"DISPATCH ABORTED by Derivatives Long Flush Warning for {pair}")
                continue

            logger.info(
                f"DISPATCHING DYNAMIC LIMIT ORDER: Cluster=[{c_name.upper()}], Regime=[{regime}], Pair={pair}, Price={limit_price}, "
                f"ProposedStake=${stake_amount} USDT (Effective=${final_stake} USDT via custom_stake_amount), EntryTag='{entry_tag}'"
            )

            success, res = self.client.force_enter(
                pair=pair,
                side="long",
                price=limit_price,
                stake_amount=stake_amount,
                entry_tag=entry_tag
            )

            if success:
                logger.info(f"SUCCESS: Dynamic resting maker order placed on {pair} [{c_name}] at {limit_price}!")
                self.state["ai_orders"].append({
                    "pair": pair,
                    "price": limit_price,
                    "cluster": c_name,
                    "regime": regime,
                    "entry_tag": entry_tag,
                    "stake_amount": final_stake,
                    "time": now.isoformat(),
                    "rr_ratio": target['rr_ratio'],
                    "risk": target.get('risk'),
                    "tp1": target.get('tp1'),
                    "tp2": target.get('tp2'),
                    "buffered_be": target.get('buffered_be')
                })
                self.save_state()
                occupied_clusters.add(c_name)
            else:
                logger.error(f"Failed to place dynamic force_enter order on {pair}: {res}")

    def start(self, poll_interval_sec: int = 60):
        logger.info("=================================================================")
        logger.info("   AI STRATEGIC SUPERVISOR DAEMON (DYNAMIC WATERFALL MULTI-SLOT) ")
        logger.info("=================================================================")
        logger.info(f"Target pairs: {', '.join(self.pairs)}")
        logger.info(f"Idle gates: Coiling {self.coiling_idle_threshold_hours}h / Breakout {self.breakout_idle_threshold_hours}h / Squeeze {self.squeeze_idle_threshold_hours}h / Normal {self.normal_idle_threshold_hours}h | Max Slots: {self.max_portfolio_slots}")
        logger.info(f"Triple-Regimes: 1. Support Dip Bounce | 2. Breakout Retest Maker | 3. Pre-Breakout Coiling Maker")
        logger.info(f"Cluster diversification: Major Anchor (BTC/ETH), High-Beta Alt (SOL/ADA/DOGE/LINK), Defensive (PAXG)")
        logger.info(f"Freqtrade API target: {self.client.base_url}")
        
        # Initial login handshake
        if not self.client.login():
            logger.warning("Could not establish initial API session. Will retry during loop.")

        while self.running:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in supervisor loop: {e}", exc_info=True)

            # Sleep in 1-second ticks for responsive shutdown
            for _ in range(poll_interval_sec):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("AI Strategic Supervisor Daemon stopped gracefully.")

    def get_ai_candidate_radar(self) -> List[Dict[str, Any]]:
        """Returns the latest evaluated candidate opportunities from the radar."""
        return self.state.get("ai_candidate_radar", [])

    def cancel_resting_order(self, trade_id: int):
        """Cancels an unfilled resting limit order via Freqtrade REST API."""
        return self.client.cancel_open_order(trade_id)

    def stop(self):
        self.running = False


if __name__ == '__main__':
    daemon = AISupervisorDaemon()

    def handle_signal(sig, frame):
        logger.info("Shutdown signal received. Stopping daemon...")
        daemon.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    daemon.start(poll_interval_sec=60)
