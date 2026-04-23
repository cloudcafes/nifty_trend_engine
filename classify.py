from config import SLIPPAGE

def count_same_sign(hist):
   if not hist: return 0
   sign = 1 if hist[-1] > 0 else -1
   count = 0
   for val in reversed(hist):
      if (val > 0 and sign > 0) or (val < 0 and sign < 0): count += 1
      else: break
   return count

def compute_pnl(entry, exit_price, trade_type):
   if trade_type == "CALL":
      real_entry = entry + SLIPPAGE
      real_exit = exit_price - SLIPPAGE
      return real_exit - real_entry
   elif trade_type == "PUT":
      real_entry = entry - SLIPPAGE
      real_exit = exit_price + SLIPPAGE
      return real_entry - real_exit
   return 0.0

# --- LAYER 1: REGIME DETECTION ---
def detect_regime(metrics):
   if metrics["atr_pct"] < 0.00015:
      # FIX: Prevent LOW_VOL from blocking clear momentum moves
      if abs(metrics["momentum"]) > 0.5:
         return "TRANSITION"
      return "LOW_VOL"
   if metrics["range_10"] > 20 and abs(metrics["momentum"]) > 0.4:
      return "TRENDING"
   if metrics["range_10"] < 15 and abs(metrics["momentum"]) < 0.3:
      return "CHOPPY"
   return "TRANSITION"

def dynamic_thresholds(metrics):
   vol = metrics["atr_pct"]
   # FIX: Raised entry scoring thresholds to filter out weak setups
   if vol > 0.0003:
      return {"momentum": 0.4, "score_threshold": 60}
   else:
      return {"momentum": 0.25, "score_threshold": 55}

def detect_breakout_pattern(prices):
   if len(prices) < 15: return False, "NONE"
   
   p_break = prices[-3]
   p_follow = prices[-2]
   p_curr = prices[-1]
   
   high_10 = max(prices[-13:-3])
   low_10 = min(prices[-13:-3])
   
   if p_break > high_10 and p_follow > p_break and p_curr >= high_10:  
      return True, "CALL"
   
   if p_break < low_10 and p_follow < p_break and p_curr <= low_10:  
      return True, "PUT"
               
   return False, "NONE"

# --- LAYER 2: SCORING ENGINE ---
def compute_score(metrics):
   score = 0
   direction = "CALL" if metrics["momentum"] > 0 else "PUT"

   if abs(metrics["momentum"]) > 0.6: score += 30
   elif abs(metrics["momentum"]) > 0.3: score += 20

   breakout, bo_dir = detect_breakout_pattern(metrics["prices"])
   if breakout:
      score += 25
      direction = bo_dir

   if direction == "CALL" and metrics["price"] > metrics["vwap"]: score += 15
   elif direction == "PUT" and metrics["price"] < metrics["vwap"]: score += 15

   if metrics["vol_spike"]: score += 10
   if metrics["range_10"] > 15: score += 10
   if abs(metrics["accel"]) > 0.2: score += 10

   if direction == "PUT" and metrics.get("pcr", 1) > 1.2: score += 10
   if direction == "CALL" and metrics.get("pcr", 1) < 0.8: score += 10
   if direction == metrics.get("oi_bias", "NONE"): score += 10

   return score, direction

def get_trade_decision(metrics, regime):
   score, direction = compute_score(metrics)
   thresh = dynamic_thresholds(metrics)

   bias = metrics.get("bias", "NONE")
   if bias == "PUT_BIAS" and direction == "CALL": return "NO_TRADE", score
   if bias == "CALL_BIAS" and direction == "PUT": return "NO_TRADE", score

   if direction == "CALL" and metrics.get("overextended_up", False): 
      return "NO_TRADE", score
   if direction == "PUT" and metrics.get("overextended_down", False): 
      return "NO_TRADE", score

   # TRENDING and TRANSITION both enabled with correct filters
   if regime == "TRENDING":
      if score >= thresh["score_threshold"]:
         if metrics.get("move_progress", 0) < 1.2: 
            return direction, score

   elif regime == "TRANSITION":
      if score >= thresh["score_threshold"] + 15:
         if metrics.get("move_progress", 0) < 0.8: 
            return direction, score

   return "NO_TRADE", score

# --- LAYER 3: EXECUTION & RISK ENGINE ---
def should_hold(state, metrics):
   if state.get("min_hold", 0) > 0:
      state["min_hold"] -= 1
      return True
   if state.get("trend_lock", 0) > 0:
      state["trend_lock"] -= 1
      return True
   trade = state.get("active_trade")
   price = metrics["price"]
   vwap = metrics["vwap"]
   
   if trade == "CALL" and price > vwap: return True
   if trade == "PUT" and price < vwap: return True
   return False

def should_exit_structural(metrics, state, persistence):
   trade = state.get("active_trade", "NO_TRADE")
   price = metrics["price"]
   vwap = metrics["vwap"]
   momentum = metrics["momentum"]

   if trade == "CALL" and price < vwap and momentum < 0 and persistence < 2: return True
   if trade == "PUT" and price > vwap and momentum > 0 and persistence < 2: return True
   return False

