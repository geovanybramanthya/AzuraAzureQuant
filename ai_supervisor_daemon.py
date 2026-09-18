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

import requests
import pandas as pd
import numpy as np
import talib.abstract as ta
import ccxt

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [AI-SUPERVISOR] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ai_supervisor.log", mode="a", encoding="utf-8")
    ]
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
                self.token_expiry = time.time() + 3600  # Default 1 hour validity
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

    def ping(self) -> bool:
        try:
            res = requests.get(f"{self.base_url}/api/v1/ping", timeout=5)
            return res.status_code == 200 and res.json().get("status") == "pong"
        except Exception:
            return False

    def get_status(self):
        try:
            res = requests.get(f"{self.base_url}/api/v1/status", headers=self._get_headers(), timeout=10)
            if res.status_code == 200:
                return res.json()
            return []
        except Exception as e:
            logger.error(f"Failed to fetch status: {e}")
            return []

    def get_trades(self, limit=50):
        try:
            res = requests.get(f"{self.base_url}/api/v1/trades?limit={limit}", headers=self._get_headers(), timeout=10)
            if res.status_code == 200:
                return res.json().get("trades", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def get_balance(self):
        try:
            res = requests.get(f"{self.base_url}/api/v1/balance", headers=self._get_headers(), timeout=10)
            if res.status_code == 200:
                return res.json()
            return {}
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {}

    def force_enter(self, pair: str, side: str, price: float, stake_amount: float, entry_tag: str):
        url = f"{self.base_url}/api/v1/forceenter"
        payload = {
            "pair": pair,
            "side": side,
            "price": price,
            "ordertype": "limit",
            "stakeamount": stake_amount,
            "entry_tag": entry_tag
        }
        try:
            res = requests.post(url, headers=self._get_headers(), json=payload, timeout=10)
            logger.info(f"force_enter response ({res.status_code}): {res.text}")
            return res.status_code == 200, res.json() if res.status_code == 200 else res.text
        except Exception as e:
            logger.error(f"Failed to force enter trade: {e}")
            return False, str(e)

    def force_exit(self, trade_id: int, ordertype: str = "limit"):
        url = f"{self.base_url}/api/v1/forceexit"
        payload = {
            "tradeid": str(trade_id),
            "ordertype": ordertype
        }
        try:
            res = requests.post(url, headers=self._get_headers(), json=payload, timeout=10)
            logger.info(f"force_exit trade #{trade_id} response ({res.status_code}): {res.text}")
            return res.status_code == 200, res.json() if res.status_code == 200 else res.text
        except Exception as e:
            logger.error(f"Failed to force exit trade #{trade_id}: {e}")
            return False, str(e)

    def cancel_open_order(self, trade_id: int):
        url = f"{self.base_url}/api/v1/trades/{trade_id}/open-order"
        try:
            res = requests.delete(url, headers=self._get_headers(), timeout=10)
            # In Freqtrade dry-run, canceling an unfilled trade purges it from SQLite and may return 502 'no active trade'
            if res.status_code == 200 or (res.status_code == 502 and "no active trade" in res.text):
                logger.info(f"Open order for trade #{trade_id} successfully cancelled and purged from SQLite.")
                return True, "Cancelled"
            logger.warning(f"cancel_open_order returned status {res.status_code}: {res.text}")
            return False, res.text
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
        # REGIME 1: Structural Support Dip Bounce (Range-Bound / Low ADX)
        # =========================================================================
        if not pd.isna(latest_4h_adx) and latest_4h_adx < 25.0:
            range_pct = (prev_resistance - prev_support) / prev_support
            if range_pct >= 0.035:
                is_green = candle['close'] > candle['open']
                body_size = abs(candle['close'] - candle['open'])
                lower_wick = (candle['open'] - candle['low']) if is_green else (candle['close'] - candle['low'])

                wick_absorbed = lower_wick > body_size * 0.75
                touched_support = candle['low'] <= prev_support * 1.003
                bounced_above = candle['close'] > prev_support
                rsi_valid = 32.0 <= candle['rsi'] <= 46.0
                vol_valid = candle['volume'] > candle['vol_mean_20'] * 0.75
                not_broken_down = current_price > prev_support * 0.998

                if touched_support and bounced_above and wick_absorbed and rsi_valid and vol_valid and not_broken_down:
                    limit_price = round(candle['low'] + 0.20 * lower_wick, dec)
                    limit_price = min(limit_price, round(current_price * 0.9995, dec))
                    stop_loss = round(prev_support * 0.992, dec)
                    take_profit = round(prev_support + 0.60 * (prev_resistance - prev_support), dec)

                    risk = limit_price - stop_loss
                    if risk <= 0:
                        risk = round(limit_price * 0.008, dec)
                        stop_loss = round(limit_price - risk, dec)

                    min_take_profit = round(limit_price + 1.80 * risk, dec)
                    if take_profit < min_take_profit:
                        take_profit = min_take_profit

                    reward = take_profit - limit_price
                    rr_ratio = round(reward / risk, 2) if risk > 0 else 1.80

                    if rr_ratio >= 1.80:
                        return {
                            'pair': pair,
                            'regime': 'support_dip_bounce',
                            'limit_price': limit_price,
                            'current_price': current_price,
                            'support_floor': round(prev_support, dec),
                            'support_floor_48h': round(prev_support, dec),
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'target_tp': take_profit,
                            'rr_ratio': round(rr_ratio, 2),
                            '4h_adx': round(latest_4h_adx, 1),
                            'rsi': round(candle['rsi'], 1),
                            'is_squeeze': is_squeeze,
                            'timestamp': candle['date']
                        }

        # =========================================================================
        # REGIME 2: Breakout-Retest S/R Flip Maker Engine (Momentum Acceleration)
        # =========================================================================
        if macro_bull_4h and not pd.isna(latest_4h_adx) and latest_4h_adx >= 19.0:
            broke_out = candle['close'] >= prev_resistance * 0.998 or candle['high'] >= prev_resistance
            vol_expansion = candle['volume'] > candle['vol_mean_20'] * 1.05
            rsi_momentum = 48.0 <= candle['rsi'] <= 89.0
            ema_stack = df_1h['ema_9'].iloc[-2] > df_1h['ema_21'].iloc[-2]

            if broke_out and vol_expansion and rsi_momentum and ema_stack:
                ema9_val = float(df_1h['ema_9'].iloc[-2])
                retest_level = max(prev_resistance, ema9_val)
                # Ensure strictly maker limit order placed below current market price
                limit_price = round(min(retest_level, current_price * 0.9985), dec)
                stop_loss = round(min(limit_price * 0.986, prev_resistance * 0.988), dec)

                risk = limit_price - stop_loss
                if risk <= 0:
                    risk = round(limit_price * 0.012, dec)
                    stop_loss = round(limit_price - risk, dec)

                take_profit = round(limit_price + 2.0 * risk, dec)
                reward = take_profit - limit_price
                rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0

                return {
                    'pair': pair,
                    'regime': 'breakout_retest_maker',
                    'limit_price': limit_price,
                    'current_price': current_price,
                    'support_floor': round(retest_level, dec),
                    'support_floor_48h': round(prev_resistance, dec),
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'target_tp': take_profit,
                    'rr_ratio': round(rr_ratio, 2),
                    '4h_adx': round(latest_4h_adx, 1),
                    'rsi': round(candle['rsi'], 1),
                    'is_squeeze': False,
                    'timestamp': candle['date']
                }

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
            rsi_acc = 46.0 <= candle['rsi'] <= 68.0
            squeeze_ok = is_squeeze or (not pd.isna(latest_4h_adx) and latest_4h_adx < 28.0)

            macro_ok = macro_bull_4h
            if is_upper_range and coiling_near_res and rising_floor and ema_hold and rsi_acc and squeeze_ok and macro_ok:
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

                if rr_ratio >= 1.80:
                    return {
                        'pair': pair,
                        'regime': 'pre_breakout_coiling',
                        'limit_price': limit_price,
                        'current_price': current_price,
                        'support_floor': round(local_floor_12, dec),
                        'support_floor_48h': round(prev_support, dec),
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'target_tp': take_profit,
                        'rr_ratio': round(rr_ratio, 2),
                        '4h_adx': round(latest_4h_adx, 1),
                        'rsi': round(candle['rsi'], 1),
                        'is_squeeze': is_squeeze,
                        'timestamp': candle['date']
                    }

        return None


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
        self.pairs = self.config.get("exchange", {}).get("pair_whitelist", [])
        self.scanner = MarketScanner(self.pairs)

        self.normal_idle_threshold_hours = 18.0
        self.squeeze_idle_threshold_hours = 12.0
        self.breakout_idle_threshold_hours = 6.0
        self.coiling_idle_threshold_hours = 12.0
        self.max_portfolio_slots = int(self.config.get('max_open_trades', 3))
        
        # Cluster Diversification Guard (Max 1 position per cluster)
        self.clusters = {
            'major': ['BTC/USDT:USDT', 'ETH/USDT:USDT'],
            'alt': ['SOL/USDT:USDT', 'ADA/USDT:USDT', 'DOGE/USDT:USDT', 'LINK/USDT:USDT'],
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
        """Dynamic Waterfall Slot Allocation:
        When Quant Core has 0 trades, AI can take up to all 3 free slots.
        Core always has preemptive priority; total active cannot exceed max_portfolio_slots.
        """
        free_slots = max(0, self.max_portfolio_slots - (core_active_count + ai_active_count))
        max_ai_allowed = max(0, self.max_portfolio_slots - core_active_count)
        return min(free_slots, max(0, max_ai_allowed - ai_active_count))

    def load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_scan_time": None, "ai_orders": [], "pruned_trades": []}

    def save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def inspect_stale_positions(self):
        """Active Early Pruning: cut underwater positions at 18-24h instead of waiting for 36h stale stop."""
        open_trades = self.client.get_status()
        now = datetime.now(timezone.utc)
        
        for trade in open_trades:
            trade_id = trade.get('trade_id')
            pair = trade.get('pair')
            open_date_str = trade.get('open_date')
            current_profit_pct = trade.get('current_profit_pct', 0.0) # In %
            
            if not open_date_str:
                continue
                
            try:
                open_date = datetime.strptime(open_date_str.split('.')[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                duration_h = (now - open_date).total_seconds() / 3600.0
            except Exception as e:
                logger.warning(f"Could not parse open_date '{open_date_str}': {e}")
                continue

            is_unfilled = float(trade.get('amount') or 0.0) == 0.0
            
            # If resting limit order is unfilled after 18h: cancel open order to free up slot
            if is_unfilled and duration_h >= 18.0:
                logger.info(f"Unfilled limit order #{trade_id} ({pair}) expired after {duration_h:.1f}h. Cancelling open order.")
                self.client.cancel_open_order(trade_id)
                continue

            # Stale pruning rule: between 18h and 24h, if underwater <= -1.5% spot on FILLED positions
            if not is_unfilled and 18.0 <= duration_h <= 24.0 and current_profit_pct <= -1.5:
                if trade_id not in self.state["pruned_trades"]:
                    logger.warning(
                        f"PRUNING TRIGGERED for Trade #{trade_id} ({pair}): "
                        f"Duration={duration_h:.1f}h, Floating PnL={current_profit_pct:.2f}%. "
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
        
        # 1. Active position inspection & stale loss pruning
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
            logger.info("No free slots currently available for AI allocation. Standby.")
            return

        # 3. Calculate system idle duration
        now = datetime.now(timezone.utc)
        all_trades = self.client.get_trades(limit=5)
        
        last_event_time = None
        if core_active_count >= 2:
            logger.info("Core engine has 2+ slots occupied. Waiting for core exits to protect portfolio margin.")
            return
        elif all_trades:
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
        if idle_h < min_required_idle:
            logger.info(f"System not yet eligible for opportunistic maker entry (idle {idle_h:.1f}h < minimum {min_required_idle:.1f}h).")
            return

        # 5. Scan whitelist pairs for Triple-Regime setups (Pre-Breakout Coiling 4h / Breakout-Retest 6h / Squeeze 12h / Support 18h)
        logger.info("Scanning pairs across clusters for Triple-Regime setups (Pre-Breakout 4h / Breakout-Retest 6h / Squeeze 12h / Support 18h)...")
        cluster_opportunities = {}
        for pair in self.pairs:
            time.sleep(0.30)  # Pacing to protect Bybit REST rate limit (Code 10006)
            c_name = self.get_pair_cluster(pair)
            # Skip pairs belonging to already occupied clusters
            if c_name in occupied_clusters:
                continue

            opp = self.scanner.evaluate_opportunity(pair)
            if opp:
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
                        f"VALID CANDIDATE [{c_name.upper()}] [{regime.upper()}]: {opp['pair']} @ {opp['limit_price']} "
                        f"(R:R {opp['rr_ratio']}:1, 4H ADX {opp['4h_adx']}, Squeeze={is_sq})"
                    )
                    if c_name not in cluster_opportunities:
                        cluster_opportunities[c_name] = []
                    cluster_opportunities[c_name].append(opp)
                else:
                    logger.info(
                        f"Candidate deferred on {pair} [{regime}]: idle {idle_h:.1f}h < required {req_idle}h (Squeeze={is_sq})"
                    )

        self.state["last_scan_time"] = now.isoformat()
        self.state["active_trades"] = active_count
        self.state["slots_available"] = slots_to_fill
        self.state["occupied_clusters"] = list(occupied_clusters)
        self.save_state()

        if not cluster_opportunities:
            logger.info("Scan complete: No valid opportunities meeting R:R >= 1.6 & idle criteria across available clusters.")
            return

        # 6. Select highest R:R candidate per unoccupied cluster
        best_cluster_candidates = []
        for c_name, opps in cluster_opportunities.items():
            best_in_cluster = max(opps, key=lambda x: x['rr_ratio'])
            best_cluster_candidates.append(best_in_cluster)

        # Sort selected cluster candidates by R:R descending
        best_cluster_candidates.sort(key=lambda x: x['rr_ratio'], reverse=True)

        # Take up to available slots
        dispatch_targets = best_cluster_candidates[:slots_to_fill]

        bal_data = self.client.get_balance()
        total_balance = float(bal_data.get('total', 1000.0))
        stake_amount = round((total_balance / float(self.max_portfolio_slots)) * 0.95, 2)

        for target in dispatch_targets:
            pair = target['pair']
            limit_price = target['limit_price']
            c_name = target['cluster']
            regime = target.get('regime', 'support_dip_bounce')
            if regime == "pre_breakout_coiling":
                entry_tag = "ai_pre_breakout_coiling"
            elif regime == "breakout_retest_maker":
                entry_tag = "ai_breakout_retest"
            else:
                entry_tag = "ai_opportunistic_support"

            logger.info(
                f"DISPATCHING DYNAMIC LIMIT ORDER: Cluster=[{c_name.upper()}], Regime=[{regime}], Pair={pair}, Price={limit_price}, "
                f"Stake=${stake_amount} USDT, EntryTag='{entry_tag}'"
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
                    "time": now.isoformat(),
                    "rr_ratio": target['rr_ratio']
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
