# Azura Quant Autonomous Watchdog & Auto-Starter
$targetDir = "C:\Users\geova\Downloads\FREQTRADE"
Set-Location $targetDir

$logFile = "$targetDir\autostart.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Checking Azura Quant daemon statuses..."

# 1. Check Freqtrade Core (Port 8080)
$ftProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*ApexDualAlpha_Omni_V12_LinkCalibrated*" }
if (-not $ftProc) {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Freqtrade Core is down. Starting..."
    Start-Process -FilePath "$targetDir\.venv\Scripts\python.exe" `
        -ArgumentList "-m freqtrade trade --config user_data/config_futures.json --strategy ApexDualAlpha_Omni_V12_LinkCalibrated --dry-run --logfile user_data/logs/freqtrade.log" `
        -WorkingDirectory $targetDir -WindowStyle Hidden
} else {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Freqtrade Core is already running (PID: $($ftProc.ProcessId))."
}

Start-Sleep -Seconds 2

# 2. Check Dashboard Server (Port 5050)
$dashProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*dashboard_server.py*" }
if (-not $dashProc) {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Dashboard Server is down. Starting..."
    Start-Process -FilePath "$targetDir\.venv\Scripts\python.exe" `
        -ArgumentList "dashboard_server.py" `
        -WorkingDirectory $targetDir -WindowStyle Hidden
} else {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Dashboard Server is already running (PID: $($dashProc.ProcessId))."
}

# 3. Check AI Strategic Supervisor
$aiProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*ai_supervisor_daemon.py*" }
if (-not $aiProc) {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] AI Supervisor is down. Starting..."
    Start-Process -FilePath "$targetDir\.venv\Scripts\python.exe" `
        -ArgumentList "ai_supervisor_daemon.py" `
        -WorkingDirectory $targetDir -WindowStyle Hidden
} else {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] AI Supervisor is already running (PID: $($aiProc.ProcessId))."
}

# 4. Check Derivatives Feed Collector
$derivProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*derivatives_feed_collector.py*" }
if (-not $derivProc) {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Derivatives Feed Collector is down. Starting..."
    Start-Process -FilePath "$targetDir\.venv\Scripts\python.exe" `
        -ArgumentList "user_data\modules\derivatives_feed_collector.py" `
        -WorkingDirectory $targetDir -WindowStyle Hidden
} else {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Derivatives Feed Collector is already running (PID: $($derivProc.ProcessId))."
}

# 5. Check Fundamental News Sentiment Daemon
$newsProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*news_sentiment_daemon.py*" }
if (-not $newsProc) {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] News Sentiment Daemon is down. Starting..."
    Start-Process -FilePath "$targetDir\.venv\Scripts\python.exe" `
        -ArgumentList "user_data\modules\news_sentiment_daemon.py" `
        -WorkingDirectory $targetDir -WindowStyle Hidden
} else {
    Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] News Sentiment Daemon is already running (PID: $($newsProc.ProcessId))."
}

Add-Content -Path $logFile -Value "[$timestamp] [WATCHDOG] Check complete."
