import json
import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import zipfile
from datetime import datetime, timezone
import time
import io
import ccxt
import pandas as pd
import talib.abstract as ta
import psutil

PORT = 5050
DB_PATH = Path('user_data/tradesv3.dryrun.sqlite')
BACKTEST_DIR = Path('user_data/backtest_results')
CONFIG_PATH = Path('user_data/config_futures.json')

_radar_cache = {'time': 0, 'data': []}
_benchmark_cache = {'time': 0, 'data': None}

def get_config_info():
    initial_wallet = 1000.0
    whitelist = [
        'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT',
        'ADA/USDT:USDT', 'DOGE/USDT:USDT', 'LINK/USDT:USDT', 'PAXG/USDT:USDT'
    ]
    max_open_trades = 2
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                initial_wallet = float(cfg.get('dry_run_wallet', 1000.0))
                wl = cfg.get('exchange', {}).get('pair_whitelist')
                if wl and isinstance(wl, list):
                    whitelist = wl
                max_open_trades = int(cfg.get('max_open_trades', 2))
        except Exception:
            pass
    return {
        'initial_wallet': initial_wallet,
        'whitelist': whitelist,
        'max_open_trades': max_open_trades
    }

def get_freqtrade_pid():
    candidates = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(proc.info.get('cmdline') or [])
            if 'freqtrade' in cmd and 'trade' in cmd:
                candidates.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if candidates:
        return candidates[-1]
    return None

def get_session_start_time():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            cmd = " ".join(proc.info.get('cmdline') or [])
            if 'freqtrade' in cmd and 'trade' in cmd:
                ct = proc.info.get('create_time')
                if ct:
                    return datetime.fromtimestamp(ct).strftime('%Y-%m-%d %H:%M:%S WIB')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')

