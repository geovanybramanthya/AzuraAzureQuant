# AzuraAzureQuant

> **High-Frequency & Quantitative Dual-Alpha Trading System on Bybit Linear Futures**

AzuraAzureQuant is a quantitative algorithmic trading architecture engineered on top of the Freqtrade framework, specifically tailored for Bybit Linear Futures. It implements a dual-alpha execution engine, asymmetric leverage management, and real-time portfolio analytics.

---

## Architectural Pillars

### 1. Dual-Alpha Market Formulation
- **Long Pullback Alpha**: Enters on high-probability retracements within confirmed higher-timeframe trends, utilizing dynamic ATR and exponential moving average envelopes.
- **Short Resistance Exhaustion Alpha**: Captures mean-reverting liquidity sweeps and volume depletion near local market peaks, hedging systemic drawdowns.

### 2. Strategy Engine Evolution (Apex Family)
- `ApexDualAlpha_Omni_V11_Ultimate`: The flagship production engine featuring adaptive volatility thresholds, asymmetric stop-loss protection, and dynamic take-profit ladders.
- `ApexTriAlpha_Futures_Strategy`: Multi-regime statistical arbitrage capturing breakout, trend, and consolidation regimes.
- `ApexDualAlpha_M15_Institutional`: 15-minute timeframe execution calibrated for liquid pairs (BTC, ETH, SOL).

### 3. Quantitative Risk & Capital Preservation
- **Asymmetric Leverage Sizing**: Tiered position allocation designed to prevent catastrophic liquidations during high-volatility flash crashes.
- **Strict Database Cleanliness**: Automated archiving of legacy simulated states (`archive_legacy_databases/`) ensuring backtest and dry-run integrity without historical pollution.
- **Circuit Breakers**: Trailing drawdown monitors and dynamic portfolio exposure ceilings.

### 4. Live Command Center & Telemetry
- **Dashboard Server (`dashboard_server.py`)**: Lightweight web service exposing portfolio performance, open positions, and equity curves on port 5050.
- **React/Vite Dashboard (`dashboard/`)**: Modern real-time telemetry frontend.
- **QuantStats Tearsheet Generator (`generate_tearsheet.py`)**: Automated generation of Sharpe, Sortino, Calmar, and underwater drawdown tear sheets.

---

## Repository Structure

```text
AzuraAzureQuant/
├── dashboard/                     # React + Vite frontend telemetry dashboard
├── user_data/
│   ├── strategies/                # Core proprietary quant strategies (ApexDualAlpha)
│   ├── config_futures.json        # Sanitized Bybit Futures configuration template
│   └── quantstats_tearsheet.html  # Quantitative tearsheet benchmark
├── analyze_trades.py              # Trade distribution and PnL breakdown
├── compare_strategies.py          # Strategy performance benchmarking utility
├── dashboard_server.py            # Local telemetry backend server (Port 5050)
├── generate_tearsheet.py          # QuantStats HTML report generator
├── simulate_candidate_engines.py  # Multi-engine Monte Carlo simulation
├── START_LIVE_SESSION.bat         # Automated orchestration launcher
└── .gitignore                     # Airtight credential and binary filter
```

---

## Getting Started

### 1. Prerequisites
- Python 3.11+
- Freqtrade installed in a virtual environment (`.venv`)
- TA-Lib, SciPy, Pandas, NumPy

### 2. Configuration
Copy the sanitized configuration template and add your sandbox/testnet API credentials:
```bash
cp user_data/config_futures.json user_data/config_live.json
```

### 3. Running the Engine
Execute dry-run paper trading with the flagship strategy:
```bash
freqtrade trade --config user_data/config_futures.json --strategy ApexDualAlpha_Futures_Strategy
```

Launch the complete ecosystem (Trading Bot + Telemetry Dashboard) on Windows:
```cmd
START_LIVE_SESSION.bat
```

---

## Disclaimer

This repository contains quantitative research and automated trading algorithms. Cryptocurrency futures trading carries substantial financial risk. Past performance in backtesting or paper-trading simulations does not guarantee future financial returns.
