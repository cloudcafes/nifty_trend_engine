from config import RECENT_SNAP_FILE, SLIPPAGE

def compute_current_pnl(price, state):
   trade = state.get("active_trade", "NO_TRADE")
   if trade == "NO_TRADE": return 0.0
   
   entry = state.get("entry_spot", price)
   if trade == "CALL": 
      return (price - SLIPPAGE) - (entry + SLIPPAGE)
   if trade == "PUT": 
      return (entry - SLIPPAGE) - (price + SLIPPAGE)
   return 0.0

def process_output(ts_str, classification, metrics, state):
   price = metrics["price"]
   momentum = metrics["momentum"]
   regime = metrics.get("regime", "UNKNOWN")
   vwap = metrics.get("vwap", price)
   bias = metrics.get("bias", "NONE")

   action = classification.get("action", "NO_TRADE")
   signal = classification.get("signal", "NO_TRADE")
   reason = classification.get("reason", "NONE")
   strike = classification.get("strike", "N/A")
   score = classification.get("score", 0)

   active_trade = state.get("active_trade", "NO_TRADE")

   type_str = reason[:6] if action == "ENTRY" else "-"

   if active_trade != "NO_TRADE":
      pnl = compute_current_pnl(price, state)
      max_pnl = state.get("max_profit", 0.0)
      
      if max_pnl > 5.0:
         dd = (max_pnl - pnl) / max_pnl
      else:
         dd = 0.0
   else:
      pnl = 0.0
      dd = 0.0
      max_pnl = 0.0

   hh_mm = ts_str.split(" ")[1][:5]
   columns_header = "TIME  | SPOT  | REGIME   | ACTION  | SIGNAL | SCORE  | PNL  | DD"
   
   main_line = (
      f"{hh_mm} | {price:.0f} | {regime:<8} | {action:<7} | "
      f"{signal:<6} | {score:<6} | {pnl:+.0f} | {dd:.0%}"
   )

   diag_lines = [
      f"MOM:{momentum:.6f} | EXP_MOVE:{metrics.get('expected_move',0):.1f} | PROG:{metrics.get('move_progress',0):.2f}",
      f"BIAS:{bias} | RNG_10:{metrics.get('range_10',0):.1f}",
      f"VOL_SPIKE:{'YES' if metrics['vol_spike'] else 'NO'} | VWAP:{vwap:.1f}",
      f"ENTRY/EXIT REASON: {reason} | DAILY_TRADES: {state.get('daily_trades', 0)}/UNCAPPED"
   ]
   
   if action == "ENTRY":
      diag_lines.append(f"SUGGESTED_STRIKE: {strike}")
   
   if active_trade != "NO_TRADE":
      diag_lines.append(
         f"TRADE:{active_trade} | BARS:{state.get('bars_in_trade', 0)} | "
         f"MAX_PNL:{max_pnl:.1f} | CUR_PNL:{pnl:.1f} | "
         f"LOCK:{state.get('trend_lock',0)} | HOLD:{state.get('min_hold',0)}"
      )
   diag_lines.append("-" * 80)

   debug_block = "\n".join(diag_lines)

   trading_status = "NO_TRADE"
   if action == "ENTRY":
      trading_status = f"{signal}_{reason}"
   elif active_trade != "NO_TRADE":
      trading_status = f"HOLD_{active_trade}"

   should_print = True
   action_reason = "MAINTAINING_STATE" if action in ["HOLD", "NO_TRADE"] else ""

   with open(RECENT_SNAP_FILE, "w", encoding='utf-8') as f:
      f.write(columns_header + "\n")
      f.write(main_line + "\n\n")
      f.write(debug_block + "\n")

   print(columns_header)
   print(main_line)
   print(debug_block)

   return should_print, action_reason, main_line, trading_status