import sqlite3
import requests
import datetime
import json
import time
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
            id INTEGER PRIMARY KEY CHECK (id = 1), date TEXT, 
            trend_state TEXT DEFAULT 'IDLE', active_trade TEXT DEFAULT 'NO_TRADE')''')

        conn.execute('''INSERT OR IGNORE INTO engine_state 
            (id, date, trend_state, active_trade) VALUES (1, '', 'IDLE', 'NO_TRADE')''')

        cols = [
            ("bars_in_trade", "INTEGER DEFAULT 0"),
            ("cooldown", "INTEGER DEFAULT 0"),
            ("atr_hist", "TEXT DEFAULT '[]'"),
            ("max_profit", "REAL DEFAULT 0.0"),
            ("entry_spot", "REAL DEFAULT 0.0"),
            ("momentum_history", "TEXT DEFAULT '[]'"),
            ("prev_momentum", "REAL DEFAULT 0.0"),
            ("prev_momentum_for_accel", "REAL DEFAULT 0.0"),
            ("vwap_num", "REAL DEFAULT 0.0"),
            ("vwap_den", "REAL DEFAULT 0.0"),
            ("last_cum_vol", "REAL DEFAULT 0.0"),
            ("prev_atr", "REAL DEFAULT 1.0"),
            ("prev_oi_flow", "REAL DEFAULT 0.0"),
            ("trend_lock", "INTEGER DEFAULT 0"),
            ("min_hold", "INTEGER DEFAULT 0"),
            ("daily_trades", "INTEGER DEFAULT 0"),
            ("consecutive_losses", "INTEGER DEFAULT 0"),
            ("loss_cooldown_until", "INTEGER DEFAULT 0"),
            ("last_trade_time", "INTEGER DEFAULT 0")
        ]
        for col, dtype in cols:
            try: conn.execute(f"ALTER TABLE engine_state ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError: pass

def fetch_nse_data():
    symbol = "NIFTY"
    base_url = "https://www.nseindia.com"
    for attempt in range(3):
        try:
            session.get(base_url, timeout=10)
            exp_resp = session.get(f"{base_url}/api/option-chain-contract-info?symbol={symbol}", timeout=10)
            if exp_resp.ok:
                nearest_expiry = exp_resp.json()['expiryDates'][0]
                chain_resp = session.get(
                    f"{base_url}/api/option-chain-v3?type=Indices&symbol={symbol}&expiry={nearest_expiry}",
                    timeout=10
                )
                if chain_resp.ok:
                    data = chain_resp.json()
                    if 'records' in data and 'data' in data['records'] and len(data['records']['data']) > 0:
                        return data
        except Exception as e: 
            pass
        time.sleep(2)
    return None

def store_snapshot_and_get_data(now: datetime.datetime):
    data = fetch_nse_data()
    if not data or 'records' not in data: return False
    
    spot = data['records']['underlyingValue']
    atm_strike = round(spot / 50) * 50
    target_strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
    
    ts_str = now.strftime('%Y-%m-%d %H:%M:00')
    ce_oi, pe_oi, ce_vol, pe_vol = 0, 0, 0, 0
    
    for r in data['records']['data']:
        if r.get('strikePrice') in target_strikes:
            ce = r.get('CE', {})
            pe = r.get('PE', {})
            ce_oi += ce.get('openInterest', 0)
            pe_oi += pe.get('openInterest', 0)
            ce_vol += ce.get('totalTradedVolume', 0)
            pe_vol += pe.get('totalTradedVolume', 0)

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT OR REPLACE INTO snapshots 
            (timestamp, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, trading_status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
            (ts_str, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, 'NO_TRADE'))
    return ts_str

def update_snapshot_status(ts_str, status):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE snapshots SET trading_status = ? WHERE timestamp = ?", (status, ts_str))

def load_snapshots(limit=35):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in reversed(rows)] 

def load_state(today_str: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        state = dict(conn.execute("SELECT * FROM engine_state WHERE id=1").fetchone())
        
        if state.get('date') != today_str:
            state['date'] = today_str
            state['atr_hist'] = '[]'
            state['momentum_history'] = '[]'
            state['trend_state'] = 'IDLE'
            state['active_trade'] = 'NO_TRADE'
            state['bars_in_trade'] = 0
            state['cooldown'] = 0
            state['max_profit'] = 0.0
            state['entry_spot'] = 0.0
            state['prev_momentum'] = 0.0
            state['prev_momentum_for_accel'] = 0.0
            state['vwap_num'] = 0.0
            state['vwap_den'] = 0.0
            state['last_cum_vol'] = 0.0
            state['prev_atr'] = 15.0
            state['prev_oi_flow'] = 0.0
            state['trend_lock'] = 0
            state['min_hold'] = 0
            state['daily_trades'] = 0
            state['consecutive_losses'] = 0
            state['loss_cooldown_until'] = 0
            state['last_trade_time'] = 0
            
        state['atr_hist'] = json.loads(state.get('atr_hist', '[]'))
        state['momentum_history'] = json.loads(state.get('momentum_history', '[]'))
        return state

def save_state(state):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''UPDATE engine_state SET 
            date=?, active_trade=?, bars_in_trade=?, cooldown=?,
            atr_hist=?, trend_state=?, max_profit=?, entry_spot=?,
            momentum_history=?, prev_momentum=?, prev_momentum_for_accel=?, 
            vwap_num=?, vwap_den=?, last_cum_vol=?, prev_atr=?, prev_oi_flow=?,
            trend_lock=?, min_hold=?, daily_trades=?, consecutive_losses=?, 
            loss_cooldown_until=?, last_trade_time=? WHERE id=1''',
            (state['date'], state['active_trade'], state['bars_in_trade'], state.get('cooldown', 0),
             json.dumps(state['atr_hist']), state.get('trend_state', 'IDLE'),
             state.get('max_profit', 0.0), state.get('entry_spot', 0.0),
             json.dumps(state.get('momentum_history', [])),
             state.get('prev_momentum', 0.0), state.get('prev_momentum_for_accel', 0.0),
             state.get('vwap_num', 0.0), state.get('vwap_den', 0.0), state.get('last_cum_vol', 0.0),
             state.get('prev_atr', 1.0), state.get('prev_oi_flow', 0.0),
             state.get('trend_lock', 0), state.get('min_hold', 0),
             state.get('daily_trades', 0), state.get('consecutive_losses', 0), 
             state.get('loss_cooldown_until', 0), state.get('last_trade_time', 0)))

def log_engine_run(ts, trend, status, printed, reason, raw):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS engine_log (timestamp TEXT, trend TEXT, trading_status TEXT, signal_printed INTEGER, suppress_reason TEXT, raw_output TEXT)")
        conn.execute("INSERT INTO engine_log VALUES (?, ?, ?, ?, ?, ?)", (ts, trend, status, int(printed), reason, raw))