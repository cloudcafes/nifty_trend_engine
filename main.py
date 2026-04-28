import time
import datetime
import threading
from config import IST, FETCH_INTERVAL_MINUTES, NSE_HOLIDAYS
from data import init_db, store_snapshot_and_get_data, load_snapshots, load_state, save_state
from compute import compute_metrics
from classify import process_engine_step
from output import process_output
from ai_notify import trigger_ai_and_telegram, validate_gemini_model_on_startup

def main():
    print("Nifty Trend Engine v32.0 (Strict PCR-OI 3-Bar Implementation Started)\n")
    init_db()
    validate_gemini_model_on_startup()
    
    last_run_minute = None

    while True:
        now = datetime.datetime.now(IST)
        today_str = now.strftime('%Y-%m-%d')
        t = now.time()

        # Skip Weekends & Holidays
        if now.weekday() >= 5 or today_str in NSE_HOLIDAYS: 
            time.sleep(3600); continue

        # Standard Market Hour Filter
        if t >= datetime.time(15, 30) or t < datetime.time(9, 15):
            time.sleep(60); continue
        
        # Stop new entries before market close
        force_exit_only = (t >= datetime.time(14, 45))
        curr_minute = now.minute

        # Execute exactly on the 3-minute interval
        if curr_minute % FETCH_INTERVAL_MINUTES == 0 and curr_minute != last_run_minute:
            last_run_minute = curr_minute
            
            ts_str = store_snapshot_and_get_data(now)
            if not ts_str:
                print(f"{now.strftime('%H:%M')} | --- NSE API FETCH FAILED ---")
                continue

            # Need minimum data to calculate momentum/bias
            snapshots = load_snapshots(10) 
            if len(snapshots) < 5: 
                print(f"{now.strftime('%H:%M')} | --- WARMING UP ({len(snapshots)}/5 BARS) ---")
                continue

            state = load_state(today_str)
            metrics = compute_metrics(snapshots, state)
            
            if metrics:
                classification = process_engine_step(metrics, state, t, force_exit_only)
                printed, trading_status = process_output(ts_str, classification, metrics, state)
                save_state(state)

                if printed:
                    # Triggered when Action is ENTRY or EXIT
                    print(f"*** NEW SIGNAL TRIGGERED: {trading_status} ***")
                    threading.Thread(target=trigger_ai_and_telegram, args=("TRADE_SIGNAL", classification, ts_str, metrics), daemon=True).start()
                
                # --- NEW SCHEDULED 30-MINUTE AI MARKET UPDATE ---
                # Triggers at xx:00 and xx:30 between 09:30 and 15:00 (only if a trade signal wasn't just sent)
                elif curr_minute % 30 == 0 and datetime.time(9, 30) <= t <= datetime.time(15, 0):
                    print(f"*** SCHEDULED 30-MIN MARKET PERSPECTIVE TRIGGERED ***")
                    threading.Thread(target=trigger_ai_and_telegram, args=("SCHEDULED_UPDATE", classification, ts_str, metrics), daemon=True).start()

        time.sleep(1)

if __name__ == "__main__":
    main()