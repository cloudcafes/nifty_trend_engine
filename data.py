import sqlite3
import requests
import datetime
import json
import time
from config import DB_NAME, IST, ATR_SEED

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
            ce_oi REAL, pe_oi REAL, ce_vol REAL, pe_vol REAL,
            trading_status TEXT DEFAULT 'NO_TRADE')''')

        conn.execute('''CREATE TABLE IF NOT EXISTS engine_state (
            id INTEGER PRIMARY KEY CHECK (id = 1), date TEXT,
            trend_state TEXT DEFAULT 'IDLE', active_trade TEXT DEFAULT 'NO_TRADE')''')

        conn.execute('''INSERT OR IGNORE INTO engine_state
            (id, date, trend_state, active_trade) VALUES (1, '', 'IDLE', 'NO_TRADE')''')

        conn.execute('''CREATE TABLE IF NOT EXISTS engine_log (
            timestamp TEXT, trend TEXT, trading_status TEXT,
            signal_printed INTEGER, suppress_reason TEXT, raw_output TEXT)''')

        # Column additions (idempotent — safe to run repeatedly)
        cols = [
            # Legacy columns kept for AI context and backward compatibility
            ("bars_in_trade", "INTEGER DEFAULT 0"),
            ("max_profit", "REAL DEFAULT 0.0"),
            ("entry_spot", "REAL DEFAULT 0.0"),
            ("momentum_history", "TEXT DEFAULT '[]'"),
            ("prev_momentum", "REAL DEFAULT 0.0"),
            ("prev_momentum_for_accel", "REAL DEFAULT 0.0"),
            ("vwap_num", "REAL DEFAULT 0.0"),
            ("vwap_den", "REAL DEFAULT 0.0"),
            ("last_cum_vol", "REAL DEFAULT 0.0"),
            ("fast_atr", f"REAL DEFAULT {ATR_SEED}"),
            ("medium_atr", f"REAL DEFAULT {ATR_SEED}"),
            ("slow_atr", f"REAL DEFAULT {ATR_SEED}"),
            ("trend_lock", "INTEGER DEFAULT 0"),
            ("min_hold", "INTEGER DEFAULT 0"),
            ("daily_trades", "INTEGER DEFAULT 0"),
            ("consecutive_losses", "INTEGER DEFAULT 0"),
            ("loss_cooldown_bars", "INTEGER DEFAULT 0"),
            ("exit_cooldown", "INTEGER DEFAULT 0"),
            ("spike_cooldown", "INTEGER DEFAULT 0"),
            ("last_trade_time", "INTEGER DEFAULT 0"),
            ("daily_pnl", "REAL DEFAULT 0.0"),
            ("last_trade_dir", "TEXT DEFAULT 'NONE'"),
            # v28 state fields
            ("stop_level", "REAL"),
            ("medium_atr_at_entry", "REAL DEFAULT 0.0"),
            ("favorable_extreme", "REAL DEFAULT 0.0"),
            ("initial_stop_dist", "REAL DEFAULT 0.0"),
            ("session_ref", "REAL"),
            ("session_high", "REAL"),
            ("session_low", "REAL"),
            ("tier_threshold", "REAL DEFAULT -1e9"),
        ]
        for col, dtype in cols:
            try:
                conn.execute(f"ALTER TABLE engine_state ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass


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
                    if ('records' in data and 'data' in data['records']
                            and len(data['records']['data']) > 0):
                        return data
        except Exception:
            pass
        time.sleep(2)
    return None


def store_snapshot_and_get_data(now: datetime.datetime):
    data = fetch_nse_data()
    if not data or 'records' not in data:
        return False

    spot = data['records']['underlyingValue']
    if spot < 10000 or spot > 40000:
        return False

    atm_strike = round(spot / 50) * 50
    target_strikes = [atm_strike - 100, atm_strike - 50, atm_strike,
                      atm_strike + 50, atm_strike + 100]
    ts_str = now.strftime('%Y-%m-%d %H:%M:00')
    ce_oi = pe_oi = ce_vol = pe_vol = 0

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
        conn.execute("UPDATE snapshots SET trading_status = ? WHERE timestamp = ?",
                     (status, ts_str))


def load_snapshots(limit=35):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(cur.fetchall())]


def load_state(today_str: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        state = dict(conn.execute("SELECT * FROM engine_state WHERE id=1").fetchone())

        if state.get('date') != today_str:
            # Daily reset — fresh start for the new trading day
            state.update({
                'date': today_str,
                'momentum_history': '[]',
                'trend_state': 'IDLE',
                'active_trade': 'NO_TRADE',
                'bars_in_trade': 0,
                'max_profit': 0.0,
                'entry_spot': 0.0,
                'prev_momentum': 0.0,
                'prev_momentum_for_accel': 0.0,
                'vwap_num': 0.0,
                'vwap_den': 0.0,
                'last_cum_vol': 0.0,
                'fast_atr': ATR_SEED,
                'medium_atr': ATR_SEED,
                'slow_atr': ATR_SEED,
                'trend_lock': 0,
                'min_hold': 0,
                'daily_trades': 0,
                'consecutive_losses': 0,
                'loss_cooldown_bars': 0,
                'exit_cooldown': 0,
                'spike_cooldown': 0,
                'last_trade_time': 0,
                'daily_pnl': 0.0,
                'last_trade_dir': 'NONE',
                # v28 fields reset
                'stop_level': None,
                'medium_atr_at_entry': 0.0,
                'favorable_extreme': 0.0,
                'initial_stop_dist': 0.0,
                'session_ref': None,
                'session_high': None,
                'session_low': None,
                'tier_threshold': -1e9,
            })

        # Deserialize momentum history from JSON
        try:
            state['momentum_history'] = json.loads(state.get('momentum_history', '[]'))
        except (TypeError, json.JSONDecodeError):
            state['momentum_history'] = []

        return state


def save_state(state):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''UPDATE engine_state SET
            date=?, active_trade=?, bars_in_trade=?, max_profit=?, entry_spot=?,
            momentum_history=?, prev_momentum=?, prev_momentum_for_accel=?,
            vwap_num=?, vwap_den=?, last_cum_vol=?,
            fast_atr=?, medium_atr=?, slow_atr=?,
            trend_lock=?, min_hold=?, daily_trades=?, consecutive_losses=?,
            loss_cooldown_bars=?, exit_cooldown=?, spike_cooldown=?,
            last_trade_time=?, daily_pnl=?, last_trade_dir=?,
            stop_level=?, medium_atr_at_entry=?, favorable_extreme=?, initial_stop_dist=?,
            session_ref=?, session_high=?, session_low=?, tier_threshold=?
            WHERE id=1''',
            (state['date'], state['active_trade'], state['bars_in_trade'],
             state.get('max_profit', 0.0), state.get('entry_spot', 0.0),
             json.dumps(state.get('momentum_history', [])),
             state.get('prev_momentum', 0.0), state.get('prev_momentum_for_accel', 0.0),
             state.get('vwap_num', 0.0), state.get('vwap_den', 0.0),
             state.get('last_cum_vol', 0.0),
             state.get('fast_atr', ATR_SEED),
             state.get('medium_atr', ATR_SEED),
             state.get('slow_atr', ATR_SEED),
             state.get('trend_lock', 0), state.get('min_hold', 0),
             state.get('daily_trades', 0), state.get('consecutive_losses', 0),
             state.get('loss_cooldown_bars', 0), state.get('exit_cooldown', 0),
             state.get('spike_cooldown', 0), state.get('last_trade_time', 0),
             state.get('daily_pnl', 0.0), state.get('last_trade_dir', 'NONE'),
             state.get('stop_level'),
             state.get('medium_atr_at_entry', 0.0),
             state.get('favorable_extreme', 0.0),
             state.get('initial_stop_dist', 0.0),
             state.get('session_ref'),
             state.get('session_high'),
             state.get('session_low'),
             state.get('tier_threshold', -1e9)))


def log_engine_run(ts, trend, status, printed, reason, raw):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO engine_log VALUES (?, ?, ?, ?, ?, ?)",
                     (ts, trend, status, int(printed), reason, raw))