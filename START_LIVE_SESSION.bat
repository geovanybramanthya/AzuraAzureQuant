@echo off
title APEX QUANTUM COMMAND CENTER - LIVE ENGINE
cd /d "C:\Users\geova\Downloads\FREQTRADE"

echo ===================================================
echo   APEX QUANTUM V3.0 - FREQTRADE LIVE CONTROLLER
echo ===================================================
echo [1/5] Memulai Bybit Derivatives Feed Collector...
start /b "" ".\.venv\Scripts\python.exe" user_data\modules\derivatives_feed_collector.py

echo [2/5] Memulai Fundamental News Sentiment Daemon...
start /b "" ".\.venv\Scripts\python.exe" user_data\modules\news_sentiment_daemon.py

echo [3/5] Memulai Dashboard Web Server di port 5050...
start /b "" ".\.venv\Scripts\python.exe" dashboard_server.py

echo [4/5] Memulai AI Supervisor Daemon...
start /b "" ".\.venv\Scripts\python.exe" ai_supervisor_daemon.py

echo [5/5] Memulai Freqtrade Live Bot (ApexDualAlpha_Omni_V12_LinkCalibrated - Bybit Futures 8-Pairs)...
start /b "" ".\.venv\Scripts\freqtrade.exe" trade --config user_data/config_futures.json --strategy ApexDualAlpha_Omni_V12_LinkCalibrated --dry-run --logfile user_data/logs/freqtrade.log

echo.
echo ===================================================
echo  [OK] Seluruh 5 Engine Berhasil Diaktifkan!
echo ===================================================
ping 127.0.0.1 -n 4 >nul
