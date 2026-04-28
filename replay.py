import sqlite3
import datetime
from config import DB_NAME, FETCH_INTERVAL_MINUTES
from compute import compute_metrics
from classify import process_engine_step

# ==========================================
# REPLAY CONFIGURATION
# ==========================================
# Change this date to replay any day stored in your nifty.db
TARGET_DATE = '2026-04-24'  

def get_fresh_state(target_date):
    """Creates a temporary in-memory state mapped perfectly to the Universal OI Trend logic."""
    return {
        'date': target_date,
        'active_trade': 'NO_TRADE',
        'entry_spot': 0.0,
        'max_profit': 0.0,
        'daily_pnl': 0.0,
        'call_bias_bars': 0,
        'put_bias_bars': 0,
        'pcr_strong_call_bars': 0,
        'pcr_weak_call_bars': 0,
        'oi_bias_history': [],
        'trend_direction': 'NEUTRAL',
        'consecutive_opposite_bias': 0
    }

def fetch_day_snapshots(target_date):
    """Fetches all legacy snapshots for the target date in chronological order."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM snapshots WHERE timestamp LIKE ? ORDER BY timestamp ASC", (f"{target_date}%",))
        return [dict(r) for r in cur.fetchall()]

def print_replay_output(ts_str, classification, metrics, state):
    """Safely prints the newly formatted output to the console."""
    price = metrics.get("price", 0.0)
    momentum = metrics.get("momentum", 0.0)
    sbias = metrics.get("str_bias", "NONE")
    tbias = metrics.get("tac_bias", "NONE")
    pcr = metrics.get("pcr_now", 1.0)
    oi_bias = metrics.get("oi_bias", "NEUTRAL")

    action = classification.get("action", "NO_TRADE")
    signal = classification.get("signal", "NO_TRADE")
    reason = classification.get("reason", "NONE")
    score = classification.get("score", 0)
    pnl = classification.get("pnl", 0.0)
    trend = classification.get("trend", state.get("trend_direction", "NEUTRAL"))
    
    active_trade = state.get("active_trade", "NO_TRADE")
    daily_pnl = state.get("daily_pnl", 0.0)
    consec_opp = state.get("consecutive_opposite_bias", 0)

    hh_mm = ts_str.split(" ")[1][:5]
    columns_header = "TIME  | SPOT  | TREND        | ACTION  | SIGNAL | PNL  | SCORE"
    main_line = f"{hh_mm} | {price:.0f} | {trend:<12} | {action:<7} | {signal:<6} | {pnl:+.1f} | {score}"

    diag_lines = [
        f"MOMENTUM: {momentum:.4f} | STR_BIAS: {sbias} | TAC_BIAS: {tbias}",
        f"PCR: {pcr:.2f} | OI_BIAS: {oi_bias} | TREND_FILTER: {trend}",
        f"PERSISTENCE: Call({state.get('call_bias_bars',0)}) Put({state.get('put_bias_bars',0)}) | PCR_Strong_Call({state.get('pcr_strong_call_bars',0)})",
        f"REASON: {reason} | DAILY PNL: {daily_pnl:.1f} | CONSEC_OPP_OI: {consec_opp} bars"
    ]
    
    if active_trade != "NO_TRADE":
        diag_lines.append(f"TRADE: {active_trade} | MAX PROFIT: {state.get('max_profit', 0.0):.1f}")
    
    diag_lines.append("-" * 80)

    print(columns_header)
    print(main_line)
    print("\n".join(diag_lines))

def run_replay():
    print(f"=== INITIATING BACKTEST REPLAY FOR {TARGET_DATE} ===\n")
    
    day_data = fetch_day_snapshots(TARGET_DATE)
    if not day_data:
        print(f"No data found for {TARGET_DATE} in {DB_NAME}. Check the date format (YYYY-MM-DD).")
        return

    # Initialize the temporary ghost state
    state = get_fresh_state(TARGET_DATE)
    rolling_window = []
    last_run_minute = None

    # Stream the data through the engine exactly like a live market
    for row in day_data:
        ts_str = row['timestamp']
        dt_obj = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        t = dt_obj.time()
        curr_minute = dt_obj.minute

        # Standard Market Hour Filter
        if t >= datetime.time(15, 30) or t < datetime.time(9, 15):
            continue
            
        force_exit_only = (t >= datetime.time(14, 45))

        # Enforce exact interval processing for legacy 1-min DBs
        if curr_minute % FETCH_INTERVAL_MINUTES == 0 and curr_minute != last_run_minute:
            last_run_minute = curr_minute
            
            rolling_window.append(row)

            # Cap rolling window at 10 to match memory overhead limits
            if len(rolling_window) > 10:
                rolling_window.pop(0)

            # Engine requires 5 bars of warmup before generating momentum/bias metrics
            if len(rolling_window) < 5:
                print(f"{ts_str.split(' ')[1][:5]} | --- WARMING UP ({len(rolling_window)}/5 BARS) ---")
                continue

            metrics = compute_metrics(rolling_window, state)
            if not metrics:
                continue

            # Process the mathematical logic for this bar
            classification = process_engine_step(metrics, state, t, force_exit_only)

            # Print the exact console output
            print_replay_output(ts_str, classification, metrics, state)

    print(f"=== REPLAY COMPLETE | NET PNL: {state.get('daily_pnl', 0.0):.1f} ===")

if __name__ == "__main__":
    run_replay()