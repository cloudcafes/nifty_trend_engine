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

# --------------------------------------------------------------
# The 5 strikes we capture around ATM (offsets in points)
# For Nifty, step size is 50 → ATM±100 gives us 5 strikes
# --------------------------------------------------------------
STRIKE_OFFSETS = [-100, -50, 0, 50, 100]  # relative to ATM

# Human-readable labels for the 5 strike positions (for columns)
STRIKE_LABELS = ["m100", "m50", "atm", "p50", "p100"]


# ==============================================================
# DATABASE INITIALIZATION
# ==============================================================

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        # -----------------------------------------------------
        # LEGACY TABLE (untouched — existing code continues to work)
        # -----------------------------------------------------
        conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            timestamp TEXT PRIMARY KEY, spot REAL, atm_strike REAL,
            ce_oi REAL, pe_oi REAL, ce_vol REAL, pe_vol REAL,
            trading_status TEXT DEFAULT 'NO_TRADE')''')

        # -----------------------------------------------------
        # NEW TABLE: per-strike OI and volume
        # -----------------------------------------------------
        # Columns: timestamp (PK), spot, atm_strike, expiry,
        #          ce_oi_m100, ce_oi_m50, ce_oi_atm, ce_oi_p50, ce_oi_p100,
        #          pe_oi_m100, pe_oi_m50, pe_oi_atm, pe_oi_p50, pe_oi_p100,
        #          ce_vol_m100, ce_vol_m50, ce_vol_atm, ce_vol_p50, ce_vol_p100,
        #          pe_vol_m100, pe_vol_m50, pe_vol_atm, pe_vol_p50, pe_vol_p100,
        #          ce_oi_total_chain, pe_oi_total_chain,
        #          underlying_change_pct
        cols_def = ["timestamp TEXT PRIMARY KEY",
                    "spot REAL",
                    "atm_strike REAL",
                    "expiry TEXT"]
        for s in ["ce_oi", "pe_oi", "ce_vol", "pe_vol"]:
            for lbl in STRIKE_LABELS:
                cols_def.append(f"{s}_{lbl} REAL DEFAULT 0")
        # Whole-chain totals (across ALL strikes of the nearest expiry)
        cols_def.append("ce_oi_total_chain REAL DEFAULT 0")
        cols_def.append("pe_oi_total_chain REAL DEFAULT 0")
        cols_def.append("underlying_change_pct REAL DEFAULT 0")

        conn.execute(f'''CREATE TABLE IF NOT EXISTS snapshots_detail (
            {", ".join(cols_def)}
        )''')

        # -----------------------------------------------------
        # engine_state table (unchanged, with idempotent column additions)
        # -----------------------------------------------------
        conn.execute('''CREATE TABLE IF NOT EXISTS engine_state (
            id INTEGER PRIMARY KEY CHECK (id = 1), date TEXT,
            trend_state TEXT DEFAULT 'IDLE', active_trade TEXT DEFAULT 'NO_TRADE')''')

        conn.execute('''INSERT OR IGNORE INTO engine_state
            (id, date, trend_state, active_trade) VALUES (1, '', 'IDLE', 'NO_TRADE')''')

        conn.execute('''CREATE TABLE IF NOT EXISTS engine_log (
            timestamp TEXT, trend TEXT, trading_status TEXT,
            signal_printed INTEGER, suppress_reason TEXT, raw_output TEXT)''')

        # Idempotent engine_state column additions
        cols = [
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
            ("stop_level", "REAL"),
            ("medium_atr_at_entry", "REAL DEFAULT 0.0"),
            ("favorable_extreme", "REAL DEFAULT 0.0"),
            ("initial_stop_dist", "REAL DEFAULT 0.0"),
            ("session_ref", "REAL"),
            ("session_high", "REAL"),
            ("session_low", "REAL"),
            ("tier_threshold", "REAL DEFAULT -1e9"),
            ("pcr_history", "TEXT DEFAULT '[]'"),
            ("entry_pcr", "REAL DEFAULT 1.0"),
        ]
        for col, dtype in cols:
            try:
                conn.execute(f"ALTER TABLE engine_state ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass


# ==============================================================
# NSE DATA FETCH
# ==============================================================

def fetch_nse_data():
    symbol = "NIFTY"
    base_url = "https://www.nseindia.com"
    for attempt in range(3):
        try:
            session.get(base_url, timeout=10)
            exp_resp = session.get(
                f"{base_url}/api/option-chain-contract-info?symbol={symbol}",
                timeout=10
            )
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
                        data['_expiry'] = nearest_expiry
                        return data
        except Exception:
            pass
        time.sleep(2)
    return None


# ==============================================================
# STORE SNAPSHOT
# ==============================================================

def store_snapshot_and_get_data(now: datetime.datetime):
    data = fetch_nse_data()
    if not data or 'records' not in data:
        return False

    spot = data['records'].get('underlyingValue', 0)
    if spot < 10000 or spot > 40000:
        return False

    atm_strike = round(spot / 50) * 50
    ts_str = now.strftime('%Y-%m-%d %H:%M:00')
    expiry = data.get('_expiry', '')

    # The 5 target strikes we care about for per-strike tracking
    target_strikes = {atm_strike + off: STRIKE_LABELS[i]
                      for i, off in enumerate(STRIKE_OFFSETS)}

    # Initialize storage dicts
    per_strike = {}
    for s in ["ce_oi", "pe_oi", "ce_vol", "pe_vol"]:
        for lbl in STRIKE_LABELS:
            per_strike[f"{s}_{lbl}"] = 0.0

    # Aggregated totals (for legacy snapshots table — kept identical to old behavior)
    legacy_ce_oi = legacy_pe_oi = legacy_ce_vol = legacy_pe_vol = 0.0

    # Whole-chain totals (across ALL strikes)
    ce_oi_total_chain = pe_oi_total_chain = 0.0

    for row in data['records']['data']:
        strike = row.get('strikePrice')
        if strike is None:
            continue

        ce = row.get('CE', {}) or {}
        pe = row.get('PE', {}) or {}

        ce_oi_v  = ce.get('openInterest', 0) or 0
        pe_oi_v  = pe.get('openInterest', 0) or 0
        ce_vol_v = ce.get('totalTradedVolume', 0) or 0
        pe_vol_v = pe.get('totalTradedVolume', 0) or 0

        # Whole-chain totals
        ce_oi_total_chain += ce_oi_v
        pe_oi_total_chain += pe_oi_v

        # If this strike is one of our target strikes, populate per-strike fields
        if strike in target_strikes:
            lbl = target_strikes[strike]
            per_strike[f"ce_oi_{lbl}"]  = ce_oi_v
            per_strike[f"pe_oi_{lbl}"]  = pe_oi_v
            per_strike[f"ce_vol_{lbl}"] = ce_vol_v
            per_strike[f"pe_vol_{lbl}"] = pe_vol_v

            # Also contribute to the legacy aggregate (same formula as old code)
            legacy_ce_oi  += ce_oi_v
            legacy_pe_oi  += pe_oi_v
            legacy_ce_vol += ce_vol_v
            legacy_pe_vol += pe_vol_v

    underlying_change_pct = data['records'].get('underlyingChangePct', 0) or 0

    with sqlite3.connect(DB_NAME) as conn:
        # --- Legacy table insert (keeps existing code working) ---
        conn.execute('''INSERT OR REPLACE INTO snapshots
            (timestamp, spot, atm_strike, ce_oi, pe_oi, ce_vol, pe_vol, trading_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (ts_str, spot, atm_strike,
             legacy_ce_oi, legacy_pe_oi, legacy_ce_vol, legacy_pe_vol,
             'NO_TRADE'))

        # --- New detail table insert ---
        cols = ["timestamp", "spot", "atm_strike", "expiry"]
        vals = [ts_str, spot, atm_strike, expiry]

        for s in ["ce_oi", "pe_oi", "ce_vol", "pe_vol"]:
            for lbl in STRIKE_LABELS:
                key = f"{s}_{lbl}"
                cols.append(key)
                vals.append(per_strike[key])

        cols.append("ce_oi_total_chain")
        vals.append(ce_oi_total_chain)
        cols.append("pe_oi_total_chain")
        vals.append(pe_oi_total_chain)
        cols.append("underlying_change_pct")
        vals.append(underlying_change_pct)

        placeholders = ", ".join(["?"] * len(vals))
        colnames = ", ".join(cols)
        conn.execute(
            f"INSERT OR REPLACE INTO snapshots_detail ({colnames}) VALUES ({placeholders})",
            vals
        )

    return ts_str


