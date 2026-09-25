import os
import sys
import time
import subprocess
from pathlib import Path
import psutil
import requests

def kill_old_processes():
    cur_pid = os.getpid()
    target_keywords = [
        'dashboard_server.py',
        'ai_supervisor_daemon.py',
        'news_sentiment_daemon.py',
        'derivatives_feed_collector.py',
        'freqtrade trade',
        'freqtrade.exe trade',
        'http.server 8000'
    ]
    killed = []
    for p in psutil.process_iter(['pid', 'name']):
        if p.pid == cur_pid:
            continue
        try:
            cmd = ' '.join(p.cmdline() or []).lower()
            if any(kw in cmd for kw in target_keywords):
                print(f"Terminating PID {p.pid}: {p.name()} -> {cmd[:70]}...")
                p.kill()
                killed.append(p.pid)
        except Exception:
            pass
    print(f"Terminated {len(killed)} old/duplicate processes.")
    time.sleep(2)

def start_daemons():
    root = Path(__file__).resolve().parent.parent
    py_exe = str(root / ".venv" / "Scripts" / "python.exe")
    ft_exe = str(root / ".venv" / "Scripts" / "freqtrade.exe")

    print("\nStarting 5 autonomous quant services cleanly under .venv...")

    # 1. Derivatives Feed Collector
    p1 = subprocess.Popen(
        [py_exe, "user_data/modules/derivatives_feed_collector.py"],
        cwd=str(root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    print(f" -> [1/5] Derivatives Feed Collector started (PID {p1.pid})")
    time.sleep(1)

    # 2. News Sentiment Daemon
    p2 = subprocess.Popen(
        [py_exe, "user_data/modules/news_sentiment_daemon.py"],
        cwd=str(root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    print(f" -> [2/5] News Sentiment Daemon started (PID {p2.pid})")
    time.sleep(1)

    # 3. Freqtrade Live Core (Port 8080)
    p3 = subprocess.Popen(
        [
            ft_exe, "trade",
            "--config", "user_data/config_futures.json",
            "--strategy", "ApexDualAlpha_Omni_V12_LinkCalibrated",
            "--dry-run",
            "--logfile", "user_data/logs/freqtrade.log"
        ],
        cwd=str(root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    print(f" -> [3/5] Freqtrade Bot started (PID {p3.pid})")
    time.sleep(3)

    # 4. AI Strategic Supervisor Daemon
    p4 = subprocess.Popen(
        [py_exe, "ai_supervisor_daemon.py"],
        cwd=str(root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    print(f" -> [4/5] AI Strategic Supervisor Daemon started (PID {p4.pid})")
    time.sleep(1)

    # 5. Dashboard Web Server (Port 5050)
    p5 = subprocess.Popen(
        [py_exe, "dashboard_server.py"],
        cwd=str(root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    print(f" -> [5/5] Dashboard Web Server started (PID {p5.pid})")
    time.sleep(2)

def verify_services():
    print("\nVerifying health of all services...")
    # Check Freqtrade API ping (8080)
    ft_ok = False
    for _ in range(10):
        try:
            r = requests.get("http://127.0.0.1:8080/api/v1/ping", timeout=3)
            if r.status_code == 200 and r.json().get("status") == "pong":
                ft_ok = True
                print(" -> Freqtrade API (port 8080): HEALTHY (pong)")
                break
        except Exception:
            time.sleep(1)
    if not ft_ok:
        print(" -> WARNING: Freqtrade API not responding on port 8080 yet.")

    # Check Dashboard API (5050)
    dash_ok = False
    for _ in range(10):
        try:
            r = requests.get("http://127.0.0.1:5050/api/data", timeout=3)
            if r.status_code == 200:
                data = r.json()
                cfg = data.get("config", {})
                live_sim = data.get("live_simulation", {})
                print(f" -> Dashboard API (port 5050): HEALTHY (HTTP 200 OK)")
                print(f"    - Max open trades: {cfg.get('max_open_trades')}")
                print(f"    - AI Slot Mode: {live_sim.get('ai_slot_mode')}")
                print(f"    - AI Slots Available: {live_sim.get('ai_slots_available')}")
                dash_ok = True
                break
        except Exception:
            time.sleep(1)
    if not dash_ok:
        print(" -> WARNING: Dashboard API not responding on port 5050 yet.")

if __name__ == "__main__":
    kill_old_processes()
    start_daemons()
    verify_services()
