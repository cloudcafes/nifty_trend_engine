import time
import datetime
import threading
from config import IST, FETCH_INTERVAL_MINUTES, NSE_HOLIDAYS
from data import init_db, store_snapshot_and_get_data, update_snapshot_status, load_snapshots, load_state, save_state, log_engine_run
from compute import compute_metrics
from classify import process_engine_step
from output import process_output
from ai_notify import trigger_ai_and_telegram
import sqlite3

def main():
   conn = sqlite3.connect("nifty.db")
   conn.execute("DELETE FROM snapshots WHERE spot <= 0")
   conn.commit()
   conn.close()
   print("Nifty Trend Engine v21.4 Started (Stop Limits & Threshold Optimization)\n")
   init_db()
   last_run_minute = None

   while True:
      now = datetime.datetime.now(IST)
      today_str = now.strftime('%Y-%m-%d')
      t = now.time()

      if now.weekday() >= 5 or today_str in NSE_HOLIDAYS: 
         time.sleep(3600)
         continue

      if t >= datetime.time(15, 30):
         time.sleep(300)
         continue
         
      if t < datetime.time(9, 0):
         time.sleep(60)
         continue
      
      force_exit_only = False
      if t >= datetime.time(15, 15):
         state = load_state(today_str)
         if state.get("active_trade") == "NO_TRADE":
            print(f"{now.strftime('%H:%M')} | --- NO NEW TRADES AFTER 15:15 ---")
            time.sleep(60)
            continue
         else:
            force_exit_only = True 

      curr_minute = now.minute
      if curr_minute % FETCH_INTERVAL_MINUTES == 0 and curr_minute != last_run_minute:
         last_run_minute = curr_minute
         
         ts_str = store_snapshot_and_get_data(now)
         
         if not ts_str: 
            print(f"{now.strftime('%H:%M')} | --- NSE API FETCH FAILED / TIMEOUT ---")
            continue

         snapshots = load_snapshots(35) 
         
         if len(snapshots) < 15: 
            print(f"{now.strftime('%H:%M')} | --- WAITING FOR MORE DATA ({len(snapshots)}/15 BARS) ---")
            continue

         state = load_state(today_str)
         
         metrics = compute_metrics(snapshots, state)
         if not metrics: continue

         classification = process_engine_step(metrics, state, t, force_exit_only)
         final_signal = classification['signal']
         action = classification['action']

         printed, reason, raw_output, trading_status = process_output(ts_str, classification, metrics, state)

         update_snapshot_status(ts_str, trading_status)
         log_engine_run(ts_str, final_signal, trading_status, printed, reason, raw_output)

         save_state(state)

         is_trade_trigger = printed and action in ["ENTRY", "EXIT"]
         
         is_periodic_update = (curr_minute in [15, 45]) and (t >= datetime.time(9, 45))

         if is_trade_trigger or is_periodic_update:
            if is_trade_trigger:
               print(f"*** NEW TELEGRAM SIGNAL: {trading_status} ***")
            else:
               print(f"*** SCHEDULED MARKET PERSPECTIVE: {now.strftime('%H:%M')} ***")
               
            threading.Thread(target=trigger_ai_and_telegram, args=(is_periodic_update,), daemon=True).start()

      time.sleep(1)

if __name__ == "__main__":
   main()