# ==============================================================
# UPDATE SNAPSHOT STATUS (legacy)
# ==============================================================

def update_snapshot_status(ts_str, status):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE snapshots SET trading_status = ? WHERE timestamp = ?",
                     (status, ts_str))


# ==============================================================
# LOAD SNAPSHOTS
# ==============================================================

def load_snapshots(limit=35):
    """
    Legacy load: returns aggregated rows from `snapshots`.
    Kept for backward compatibility with current compute.py / classify.py.
    """
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(cur.fetchall())]


def load_snapshots_detail(limit=35):
    """
    New load: returns per-strike detail rows from `snapshots_detail`.
    Use this for the new OI-writer-capitulation analysis.
    """
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM snapshots_detail ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(cur.fetchall())]


# ==============================================================
# STATE LOAD / SAVE (unchanged except for pcr_history + entry_pcr)
# ==============================================================

def load_state(today_str: str):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        state = dict(conn.execute("SELECT * FROM engine_state WHERE id=1").fetchone())

        if state.get('date') != today_str:
            state.update({
                'date': today_str,
                'momentum_history': '[]',
                'pcr_history': '[]',
                'trend_state': 'IDLE',
                'active_trade': 'NO_TRADE',
                'bars_in_trade': 0,
                'max_profit': 0.0,
                'entry_spot': 0.0,
                'entry_pcr': 1.0,
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
                'stop_level': None,
                'medium_atr_at_entry': 0.0,
                'favorable_extreme': 0.0,
                'initial_stop_dist': 0.0,
                'session_ref': None,
                'session_high': None,
                'session_low': None,
                'tier_threshold': -1e9,
            })

        # Deserialize history fields
        try:
            state['momentum_history'] = json.loads(state.get('momentum_history', '[]'))
        except (TypeError, json.JSONDecodeError):
            state['momentum_history'] = []

        try:
            state['pcr_history'] = json.loads(state.get('pcr_history', '[]'))
        except (TypeError, json.JSONDecodeError):
            state['pcr_history'] = []

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
            session_ref=?, session_high=?, session_low=?, tier_threshold=?,
            pcr_history=?, entry_pcr=?
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
             state.get('tier_threshold', -1e9),
             json.dumps(state.get('pcr_history', [])),
             state.get('entry_pcr', 1.0)))


# ==============================================================
# LOG
# ==============================================================

def log_engine_run(ts, trend, status, printed, reason, raw):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO engine_log VALUES (?, ?, ?, ?, ?, ?)",
                     (ts, trend, status, int(printed), reason, raw))