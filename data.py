import sqlite3
import requests
import datetime
import json
from config import DB_NAME, IST

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.nseindia.com/option-chain",
})

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            timestamp TEXT PRIMARY KEY, spot REAL, atm_strike REAL,
            ce_oi REAL, pe_oi REAL, ce_vol REAL, pe_vol REAL, trading_status TEXT DEFAULT 'NO_TRADE')''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS engine_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_trend TEXT, last_accel TEXT, last_oi_label TEXT, 
            last_vol_label TEXT, prev_raw_trend TEXT, 
            slope_history TEXT, session_open_spot REAL, date TEXT, last_trading_status TEXT)''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS engine_log (
            timestamp TEXT, trend TEXT, trading_status TEXT, signal_printed INTEGER, 
            suppress_reason TEXT, raw_output TEXT)''')

        conn.execute('''INSERT OR IGNORE INTO engine_state 
            (id, last_trend, last_accel, last_oi_label, last_vol_label, prev_raw_trend, slope_history, session_open_spot, date, last_trading_status) 
            VALUES (1, 'SIDEWAYS', 'REVERSAL', 'NEUTRAL', 'NORMAL', 'SIDEWAYS', '[]', 0.0, '', 'NO_TRADE')''')

def fetch_nse_data():
    symbol = "NIFTY"
    base_url = "https://www.nseindia.com"
    try:
        session.get(base_url, timeout=10)
        exp_resp = session.get(f"{base_url}/api/option-chain-contract-info?symbol={symbol}", timeout=10)
        if not exp_resp.ok: return None
        nearest_expiry = exp_resp.json()['expiryDates'][0]
        chain_resp = session.get(
            f"{base_url}/api/option-chain-v3?type=Indices&symbol={symbol}&expiry={nearest_expiry}",
            timeout=10
        )
        if not chain_resp.ok: return None
        return chain_resp.json()
    except Exception as e:
        return None

def store_snapshot_and_get_data(now: datetime.datetime):
    data = fetch_nse_data()
    if not data or 'records' not in data:
        return False
    
    spot = data['records']['underlyingValue']
    atm_strike = round(spot / 50) * 50
    ts_str = now.strftime('%Y-%m-%d %H:%M:00')
    
    ce_oi, pe_oi, ce_vol, pe_vol = 0, 0, 0, 0
    for r in data['records']['data']:
        if r.get('strikePrice') == atm_strike:
            ce = r.get('CE', {})
            pe = r.get('PE', {})
            ce_oi = ce.get('openInterest', 0)
            pe_oi = pe.get('openInterest', 0)
            ce_vol = ce.get('totalTradedVolume', 0)
            pe_vol = pe.get('totalTradedVolume', 0)
            break

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT OR REPLACE INTO snapshots 
            (timestamp, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, trading_status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
            (ts_str, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, 'NO_TRADE'))
    return ts_str

def update_snapshot_status(ts_str, status):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE snapshots SET trading_status = ? WHERE timestamp = ?", (status, ts_str))

def load_snapshots(limit=15):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in reversed(rows)] 

def load_state(today_str: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        state = dict(conn.execute("SELECT * FROM engine_state WHERE id=1").fetchone())
        
        if state['date'] != today_str:
            state['session_open_spot'] = None
            state['date'] = today_str
            state['slope_history'] = '[]'
            
        state['slope_history'] = json.loads(state['slope_history'])
        return state

def save_state(state):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''UPDATE engine_state SET 
            last_trend=?, last_accel=?, last_oi_label=?, last_vol_label=?, 
            prev_raw_trend=?, slope_history=?, session_open_spot=?, date=?, last_trading_status=? WHERE id=1''',
            (state['last_trend'], state['last_accel'], state['last_oi_label'], state['last_vol_label'],
             state['prev_raw_trend'], json.dumps(state['slope_history']), state['session_open_spot'], 
             state['date'], state['last_trading_status']))

def log_engine_run(ts, trend, status, printed, reason, raw):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO engine_log VALUES (?, ?, ?, ?, ?, ?)",
                     (ts, trend, status, int(printed), reason, raw))