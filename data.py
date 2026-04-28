import sqlite3
import requests
import datetime
import json
import time
from config import DB_NAME, IST

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/option-chain",
})

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            timestamp TEXT PRIMARY KEY, spot REAL, atm_strike REAL,
            ce_oi REAL, pe_oi REAL, ce_vol REAL, pe_vol REAL,
            trading_status TEXT DEFAULT 'NO_TRADE')''')

        # Updated schema: explicitly includes exit_confirm_bars
        conn.execute('''CREATE TABLE IF NOT EXISTS engine_state (
            id INTEGER PRIMARY KEY CHECK (id = 1), date TEXT,
            active_trade TEXT DEFAULT 'NO_TRADE', entry_spot REAL DEFAULT 0.0,
            bars_in_trade INTEGER DEFAULT 0, max_profit REAL DEFAULT 0.0,
            daily_pnl REAL DEFAULT 0.0, consecutive_losses INTEGER DEFAULT 0,
            cooldown_bars INTEGER DEFAULT 0, call_bias_bars INTEGER DEFAULT 0, 
            put_bias_bars INTEGER DEFAULT 0, pcr_strong_call_bars INTEGER DEFAULT 0, 
            pcr_strong_put_bars INTEGER DEFAULT 0, consecutive_opposite_bias INTEGER DEFAULT 0,
            exit_confirm_bars INTEGER DEFAULT 0,
            pcr_history TEXT DEFAULT '[]', oi_bias_history TEXT DEFAULT '[]')''')

        conn.execute('''INSERT OR IGNORE INTO engine_state (id, date) VALUES (1, '')''')

def fetch_nse_data():
    symbol = "NIFTY"
    base_url = "https://www.nseindia.com"
    for attempt in range(3):
        try:
            session.get(base_url, timeout=10)
            exp_resp = session.get(f"{base_url}/api/option-chain-contract-info?symbol={symbol}", timeout=10)
            if exp_resp.ok:
                nearest_expiry = exp_resp.json()['expiryDates'][0]
                chain_resp = session.get(f"{base_url}/api/option-chain-v3?type=Indices&symbol={symbol}&expiry={nearest_expiry}", timeout=10)
                if chain_resp.ok:
                    data = chain_resp.json()
                    if 'records' in data and len(data['records']['data']) > 0:
                        return data
        except Exception:
            pass
        time.sleep(2)
    return None

def store_snapshot_and_get_data(now):
    data = fetch_nse_data()
    if not data or 'records' not in data: return False

    spot = data['records'].get('underlyingValue', 0)
    if spot < 10000 or spot > 40000: return False

    atm_strike = round(spot / 50) * 50
    ts_str = now.strftime('%Y-%m-%d %H:%M:00')

    ce_oi = pe_oi = ce_vol = pe_vol = 0.0
    for row in data['records']['data']:
        strike = row.get('strikePrice')
        if strike is None: continue
        ce = row.get('CE', {}) or {}
        pe = row.get('PE', {}) or {}
        
        ce_oi += ce.get('openInterest', 0) or 0
        pe_oi += pe.get('openInterest', 0) or 0
        ce_vol += ce.get('totalTradedVolume', 0) or 0
        pe_vol += pe.get('totalTradedVolume', 0) or 0

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT OR REPLACE INTO snapshots
            (timestamp, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, trading_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'NO_TRADE')''',
            (ts_str, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol))
    return ts_str

def load_snapshots(limit=35):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(cur.fetchall())]

def load_state(today_str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        state = dict(conn.execute("SELECT * FROM engine_state WHERE id=1").fetchone())

        if state.get('date') != today_str:
            state.update({
                'date': today_str, 'active_trade': 'NO_TRADE', 'entry_spot': 0.0,
                'bars_in_trade': 0, 'max_profit': 0.0, 'daily_pnl': 0.0,
                'consecutive_losses': 0, 'cooldown_bars': 0,
                'call_bias_bars': 0, 'put_bias_bars': 0,
                'pcr_strong_call_bars': 0, 'pcr_strong_put_bars': 0,
                'consecutive_opposite_bias': 0, 'exit_confirm_bars': 0,
                'pcr_history': '[]', 'oi_bias_history': '[]'
            })

        for key in ['pcr_history', 'oi_bias_history']:
            try: state[key] = json.loads(state.get(key, '[]'))
            except: state[key] = []
        return state

def save_state(state):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''UPDATE engine_state SET
            date=?, active_trade=?, entry_spot=?, bars_in_trade=?, max_profit=?,
            daily_pnl=?, consecutive_losses=?, cooldown_bars=?,
            call_bias_bars=?, put_bias_bars=?, pcr_strong_call_bars=?, pcr_strong_put_bars=?,
            consecutive_opposite_bias=?, exit_confirm_bars=?, pcr_history=?, oi_bias_history=? WHERE id=1''',
            (state['date'], state['active_trade'], state['entry_spot'], state['bars_in_trade'],
             state['max_profit'], state['daily_pnl'], state['consecutive_losses'], state['cooldown_bars'],
             state['call_bias_bars'], state['put_bias_bars'], state['pcr_strong_call_bars'], state['pcr_strong_put_bars'],
             state['consecutive_opposite_bias'], state['exit_confirm_bars'], json.dumps(state['pcr_history']), json.dumps(state['oi_bias_history'])))