def should_exit_no_progress(state, pnl, metrics):
   bars = state.get("bars_in_trade", 0)
   momentum = metrics.get("momentum", 0)
   trade = state.get("active_trade", "NO_TRADE")
   
   if bars >= 5 and pnl <= 0: return True
   
   if bars >= 3 and pnl <= 0:
      if trade == "PUT" and momentum > 0: return True
      if trade == "CALL" and momentum < 0: return True
   return False

def should_trail_profit(state, pnl, metrics):
   max_profit = max(state.get("max_profit", 0.0), pnl)
   state["max_profit"] = max_profit

   if max_profit >= 20:
      if pnl < max_profit - 5: return True  
   elif max_profit >= 15:
      if pnl < max_profit - 7: return True  
   elif max_profit >= 10:
      if pnl < 2: return True
   elif max_profit >= 5:
      if pnl < 0: return True 

   atr_tp = 2.0 * metrics["atr"]
   if pnl > atr_tp:
      if pnl < max_profit * 0.7: return True
      
   return False

def should_force_exit(state, pnl, metrics):
   # FIX: Implemented hard floor of 12 points for stop loss minimum
   min_stop = max(metrics["atr"] * 1.2, 12.0)
   if pnl < -min_stop: return True
   return False

def risk_block_active(state, current_time, metrics):
   # FIX: Restored immediate momentum bypass (threshold > 0.65)
   if abs(metrics.get("momentum", 0)) > 0.65:
      return False

   if state.get("consecutive_losses", 0) >= 3:
      t_min = current_time.hour * 60 + current_time.minute
      if t_min < state.get("loss_cooldown_until", 0):
         return True
      else:
         state["consecutive_losses"] = 0
   return False

def select_strike(spot, expected_move, direction):
   atm = round(spot / 50) * 50
   if expected_move > 50: offset = 100
   elif expected_move > 25: offset = 50
   else: offset = 0
   
   if direction == "CALL": return atm + offset
   if direction == "PUT": return atm - offset
   return atm

def reset_trade(state, pnl, current_time):
   state["active_trade"] = "NO_TRADE"
   state["bars_in_trade"] = 0
   state["max_profit"] = 0.0
   state["entry_spot"] = 0.0
   state["trend_lock"] = 0
   state["min_hold"] = 0
   
   if pnl < 0:
      state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
      t_min = current_time.hour * 60 + current_time.minute
      state["loss_cooldown_until"] = t_min + 30
   else:
      if pnl >= 5.0:
         state["consecutive_losses"] = 0

# --- MASTER ENGINE FLOW ---
def process_engine_step(metrics, state, current_time, force_exit_only=False):
   price = metrics["price"]
   momentum = metrics["momentum"]
   t_min = current_time.hour * 60 + current_time.minute
   
   hist = state.get("momentum_history", [])
   hist.append(momentum)
   if len(hist) > 10: hist.pop(0)
   state["momentum_history"] = hist
   persistence = count_same_sign(hist)

   regime = detect_regime(metrics)
   metrics["regime"] = regime

   active_trade = state.get("active_trade", "NO_TRADE")

   if regime == "LOW_VOL" and active_trade == "NO_TRADE":
      return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "LOW_VOL", "score": 0}

   if active_trade != "NO_TRADE":
      entry_price = state.get("entry_spot", price)
      pnl = compute_pnl(entry_price, price, active_trade)
      
      if should_force_exit(state, pnl, metrics):
         reset_trade(state, pnl, current_time)
         return {"action": "EXIT", "signal": "NO_TRADE", "reason": "STOP_LOSS", "score": 0}

      elif should_exit_structural(metrics, state, persistence):
         reset_trade(state, pnl, current_time)
         return {"action": "EXIT", "signal": "NO_TRADE", "reason": "STRUCTURAL_EXIT", "score": 0}

      elif should_exit_no_progress(state, pnl, metrics):
         reset_trade(state, pnl, current_time)
         return {"action": "EXIT", "signal": "NO_TRADE", "reason": "NO_PROGRESS", "score": 0}

      elif should_trail_profit(state, pnl, metrics):
         reset_trade(state, pnl, current_time)
         return {"action": "EXIT", "signal": "NO_TRADE", "reason": "TRAIL_STOP", "score": 0}

      else:
         hold_valid = should_hold(state, metrics)
         if not hold_valid and state.get("min_hold", 0) <= 0 and state.get("trend_lock", 0) <= 0:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": "HOLD_FAILED", "score": 0}
            
         state["bars_in_trade"] = state.get("bars_in_trade", 0) + 1
         return {"action": "HOLD", "signal": active_trade, "reason": "HOLDING", "score": 0}

   else:
      if force_exit_only:
         return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "TIME_BLOCK", "score": 0}

      if risk_block_active(state, current_time, metrics):
         return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "RISK_BLOCK", "score": 0}

      decision, score = get_trade_decision(metrics, regime)
      reason = "SCORING_MODEL"

      if decision != "NO_TRADE":
         state["active_trade"] = decision
         state["entry_spot"] = price
         state["max_profit"] = 0.0
         state["bars_in_trade"] = 0
         state["min_hold"] = 2
         state["trend_lock"] = 3
         state["daily_trades"] = state.get("daily_trades", 0) + 1
         state["last_trade_time"] = t_min
         
         strike = select_strike(price, metrics["expected_move"], decision)
         return {"action": "ENTRY", "signal": decision, "reason": reason, "strike": strike, "score": score}

   return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "SEARCHING", "score": 0}