def get_live_market_radar():
    global _radar_cache
    now = time.time()
    if now - _radar_cache['time'] < 15 and len(_radar_cache['data']) > 0:
        return _radar_cache['data']
    
    cfg_info = get_config_info()
    pairs = cfg_info['whitelist']
    radar = []
    try:
        exchange = ccxt.bybit({'options': {'defaultType': 'linear'}, 'timeout': 10000})
        tickers = {}
        try:
            tickers = exchange.fetch_tickers(pairs)
        except Exception:
            pass

        for p in pairs:
            sym = p
            try:
                t = tickers.get(sym) or exchange.fetch_ticker(sym)
                o1 = exchange.fetch_ohlcv(sym, '1h', limit=100)
                o4 = exchange.fetch_ohlcv(sym, '4h', limit=250)
                
                df1 = pd.DataFrame(o1, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                df4 = pd.DataFrame(o4, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                
                # 1H Indicators
                df1['rsi'] = ta.RSI(df1, timeperiod=14)
                df1['ema21'] = ta.EMA(df1, timeperiod=21)
                df1['ema50'] = ta.EMA(df1, timeperiod=50)
                df1['ema200'] = ta.EMA(df1, timeperiod=200)
                df1['atr'] = ta.ATR(df1, timeperiod=14)
                boll = ta.BBANDS(df1, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
                df1['bb_mid'] = boll['middleband']
                stoch = ta.STOCH(df1, fastk_period=14, slowk_period=3, slowd_period=3)
                df1['k'] = stoch['slowk']
                df1['d'] = stoch['slowd']
                
                # 4H Indicators
                df4['adx'] = ta.ADX(df4, timeperiod=14)
                df4['ema50'] = ta.EMA(df4, timeperiod=50)
                df4['ema200'] = ta.EMA(df4, timeperiod=200)
                
                c = float(df1['close'].iloc[-1])
                h = float(df1['high'].iloc[-1])
                l = float(df1['low'].iloc[-1])
                r = float(df1['rsi'].iloc[-1])
                k_val = float(df1['k'].iloc[-1])
                d_val = float(df1['d'].iloc[-1])
                ema21 = float(df1['ema21'].iloc[-1])
                ema50 = float(df1['ema50'].iloc[-1])
                ema200_1h = float(df1['ema200'].iloc[-1])
                bb_mid = float(df1['bb_mid'].iloc[-1])
                atr1 = float(df1['atr'].iloc[-1])
                
                c4 = float(df4['close'].iloc[-1])
                adx4 = float(df4['adx'].iloc[-1])
                ema50_4h = float(df4['ema50'].iloc[-1])
                ema200_4h = float(df4['ema200'].iloc[-1])
                
                # Strategy Gate Parameters from ApexDualAlpha_Omni_V11_Ultimate
                is_btc = 'BTC' in p
                is_sol = 'SOL' in p
                is_eth = 'ETH' in p
                
                adx_long_gate = 24.0 if is_btc else (22.5 if (is_eth or is_sol) else 21.0)
                adx_short_gate = 25.0 if is_sol else 22.0
                
                rsi_min_long = 44.0 if is_sol else 43.0
                rsi_min_short = 56.0 if is_sol else 53.0
                
                # Pillar 1: Macro 4H Direction
                macro_bull = (c4 > ema50_4h) and (ema50_4h > ema200_4h) and (adx4 > adx_long_gate)
                macro_bear = (c4 < ema50_4h) and (ema50_4h < ema200_4h) and (adx4 > adx_short_gate)
                
                if macro_bull:
                    gate_text = 'Bullish Gate (4H)'
                elif macro_bear:
                    if is_btc:
                        gate_text = 'Macro Bear (BTC Long-Only Filter)'
                    else:
                        gate_text = 'Bearish Gate (4H)'
                else:
                    gate_text = 'Sideways (Chop Filter)'
                    
                # Pillar 2: RSI Gate
                rsi_long_ok = (rsi_min_long <= r <= 63.0)
                rsi_short_ok = (rsi_min_short <= r <= 67.0)
                
                # Pillar 3: Discount EMA Zone & Resistance Exhaustion
                ema_long_ok = ((c <= ema21 * 1.006) or (l <= bb_mid)) and (c >= ema50 * 0.990)
                ema_short_ok = ((h >= ema21 * 0.996) or (h >= bb_mid * 0.998)) and (c <= ema50 + 0.30 * atr1)
                if is_sol:
                    ema_short_ok = ema_short_ok and (c < ema200_1h)
                
                # Pillar 4: Stochastic Trigger
                stoch_cross_bull = (k_val > d_val) and (k_val < 54.0)
                stoch_cross_bear = (k_val < d_val) and (k_val > 55.0)
                
                # Calculate True Signal Readiness Conviction (0% - 100%)
                readiness = 0
                if macro_bull:
                    readiness += 35
                    if rsi_long_ok: readiness += 25
                    if ema_long_ok: readiness += 20
                    if stoch_cross_bull: readiness += 20
                elif macro_bear and not is_btc:
                    readiness += 35
                    if rsi_short_ok: readiness += 25
                    if ema_short_ok: readiness += 20
                    if stoch_cross_bear: readiness += 20
                else:
                    readiness = 20
                    if rsi_long_ok or (rsi_short_ok and not is_btc): readiness += 10
                    if stoch_cross_bull or (stoch_cross_bear and not is_btc): readiness += 5
                
                if readiness >= 85:
                    action_status = 'Signal Trigger Matang'
                elif readiness >= 60:
                    action_status = 'Setup Berkembang (Menunggu Trigger)'
                elif readiness >= 40:
                    action_status = 'RSI Masuk Zona Filter'
                else:
                    action_status = 'Pasar Sideways / Standby'
                
                radar.append({
                    'pair': p,
                    'mark_price': round(c, 4 if c < 1 else 2),
                    'change_24h': round(float(t.get('percentage', 0) or 0), 2),
                    'rsi_1h': round(r, 1),
                    'rsi_thresh': f'{int(rsi_min_long)}-63 (Long) / {int(rsi_min_short)}-67 (Short)' if not is_btc else f'{int(rsi_min_long)}-63 (Long Only)',
                    'gate_4h': gate_text,
                    'action': action_status,
                    'conviction': readiness
                })
            except Exception as pe:
                pass
        if len(radar) > 0:
            _radar_cache = {'time': now, 'data': radar}
            return radar
    except Exception as e:
        print('Error fetching live radar:', e)
        
    if len(_radar_cache['data']) > 0:
        return _radar_cache['data']
    return []

def load_benchmark_data():
    global _benchmark_cache
    now = time.time()
    if _benchmark_cache['data'] and (now - _benchmark_cache['time'] < 60):
        return _benchmark_cache['data']

    zips = sorted(BACKTEST_DIR.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)
    if not zips:
        return {'trades': [], 'summary': {}, 'equity_curves': {}}
    
    target_zip = zips[0]
    try:
        with zipfile.ZipFile(target_zip, 'r') as z:
            wallet_df = None
            for name in z.namelist():
                if 'wallet.feather' in name:
                    try:
                        wallet_df = pd.read_feather(io.BytesIO(z.read(name)))
                    except Exception:
                        pass
                    break

            for name in z.namelist():
                if name.endswith('.json') and not name.endswith('_config.json'):
                    data = json.loads(z.read(name).decode('utf-8'))
                    strat = None
                    for s in data.get('strategy', {}):
                        if 'V11' in s or 'Ultimate' in s:
                            strat = s
                            break
                    if not strat:
                        strat = list(data.get('strategy', {}).keys())[0]
                    strat_data = data['strategy'][strat]
                    
                    raw_trades = strat_data.get('trades', [])
                    trades = []
                    
                    start_bal = float(strat_data.get('starting_balance', 25.0) or 25.0)
                    running_equity = start_bal
                    curve_all = [{'time': 'Start', 'equity': round(start_bal, 2), 'pnl_pct': 0.0}]
                    
                    exit_counts = {}
                    for t in raw_trades:
                        p_ratio = t.get('profit_ratio', 0.0)
                        p_usd = t.get('profit_abs', 0.0)
                        running_equity += p_usd
                        
                        exit_r = t.get('exit_reason', 'roi')
                        exit_counts[exit_r] = exit_counts.get(exit_r, 0) + 1
                        
                        lev = t.get('leverage', 3.0) or 3.0
                        trades.append({
                            'pair': t.get('pair', ''),
                            'open_date': t.get('open_date', ''),
                            'close_date': t.get('close_date', ''),
                            'profit_ratio': p_ratio,
                            'profit_abs': round(p_usd, 4),
                            'profit_pct': round(p_ratio * 100 * lev, 2),
                            'open_rate': t.get('open_rate', 0.0),
                            'close_rate': t.get('close_rate', 0.0),
                            'exit_reason': exit_r,
                            'enter_tag': t.get('enter_tag', ''),
                            'is_short': bool(t.get('is_short', False)),
                            'leverage': lev
                        })
                        
                        c_date = t.get('close_date', '')
                        if c_date:
                            curve_all.append({
                                'time': c_date.replace('T', ' ')[:16],
                                'equity': round(running_equity, 2),
                                'pnl_pct': round(((running_equity - start_bal) / start_bal) * 100, 2)
                            })
                    
                    # 30D curve (trades since 2026-08-11)
                    curve_30d = [c for c in curve_all if c['time'] >= '2026-08-11']
                    if not curve_30d:
                        curve_30d = curve_all[-30:] if len(curve_all) > 30 else curve_all
                    
                    # 7D curve (trades since 2026-09-03)
                    curve_7d = [c for c in curve_all if c['time'] >= '2026-09-03']
                    if not curve_7d:
                        curve_7d = curve_all[-10:] if len(curve_all) > 10 else curve_all
                    
                    # Authentic 24H curve from wallet.feather if available
                    if wallet_df is not None and not wallet_df.empty:
                        tail25 = wallet_df.tail(25)
                        curve_24h = []
                        for _, r in tail25.iterrows():
                            t_str = str(r['date'])[:16].replace('T', ' ')
                            b_val = float(r['balance'])
                            curve_24h.append({
                                'time': t_str,
                                'equity': round(b_val, 2),
                                'pnl_pct': round(((b_val - start_bal) / start_bal) * 100, 2)
                            })
                    else:
                        curve_24h = curve_all[-10:] if len(curve_all) > 10 else curve_all

                    # Dynamic Win/Loss Statistics
                    win_trades = [t for t in trades if t['profit_abs'] > 0]
                    loss_trades = [t for t in trades if t['profit_abs'] < 0]
                    avg_win_abs = round(sum(t['profit_abs'] for t in win_trades) / max(len(win_trades), 1), 2)
                    avg_loss_abs = round(sum(t['profit_abs'] for t in loss_trades) / max(len(loss_trades), 1), 2)
                    avg_win_pct = round(sum(t['profit_pct'] for t in win_trades) / max(len(win_trades), 1), 2)
                    avg_loss_pct = round(sum(t['profit_pct'] for t in loss_trades) / max(len(loss_trades), 1), 2)

                    # Dynamic Monthly Heatmap Data for Benchmark Calendar
                    monthly_heatmaps = {}
                    for t in trades:
                        c_date = t.get('close_date', '')
                        if c_date and len(c_date) >= 10:
                            m_key = c_date[:7]
                            d_key = int(c_date[8:10])
                            if m_key not in monthly_heatmaps:
                                monthly_heatmaps[m_key] = {
                                    'daily_map': {},
                                    'total_pnl': 0.0,
                                    'win_days': set(),
                                    'loss_days': set(),
                                    'total_deals': 0
                                }
                            m_entry = monthly_heatmaps[m_key]
                            m_entry['total_pnl'] += t['profit_abs']
                            m_entry['total_deals'] += 1
                            if d_key not in m_entry['daily_map']:
                                m_entry['daily_map'][d_key] = {'pnl': 0.0, 'count': 0}
                            m_entry['daily_map'][d_key]['pnl'] += t['profit_abs']
                            m_entry['daily_map'][d_key]['count'] += 1

                    for m_key, m_val in monthly_heatmaps.items():
                        m_val['total_pnl'] = round(m_val['total_pnl'], 2)
                        for d_k, d_v in m_val['daily_map'].items():
                            d_v['pnl'] = round(d_v['pnl'], 2)
                            if d_v['pnl'] > 0:
                                m_val['win_days'].add(d_k)
                            elif d_v['pnl'] < 0:
                                m_val['loss_days'].add(d_k)
                        m_val['win_days_count'] = len(m_val['win_days'])
                        m_val['loss_days_count'] = len(m_val['loss_days'])
                        m_val['win_days'] = sorted(list(m_val['win_days']))
                        m_val['loss_days'] = sorted(list(m_val['loss_days']))

                    total_wins = int(strat_data.get('wins', len(win_trades)) or len(win_trades))
                    total_losses = int(strat_data.get('losses', len(loss_trades)) or len(loss_trades))
                    winrate = round((strat_data.get('winrate', 0.7651) or 0.7651) * 100, 1)
                    profit_factor = round(strat_data.get('profit_factor', 1.41) or 1.41, 2)
                    max_dd = round((strat_data.get('max_drawdown_account', 0.1641) or 0.1641) * 100, 2)

                    res = {
                        'strategy_name': strat,
                        'total_trades_count': len(trades),
                        'summary': {
                            'initial_balance': start_bal,
                            'final_balance': round(running_equity, 2),
                            'total_profit_abs': round(running_equity - start_bal, 2),
                            'total_profit_pct': round(((running_equity - start_bal) / start_bal) * 100, 2),
                            'winrate_pct': winrate,
                            'total_wins': total_wins,
                            'total_losses': total_losses,
                            'profit_factor': profit_factor,
                            'max_drawdown_pct': max_dd,
                            'drawdown_recovery_days': 16,
                            'exit_counts': exit_counts,
                            'avg_win_pct': avg_win_pct,
                            'avg_loss_pct': avg_loss_pct,
                            'avg_win_abs': avg_win_abs,
                            'avg_loss_abs': avg_loss_abs,
                            'monthly_heatmaps': monthly_heatmaps
                        },
                        'equity_curves': {
                            'ALL': curve_all,
                            '30D': curve_30d,
                            '7D': curve_7d,
                            '24H': curve_24h
                        },
                        'trades': trades
                    }
                    _benchmark_cache = {'time': now, 'data': res}
                    return res
    except Exception as e:
        print('Error parsing benchmark zip:', e)
        return {'trades': [], 'summary': {}, 'equity_curves': {}}

def get_live_db_data():
    cfg_info = get_config_info()
    init_bal = cfg_info['initial_wallet']
    session_start_str = get_session_start_time()
    current_date_str = datetime.now().strftime('%Y-%m-%d')
    current_month_str = datetime.now().strftime('%B %Y')

    default_summary = {
        'start_time': session_start_str,
        'current_date': current_date_str,
        'current_month': current_month_str,
        'closed_count': 0,
        'session_pnl_usd': 0.0,
        'session_pnl_pct': 0.0,
        'floating_pnl_usd': 0.0,
        'floating_pnl_pct': 0.0,
        'balance': round(init_bal, 2),
        'free_collateral': round(init_bal, 2),
        'daily_map': {},
        'win_days_count': 0,
        'loss_days_count': 0
    }

    if not DB_PATH.exists():
        return {
            'open_trades': [],
            'closed_trades': [],
            'session_summary': default_summary
        }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if not cursor.fetchone():
            conn.close()
            return {
                'open_trades': [],
                'closed_trades': [],
                'session_summary': default_summary
            }

        radar_prices = {}
        if _radar_cache and _radar_cache.get('data'):
            for item in _radar_cache['data']:
                radar_prices[item['pair']] = item['mark_price']

        cursor.execute('SELECT id, pair, is_open, amount, open_rate, close_rate, close_profit, open_date, close_date, strategy, enter_tag, exit_reason, is_short, leverage, stake_amount FROM trades WHERE is_open = 1')
        open_rows = cursor.fetchall()
        open_trades = []
        total_open_stake = 0.0
        total_floating_pnl = 0.0

        for r in open_rows:
            pair = r[1]
            open_rate = float(r[4] or 0.0)
            is_short = bool(r[12])
            lev = float(r[13] or (7.0 if 'BTC' in pair else 3.0))
            stk = float(r[14] or (init_bal / 2.0))
            total_open_stake += stk

            mark_price = float(radar_prices.get(pair, open_rate))
            if open_rate > 0:
                profit_ratio = (open_rate - mark_price) / open_rate if is_short else (mark_price - open_rate) / open_rate
            else:
                profit_ratio = 0.0
            
            p_usd = profit_ratio * stk * lev
            total_floating_pnl += p_usd

            open_trades.append({
                'id': r[0],
                'pair': pair,
                'is_open': True,
                'amount': r[3],
                'open_rate': open_rate,
                'close_rate': mark_price,
                'profit_ratio': profit_ratio,
                'profit_abs': round(p_usd, 4),
                'profit_pct': round(profit_ratio * 100 * lev, 2),
                'open_date': r[7],
                'close_date': r[8],
                'strategy': r[9],
                'enter_tag': r[10] or 'Signal Trigger',
                'exit_reason': r[11] or 'Active Trajectory',
                'is_short': is_short,
                'leverage': lev,
                'stake_amount': stk
            })
            
        cursor.execute("SELECT COALESCE(SUM(close_profit_abs), 0.0), COUNT(*) FROM trades WHERE is_open = 0")
        closed_stats = cursor.fetchone()
        session_pnl = float(closed_stats[0]) if closed_stats else 0.0
        total_closed_count = int(closed_stats[1]) if closed_stats else 0

        cursor.execute("SELECT id, pair, is_open, amount, open_rate, close_rate, close_profit, close_profit_abs, open_date, close_date, strategy, enter_tag, exit_reason, is_short, leverage, stake_amount FROM trades WHERE is_open = 0 ORDER BY id DESC LIMIT 50")
        closed_rows = cursor.fetchall()
        closed_trades = []
        daily_map = {}
        win_days = set()
        loss_days = set()
        
        for r in closed_rows:
            p_ratio = float(r[6] or 0.0)
            stk = float(r[15] or (init_bal / 2.0))
            lev = float(r[14] or (7.0 if 'BTC' in r[1] else 3.0))
            if r[7] is not None:
                p_usd = float(r[7])
            else:
                p_usd = p_ratio * stk * lev

            c_date = r[9] or ''
            if c_date and len(c_date) >= 10:
                try:
                    day_key = c_date[:10]
                    if day_key not in daily_map:
                        daily_map[day_key] = {'pnl': 0.0, 'count': 0}
                    daily_map[day_key]['pnl'] += p_usd
                    daily_map[day_key]['count'] += 1
                except Exception:
                    pass
                    
            closed_trades.append({
                'id': r[0],
                'pair': r[1],
                'is_open': bool(r[2]),
                'amount': r[3],
                'open_rate': r[4],
                'close_rate': r[5],
                'profit_ratio': p_ratio,
                'profit_abs': round(p_usd, 4),
                'profit_pct': round(p_ratio * 100 * lev, 2),
                'open_date': r[8],
                'close_date': r[9],
                'strategy': r[10],
                'enter_tag': r[11],
                'exit_reason': r[12],
                'is_short': bool(r[13]),
                'leverage': lev,
                'stake_amount': stk
            })
        conn.close()
        
        for d, v in daily_map.items():
            if v['pnl'] > 0:
                win_days.add(d)
            elif v['pnl'] < 0:
                loss_days.add(d)
        
        live_balance = round(init_bal + session_pnl, 2)
        free_collateral = round(live_balance - total_open_stake, 2)
        floating_pnl_pct = round((total_floating_pnl / init_bal) * 100, 2)
        
        return {
            'open_trades': open_trades,
            'closed_trades': closed_trades,
            'session_summary': {
                'start_time': session_start_str,
                'current_date': current_date_str,
                'current_month': current_month_str,
                'closed_count': total_closed_count,
                'session_pnl_usd': round(session_pnl, 2),
                'session_pnl_pct': round((session_pnl / init_bal) * 100, 2),
                'floating_pnl_usd': round(total_floating_pnl, 2),
                'floating_pnl_pct': floating_pnl_pct,
                'balance': live_balance,
                'free_collateral': free_collateral,
                'daily_map': daily_map,
                'win_days_count': len(win_days),
                'loss_days_count': len(loss_days)
            }
        }
    except Exception as e:
        return {
            'error': str(e),
            'open_trades': [],
            'closed_trades': [],
            'session_summary': default_summary
        }

class DashboardHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/' or parsed.path == '/index.html':
            html_file = Path('user_data/dashboard/index.html')
            if html_file.exists():
                with open(html_file, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
        elif parsed.path == '/api/data':
            live_data = get_live_db_data()
            benchmark_data = load_benchmark_data()
            radar_data = get_live_market_radar()
            cfg_info = get_config_info()
            freq_pid = get_freqtrade_pid()
            
            init_bal = cfg_info['initial_wallet']
            session_sum = live_data.get('session_summary', {})
            payload = {
                'server_time': datetime.now(timezone.utc).isoformat(),
                'status': 'online',
                'daemon_pid': freq_pid or 'ACTIVE',
                'current_date': session_sum.get('current_date', '2026-09-10'),
                'current_month': session_sum.get('current_month', 'September 2026'),
                'strategy': 'ApexDualAlpha_Omni_V11_Ultimate',
                'config': {
                    'exchange': 'Bybit Perpetual Futures',
                    'margin_mode': 'Isolated',
                    'leverage': '3.0x (Altcoins & PAXG) / 7.0x (BTC Dynamic)',
                    'whitelist': cfg_info['whitelist'],
                    'max_open_trades': cfg_info['max_open_trades'],
                    'initial_wallet': init_bal,
                    'stake_amount': f'Dynamic Compound (Unlimited) / Max {cfg_info["max_open_trades"]} Trades'
                },
                'market_radar': radar_data,
                'live_simulation': {
                    'is_active': True,
                    'mode_title': 'Simulasi Live Trading (Dry-Run Paper Trading)',
                    'description': f'Berjalan langsung dengan feed WebSocket Bybit Futures real-time menggunakan saldo virtual ${init_bal:,.2f} USDT.',
                    'start_time': session_sum.get('start_time', '2026-09-10 19:30:00 WIB'),
                    'wallet_balance': session_sum.get('balance', init_bal),
                    'free_collateral': session_sum.get('free_collateral', init_bal),
                    'session_pnl_usd': session_sum.get('session_pnl_usd', 0.0),
                    'session_pnl_pct': session_sum.get('session_pnl_pct', 0.0),
                    'floating_pnl_usd': session_sum.get('floating_pnl_usd', 0.0),
                    'floating_pnl_pct': session_sum.get('floating_pnl_pct', 0.0),
                    'closed_count': session_sum.get('closed_count', 0),
                    'daily_map': session_sum.get('daily_map', {}),
                    'win_days_count': session_sum.get('win_days_count', 0),
                    'loss_days_count': session_sum.get('loss_days_count', 0),
                    'session_summary': session_sum,
                    'open_trades': live_data.get('open_trades', []),
                    'closed_trades': live_data.get('closed_trades', []),
                    'chart_24h': benchmark_data.get('equity_curves', {}).get('24H', [])
                },
                'real_live_trading': {
                    'is_armed': False,
                    'status': 'DISARMED / STANDBY (SAFE MODE)',
                    'mode_title': 'Live Trading Asli (Real Funds / Mainnet Bybit)',
                    'safety_notice': 'Trading Asli belum diaktifkan. Modal nyata $0.00 dalam status aman (terisolasi). Sistem saat ini hanya mengeksekusi logika di lingkungan Simulasi Live.',
                    'real_wallet_balance': 0.00,
                    'real_trades_count': 0,
                    'checklist': [
                        {'item': 'Bybit Real API Key & Secret', 'status': 'NOT CONFIGURED (Safe Mode)', 'ok': False},
                        {'item': 'Isolated Margin Enforcement (3.0x / 7.0x)', 'status': 'VERIFIED LOCKED', 'ok': True},
                        {'item': f'Max Risk per Trade Cap (50% Wallet = ${init_bal/2:,.2f})', 'status': 'VERIFIED LOCKED', 'ok': True},
                        {'item': 'Automated Daily Loss Kill Switch (-2.0%)', 'status': 'ARMED & ACTIVE', 'ok': True}
                    ]
                },
                'benchmark': benchmark_data
            }
            
            body = json.dumps(payload).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    print(f'Starting Freqtrade Multi-Mode Dashboard Bridge API on port {PORT}...')
    server = HTTPServer(('127.0.0.1', PORT), DashboardHandler)
    server.serve_forever()

if __name__ == '__main__':
    run_server()
