import time
import datetime
import threading
from config import IST, FETCH_INTERVAL_MINUTES, NSE_HOLIDAYS
from data import init_db, store_snapshot_and_get_data, update_snapshot_status, load_snapshots, load_state, save_state, log_engine_run
from compute import compute_metrics
from classify import classify_signal
from output import process_output
from ai_notify import trigger_ai_and_telegram

def main():
    init_db()
    print("Nifty Trend Engine v2 Started (Threaded AI & Internal Scheduler)\n")
    last_run_minute = None

    while True:
        now = datetime.datetime.now(IST)
        today_str = now.strftime('%Y-%m-%d')
        
        # ==========================================
        # INTERNAL SCHEDULER
        # ==========================================
        
        # 1. Weekend Check (0=Monday ... 5=Saturday, 6=Sunday)
        if now.weekday() >= 5: 
            time.sleep(3600) # Sleep 1 hour
            continue

        # 2. Holiday Check
        if today_str in NSE_HOLIDAYS:
            time.sleep(3600) # Sleep 1 hour
            continue

        # 3. Market Hours Check (9:15 AM to 3:30 PM)
        market_open = datetime.time(9, 15)
        market_close = datetime.time(15, 30)
        
        if not (market_open <= now.time() <= market_close):
            time.sleep(60) # Sleep 1 minute
            continue

        # ==========================================
        # ENGINE EXECUTION PIPELINE
        # ==========================================
        
        curr_minute = now.minute

        if curr_minute % FETCH_INTERVAL_MINUTES == 0 and curr_minute != last_run_minute:
            last_run_minute = curr_minute
            
            ts_str = store_snapshot_and_get_data(now)
            if not ts_str:
                continue

            snapshots = load_snapshots(15)
            if len(snapshots) < 8:
                continue

            if now.time() <= datetime.time(9, 20):
                continue
            
            today_str = now.strftime('%Y-%m-%d')
            state = load_state(today_str)
            
            metrics = compute_metrics(snapshots, state)
            if not metrics: continue

            final_signal = classify_signal(metrics)

            printed, reason, raw_output, trading_status = process_output(ts_str, final_signal, metrics, state)

            update_snapshot_status(ts_str, trading_status)
            log_engine_run(ts_str, final_signal, trading_status, printed, reason, raw_output)

            # Update State Memory
            state['last_trend'] = final_signal
            state['last_accel'] = metrics['accel']
            state['last_oi_label'] = metrics['oi_label']
            state['last_vol_label'] = metrics['vol_label']
            state['prev_raw_trend'] = metrics['raw_trend']
            state['last_trading_status'] = trading_status
            
            state['slope_history'].append(metrics['fast_slope'])
            if len(state['slope_history']) > 5: 
                state['slope_history'].pop(0)

            save_state(state)

            # ==========================================
            # NON-INTERFERING ASYNC AI & TELEGRAM TRIGGER
            # ==========================================
            if printed and trading_status in ["HIGH_PROB_CALL", "HIGH_PROB_PUT"]:
                print(f"*** TRADE TRIGGERED: {trading_status} ***")
                print("Launching AI Analysis and Telegram alert in background...")
                # Daemon=True ensures this runs completely detached from the 1-minute loop
                threading.Thread(target=trigger_ai_and_telegram, daemon=True).start()

        time.sleep(1)

if __name__ == "__main__":
    main()