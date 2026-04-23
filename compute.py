import numpy as np

def sanitize_prices(raw_prices):
   clean = []
   for i, p in enumerate(raw_prices):
      if i == 0:
         clean.append(max(p, 1))
         continue
      prev = clean[-1]
      change_pct = abs(p - prev) / prev if prev > 0 else 0
      if change_pct > 0.02:  
         clean.append(prev) 
      else:
         clean.append(p)
   return clean

def linear_regression_slope(y):
   if len(y) < 2: return 0.0
   x = np.arange(len(y))
   return np.polyfit(x, y, 1)[0]

def update_vwap(price, total_vol, state):
   last_cum_vol = state.get("last_cum_vol", 0.0)
   inc_vol = max(0.0, total_vol - last_cum_vol) if last_cum_vol > 0 else total_vol
   state["last_cum_vol"] = total_vol
   
   state["vwap_num"] = state.get("vwap_num", 0.0) + (price * inc_vol)
   state["vwap_den"] = state.get("vwap_den", 0.0) + inc_vol
   
   if state["vwap_den"] > 0:
      return state["vwap_num"] / state["vwap_den"]
   return price

def compute_atr(prices, state):
   if len(prices) >= 2:
      tr = abs(prices[-1] - prices[-2])
   else:
      tr = 1.0

   prev_atr = state.get("prev_atr", tr)
   atr = 0.9 * prev_atr + 0.1 * tr
   state["prev_atr"] = atr
   return atr

def compute_momentum(prices, atr, state):
   if len(prices) >= 5: slope = linear_regression_slope(prices[-5:])
   else: slope = 0.0

   roc = (prices[-1] - prices[-3]) / prices[-3] if len(prices) >= 3 and prices[-3] > 0 else 0.0
   norm_slope = slope / prices[-1] if prices[-1] > 0 else 0.0

   raw = 0.5 * norm_slope + 0.5 * roc
   vol_adj = max(atr / prices[-1] if prices[-1] > 0 else 0.00005, 0.00005)
   momentum = raw / vol_adj
   momentum = max(min(momentum, 3.0), -3.0)

   prev_momentum = state.get("prev_momentum", momentum)
   momentum = 0.6 * prev_momentum + 0.4 * momentum
   state["prev_momentum"] = momentum

   return momentum

def get_market_bias(prices):
   if len(prices) < 30: return "NONE"
   lookback = min(len(prices), 90)
   slope = linear_regression_slope(prices[-lookback:]) 
   if slope > 0.05: return "CALL_BIAS"
   if slope < -0.05: return "PUT_BIAS"
   return "NONE"

def compute_metrics(snapshots, state):
   if len(snapshots) < 8: return None
   
   raw_spots = [r['spot'] for r in snapshots]
   prices = sanitize_prices(raw_spots)
   price = prices[-1]
   
   pe_ois = [r['pe_oi'] for r in snapshots]
   ce_ois = [r['ce_oi'] for r in snapshots]
   vols = [r['ce_vol'] + r['pe_vol'] for r in snapshots]

   atr = compute_atr(prices, state)
   momentum = compute_momentum(prices, atr, state)
   
   prev_m_accel = state.get("prev_momentum_for_accel", momentum)
   accel = momentum - prev_m_accel
   state["prev_momentum_for_accel"] = momentum

   vwap = update_vwap(price, vols[-1], state)

   avg_vol_5bars = np.mean(vols[-6:-1]) if len(vols) >= 6 else 1.0
   vol_spike = vols[-1] > avg_vol_5bars * 1.3

   range_10 = max(prices[-10:]) - min(prices[-10:]) if len(prices) >= 10 else 0
   expected_move = atr * 2

   range_5_dir = abs(price - prices[-5]) if len(prices) >= 5 else 0
   move_progress = range_5_dir / expected_move if expected_move > 0 else 0.0

   overextended_up = len(prices) >= 4 and (prices[-1] > prices[-2] > prices[-3] > prices[-4])
   overextended_down = len(prices) >= 4 and (prices[-1] < prices[-2] < prices[-3] < prices[-4])

   recent_ce = ce_ois[-1] if ce_ois[-1] > 0 else 1
   recent_pe = pe_ois[-1] if pe_ois[-1] > 0 else 1
   pcr = recent_pe / recent_ce

   ce_oi_delta = ce_ois[-1] - ce_ois[-3] if len(ce_ois) >= 3 else 0
   pe_oi_delta = pe_ois[-1] - pe_ois[-3] if len(pe_ois) >= 3 else 0
   oi_bias = "PUT" if pe_oi_delta > ce_oi_delta else "CALL"

   return {
      "momentum": momentum,
      "accel": accel,
      "atr": atr,
      "atr_pct": atr / price if price > 0 else 0.0,
      "expected_move": expected_move,
      "move_progress": move_progress,
      "range_10": range_10,
      "vol_spike": vol_spike,
      "price": price,
      "prices": prices,
      "vwap": vwap,
      "bias": get_market_bias(prices),
      "overextended_up": overextended_up,
      "overextended_down": overextended_down,
      "pcr": pcr,
      "oi_bias": oi_bias
   }