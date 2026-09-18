@echo off
title APEX QUANTUM COMMAND CENTER - LIVE ENGINE
cd /d "C:\Users\geova\Downloads\FREQTRADE"

echo ===================================================
echo   APEX QUANTUM V3.0 - FREQTRADE LIVE CONTROLLER
echo ===================================================
echo [1/3] Memulai Dashboard Web Server di port 5050...
start /b "" ".\.venv\Scripts\python.exe" dashboard_server.py

echo [2/3] Memulai AI Supervisor Daemon...
start /b "" ".\.venv\Scripts\python.exe" ai_supervisor_daemon.py

echo [3/3] Memulai Freqtrade Live Bot (ApexDualAlpha_Omni_V11_OptionB - Bybit Futures 7-Pairs)...
start /b "" ".\.venv\Scripts\freqtrade.exe" trade --config user_data/config_futures.json --strategy ApexDualAlpha_Omni_V11_OptionB

echo.
echo ===================================================
echo  [OK] Seluruh Engine Berhasil Diaktifkan!
echo  Dashboard: http://127.0.0.1:5050
echo ===================================================
timeout /t 3 >nul
start http://127.0.0.1:5050
