import sqlite3
import datetime
from config import DB_NAME, ATR_SEED
from compute import compute_metrics
from classify import process_engine_step
from output import compute_current_pnl

# ==========================================
# REPLAY CONFIGURATION
# ==========================================
# Change this date to replay any day stored in your nifty.db
TARGET_DATE = '2026-04-24'  

def get_fresh_state(target_date):
    """Creates a temporary in-memory state, identical to a fresh morning start."""
    return {
        'date': target_date, 'momentum_history': [], 'trend_state': 'IDLE', 'active_trade': 'NO_TRADE',
        'bars_in_trade': 0, 'max_profit': 0.0, 'entry_spot': 0.0, 'prev_momentum': 0.0,
        'prev_momentum_for_accel': 0.0, 'vwap_num': 0.0, 'vwap_den': 0.0, 'last_cum_vol': 0.0,
        'fast_atr': ATR_SEED, 'medium_atr': ATR_SEED, 'slow_atr': ATR_SEED, 'trend_lock': 0,
        'min_hold': 0, 'daily_trades': 0, 'consecutive_losses': 0, 'loss_cooldown_bars': 0,
        'exit_cooldown': 0, 'spike_cooldown': 0, 'last_trade_time': 0, 'daily_pnl': 0.0,
        'last_trade_dir': 'NONE'
    }

def fetch_day_snapshots(target_date):
    """Fetches all 1-minute snapshots for the target date in chronological order."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        # The LIKE operator matches the date prefix. ASC ensures chronological order.
        cur = conn.execute("SELECT * FROM snapshots WHERE timestamp LIKE ? ORDER BY timestamp ASC", (f"{target_date}%",))
        return [dict(r) for r in cur.fetchall()]

def print_replay_output(ts_str, classification, metrics, state):
    """Safely prints the formatted output to console without touching text files."""
    price, momentum = metrics["price"], metrics["momentum"]
    regime, vwap = metrics.get("regime", "UNKNOWN"), metrics.get("vwap", price)
    sbias, tbias = metrics.get("structural_bias", "NONE"), metrics.get("tactical_bias", "NONE")

    action, signal = classification.get("action", "NO_TRADE"), classification.get("signal", "NO_TRADE")
    reason, strike, score = classification.get("reason", "NONE"), classification.get("strike", "N/A"), classification.get("score", 0)

    active_trade, daily_pnl = state.get("active_trade", "NO_TRADE"), state.get("daily_pnl", 0.0)

    if active_trade != "NO_TRADE":
        pnl = compute_current_pnl(price, state)
        max_pnl = state.get("max_profit", 0.0)
        dd = (max_pnl - pnl) / max_pnl if max_pnl > 5.0 else 0.0
    else: 
        pnl, max_pnl, dd = 0.0, 0.0, 0.0

    hh_mm = ts_str.split(" ")[1][:5]
    columns_header = "TIME  | SPOT  | REGIME   | ACTION  | SIGNAL | SCORE  | OPT_PNL | DD"
    main_line = f"{hh_mm} | {price:.0f} | {regime:<8} | {action:<7} | {signal:<6} | {score:<6} | {pnl:+.1f}   | {dd:.0%}"

    diag_lines = [
        f"MOM:{momentum:.6f} | ACCEL:{metrics.get('accel',0):.6f} | FAST_ATR:{metrics.get('fast_atr',0):.1f} ({metrics.get('atr_pct',0):.4%})",
        f"EXP_MOVE:{metrics.get('expected_move',0):.1f} | PROG:{metrics.get('move_progress',0):.2f} | RNG_10:{metrics.get('range_10',0):.1f}",
        f"BIAS (STR/TAC): {sbias}/{tbias} | VWAP:{vwap:.1f} | VOL_SPIKE:{'YES' if metrics.get('vol_spike') else 'NO'}",
        f"PCR:{metrics.get('pcr',1):.2f} | PCR_DELTA:{metrics.get('pcr_delta',0):.3f} | OI_BIAS:{metrics.get('oi_bias','NONE')} | EXT_UP:{'YES' if metrics.get('ext_up') else 'NO'} | EXT_DN:{'YES' if metrics.get('ext_down') else 'NO'}",
        f"ENTRY/EXIT REASON: {reason} | DAILY_TRADES: {state.get('daily_trades', 0)}/UNCAPPED | DAILY_PNL: {daily_pnl:.1f}"
    ]
    
    if action == "ENTRY": 
        diag_lines.append(f"SUGGESTED_STRIKE: {strike}")
    
    if active_trade != "NO_TRADE":
        diag_lines.append(f"TRADE:{active_trade} | BARS:{state.get('bars_in_trade', 0)} | MAX_PNL:{max_pnl:.1f} | CUR_PNL:{pnl:.1f} | LOCK:{state.get('trend_lock',0)} | HOLD:{state.get('min_hold',0)}")
    
    diag_lines.append("-" * 80)

    print(columns_header)
    print(main_line)
    print("\n".join(diag_lines))

def run_replay():
    print(f"=== INITIATING BACKTEST REPLAY FOR {TARGET_DATE} ===\n")
    
    day_data = fetch_day_snapshots(TARGET_DATE)
    if not day_data:
        print(f"No data found for {TARGET_DATE} in {DB_NAME}. Check the date format (YYYY-MM-DD) or ensure the engine ran on this day.")
        return

    # Initialize the temporary ghost state
    state = get_fresh_state(TARGET_DATE)
    rolling_window = []

    # Stream the data through the engine exactly like a live market
    for row in day_data:
        rolling_window.append(row)
        
        # Engine computes metrics using maximum of last 35 bars
        if len(rolling_window) > 35:
            rolling_window.pop(0)

        ts_str = row['timestamp']
        dt_obj = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        t = dt_obj.time()

        # Engine requires 15 bars of warmup before generating metrics
        if len(rolling_window) < 15:
            print(f"{ts_str.split(' ')[1][:5]} | --- WARMING UP ({len(rolling_window)}/15 BARS) ---")
            continue

        metrics = compute_metrics(rolling_window, state)
        if not metrics:
            continue

        # EOD Cutoff Logic
        if t >= datetime.time(15, 30):
            print(f"{ts_str.split(' ')[1][:5]} | --- MARKET CLOSED ---")
            break
            
        force_exit_only = False
        if t >= datetime.time(14, 45):
            if state.get('active_trade', 'NO_TRADE') == 'NO_TRADE':
                print(f"{ts_str.split(' ')[1][:5]} | NO NEW ENTRIES AFTER 14:45")
                continue
            else:
                force_exit_only = True

        # Process the mathematical logic for this minute
        classification = process_engine_step(metrics, state, t, force_exit_only)

        # Print the exact console output you are used to seeing
        print_replay_output(ts_str, classification, metrics, state)

    print(f"=== REPLAY COMPLETE | NET PNL: {state.get('daily_pnl', 0.0):.1f} ===")

if __name__ == "__main__":
    run_replay()