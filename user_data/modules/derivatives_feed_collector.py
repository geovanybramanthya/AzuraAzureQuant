"""
Derivatives Feed Collector & Open Interest Squeeze Engine
---------------------------------------------------------
Modul penarik dan penganalisis data pasar derivatif Bybit Linear Futures:
1. Mengambil data real-time Funding Rate, Next Funding Countdown, dan Open Interest (OI)
   untuk 8 pasangan whitelist dalam satu pemanggilan terpadu via Bybit v5 Tickers.
2. Melacak riwayat deret waktu (rolling time-series) untuk mendeteksi:
   - Funding Rate Velocity (perubahan kecepatan funding rate).
   - OI Delta & 1H/24H Expansion Ratio.
   - Basis Spread (selisih Mark Price vs Index Price).
   - Imbalance L1 Orderbook Bid/Ask Volume.
3. Mengklasifikasikan rezim pasar derivatif:
   - SHORT_SQUEEZE_FUEL: Funding negatif + lonjakan OI saat harga di lantai support.
   - LONG_FLUSH_WARNING: Funding positif ekstrem (>0.03%/8h) + saturasi leverage tinggi.
   - ABSORPTION_ACCUMULATION: OI melonjak tajam saat harga berkonsolidasi (pre-breakout).
   - EQUILIBRIUM: Kondisi leverage terdistribusi sehat.
4. Menyimpan status secara atomik ke 'user_data/data/derivatives_state.json'.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path
import ccxt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DERIVATIVES-COLLECTOR] %(message)s"
)
logger = logging.getLogger("DerivativesCollector")

WHITELIST_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "ADA/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "PAXG/USDT:USDT",
    "HYPE/USDT:USDT"
]

class DerivativesFeedCollector:
    def __init__(self, state_file_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.state_file = Path(state_file_path) if state_file_path else base_dir / "data" / "derivatives_state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.exchange = ccxt.bybit({
            'options': {'defaultType': 'linear'},
            'timeout': 10000,
            'enableRateLimit': True
        })

        # Rolling history per pair: {pair: [{'timestamp': float, 'oi_usd': float, 'price': float, 'funding': float}]}
        # Maksimal menyimpan 120 observasi per pair (setara 2 jam pada interval 60 detik)
        self.history: Dict[str, List[Dict[str, float]]] = {pair: [] for pair in WHITELIST_PAIRS}
        self.load_existing_state()

    def load_existing_state(self):
        """Memuat riwayat sebelumnya dari disk jika tersedia untuk kontinuitas."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    pairs_data = data.get("pairs", {})
                    for pair, p_info in pairs_data.items():
                        if pair in self.history and "recent_history" in p_info:
                            self.history[pair] = p_info["recent_history"][-120:]
                logger.info(f"Loaded existing history from {self.state_file}")
            except Exception as e:
                logger.warning(f"Could not load previous derivatives state: {e}")

    def fetch_all_tickers(self) -> Dict[str, Any]:
        """Mengambil data semua pairs dalam satu kali panggilan REST Bybit v5."""
        try:
            tickers = self.exchange.fetch_tickers(WHITELIST_PAIRS)
            return tickers
        except Exception as e:
            logger.error(f"Error fetching tickers from Bybit: {e}")
            return {}

    def analyze_pair(self, pair: str, ticker: Dict[str, Any], now_ts: float) -> Dict[str, Any]:
        raw = ticker.get('info', {})
        
        mark_price = float(ticker.get('close') or raw.get('markPrice', 0.0) or 0.0)
        index_price = float(raw.get('indexPrice', mark_price) or mark_price)
        basis_spread_usd = mark_price - index_price
        basis_pct = (basis_spread_usd / index_price * 100.0) if index_price > 0 else 0.0

        # Funding rate (Bybit mengembalikan desimal 8 jam, misal 0.0001 = 0.01%)
        funding_rate_raw = float(raw.get('fundingRate', 0.0) or 0.0)
        funding_rate_pct = funding_rate_raw * 100.0 # dalam persen per 8 jam
        funding_annualized_pct = funding_rate_pct * 3.0 * 365.0 # APR tahunan
        next_funding_time = raw.get('nextFundingTime', '')

        # Open Interest
        oi_contracts = float(raw.get('openInterest', 0.0) or 0.0)
        oi_usd = float(raw.get('openInterestValue', 0.0) or 0.0)
        if oi_usd <= 0 and oi_contracts > 0 and mark_price > 0:
            oi_usd = oi_contracts * mark_price

        # Turnover 24h & Volume
        turnover_24h_usd = float(raw.get('turnover24h', 0.0) or 0.0)
        price_24h_pct = float(raw.get('price24hPcnt', 0.0) or 0.0) * 100.0

        # Orderbook Level 1 Imbalance
        bid1_size = float(raw.get('bid1Size', 0.0) or 0.0)
        ask1_size = float(raw.get('ask1Size', 0.0) or 0.0)
        total_l1 = bid1_size + ask1_size
        l1_imbalance_pct = round(((bid1_size - ask1_size) / total_l1 * 100.0), 2) if total_l1 > 0 else 0.0

        # Update history
        pair_hist = self.history.setdefault(pair, [])
        pair_hist.append({
            'ts': now_ts,
            'price': mark_price,
            'oi_usd': oi_usd,
            'funding': funding_rate_raw
        })
        # Batasi ke 120 data poin
        if len(pair_hist) > 120:
            pair_hist.pop(0)

        # Hitung Delta OI 15m & 1h
        oi_delta_15m_pct = 0.0
        oi_delta_1h_pct = 0.0
        funding_velocity_1h = 0.0

        if len(pair_hist) >= 2:
            # Cari data sekitar 15 menit lalu (ts - 900)
            hist_15m = [h for h in pair_hist if h['ts'] <= now_ts - 800]
            if hist_15m:
                base_oi = hist_15m[-1]['oi_usd']
                if base_oi > 0:
                    oi_delta_15m_pct = round(((oi_usd - base_oi) / base_oi) * 100.0, 3)

            # Cari data sekitar 1 jam lalu (ts - 3600)
            hist_1h = [h for h in pair_hist if h['ts'] <= now_ts - 3300]
            if hist_1h:
                base_oi_1h = hist_1h[-1]['oi_usd']
                if base_oi_1h > 0:
                    oi_delta_1h_pct = round(((oi_usd - base_oi_1h) / base_oi_1h) * 100.0, 3)
                funding_velocity_1h = round((funding_rate_raw - hist_1h[-1]['funding']) * 100.0, 4)

        # Klasifikasi Rezim Derivatif & Squeeze Alert
        derivatives_regime = "EQUILIBRIUM"
        squeeze_signal = "NONE"
        leverage_risk = "LOW"

        # 1. Peringatan Long Flush (Overheated Long Leverage)
        # Funding rate > +0.035% per 8h (APR > 38%) atau lonjakan funding tajam
        if funding_rate_pct >= 0.035:
            derivatives_regime = "LONG_OVERHEATED"
            leverage_risk = "HIGH"
            if oi_delta_15m_pct >= 1.5 or oi_delta_1h_pct >= 3.0:
                squeeze_signal = "LONG_FLUSH_WARNING"

        # 2. Bahan Bakar Short Squeeze (Negative Funding + OI Expansion)
        elif funding_rate_pct <= -0.005:
            derivatives_regime = "SHORT_SQUEEZE_FUEL"
            leverage_risk = "MEDIUM"
            if oi_delta_15m_pct >= 1.0 or oi_delta_1h_pct >= 2.0:
                squeeze_signal = "SHORT_SQUEEZE_ALERT"

        # 3. Akumulasi Penyerapan (Pre-Breakout Absorption)
        elif abs(funding_rate_pct) < 0.015 and (oi_delta_15m_pct >= 2.0 or oi_delta_1h_pct >= 4.0):
            derivatives_regime = "ABSORPTION_ACCUMULATION"
            squeeze_signal = "BREAKOUT_BUILDUP"
            leverage_risk = "MEDIUM"

        return {
            "pair": pair,
            "mark_price": round(mark_price, 4 if mark_price < 10 else 2),
            "index_price": round(index_price, 4 if index_price < 10 else 2),
            "basis_pct": round(basis_pct, 4),
            "funding_rate_8h_pct": round(funding_rate_pct, 5),
            "funding_rate_apr_pct": round(funding_annualized_pct, 2),
            "funding_velocity_1h": funding_velocity_1h,
            "next_funding_time": next_funding_time,
            "open_interest_usd": round(oi_usd, 2),
            "turnover_24h_usd": round(turnover_24h_usd, 2),
            "oi_delta_15m_pct": oi_delta_15m_pct,
            "oi_delta_1h_pct": oi_delta_1h_pct,
            "l1_imbalance_pct": l1_imbalance_pct,
            "derivatives_regime": derivatives_regime,
            "squeeze_signal": squeeze_signal,
            "leverage_risk": leverage_risk,
            "recent_history": pair_hist[-60:]
        }

    def aggregate_market_derivatives(self, pairs_state: Dict[str, Any]) -> Dict[str, Any]:
        total_oi_usd = sum(p["open_interest_usd"] for p in pairs_state.values())
        total_turnover_24h = sum(p["turnover_24h_usd"] for p in pairs_state.values())

        # Rata-rata funding rate berbobot modal (capital-weighted funding)
        if total_oi_usd > 0:
            weighted_funding = sum(
                p["funding_rate_8h_pct"] * (p["open_interest_usd"] / total_oi_usd)
                for p in pairs_state.values()
            )
        else:
            weighted_funding = 0.0

        squeeze_alerts = [
            {"pair": p["pair"], "signal": p["squeeze_signal"], "regime": p["derivatives_regime"]}
            for p in pairs_state.values()
            if p["squeeze_signal"] != "NONE"
        ]

        if weighted_funding > 0.025:
            macro_leverage_state = "LEVERAGE_LONG_HEAVY"
        elif weighted_funding < -0.005:
            macro_leverage_state = "LEVERAGE_SHORT_HEAVY"
        else:
            macro_leverage_state = "LEVERAGE_BALANCED"

        return {
            "total_open_interest_usd": round(total_oi_usd, 2),
            "total_turnover_24h_usd": round(total_turnover_24h, 2),
            "weighted_funding_rate_8h_pct": round(weighted_funding, 5),
            "weighted_funding_apr_pct": round(weighted_funding * 3.0 * 365.0, 2),
            "macro_leverage_state": macro_leverage_state,
            "active_squeeze_alerts": squeeze_alerts
        }

    def save_state(self, state_payload: Dict[str, Any]):
        """Menyimpan status derivatif secara atomik ke disk."""
        tmp_file = self.state_file.with_suffix('.tmp')
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_file, self.state_file)
            logger.info(
                f"Derivatives state updated ({self.state_file.name}). "
                f"Total OI: ${state_payload['macro_derivatives']['total_open_interest_usd']:,.2f} | "
                f"Weighted Funding: {state_payload['macro_derivatives']['weighted_funding_rate_8h_pct']:.4f}% "
                f"({state_payload['macro_derivatives']['macro_leverage_state']})"
            )
        except Exception as e:
            logger.error(f"Error saving derivatives state: {e}")
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def run_cycle(self) -> Dict[str, Any]:
        now_ts = time.time()
        tickers = self.fetch_all_tickers()
        if not tickers:
            logger.warning("Empty tickers fetched, skipping cycle.")
            return {}

        pairs_state: Dict[str, Any] = {}
        for pair in WHITELIST_PAIRS:
            ticker = tickers.get(pair)
            if ticker:
                pairs_state[pair] = self.analyze_pair(pair, ticker, now_ts)

        macro_data = self.aggregate_market_derivatives(pairs_state)

        payload = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "collector_status": "ONLINE",
            "macro_derivatives": macro_data,
            "pairs": pairs_state
        }

        self.save_state(payload)
        return payload

    def run_forever(self, interval_seconds: int = 60):
        logger.info(f"Starting DerivativesFeedCollector loop (interval: {interval_seconds}s)...")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in collector cycle: {e}", exc_info=True)
            time.sleep(interval_seconds)

if __name__ == "__main__":
    collector = DerivativesFeedCollector()
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        collector.run_cycle()
    else:
        collector.run_forever(interval_seconds=60)
