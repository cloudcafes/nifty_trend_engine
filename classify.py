from config import SLIPPAGE, DELTA_ATM, MAX_DAILY_DRAWDOWN

def count_same_sign(hist):
    if not hist: return 0
    sign = 1 if hist[-1] > 0 else -1
    count = 0
    for val in reversed(hist):
        if (val > 0 and sign > 0) or (val < 0 and sign < 0): count += 1
        else: break
    return count

def compute_pnl(entry, exit_price, trade_type):
    if trade_type == "CALL": raw_spot_diff = exit_price - entry
    elif trade_type == "PUT": raw_spot_diff = entry - exit_price
    else: return 0.0
    return (raw_spot_diff * DELTA_ATM) - SLIPPAGE

def detect_regime(metrics):
    atr_pct, momentum, range_10 = metrics.get("atr_pct", 0), abs(metrics.get("momentum", 0)), metrics.get("range_10", 0)
    if atr_pct < 0.00015:
        if momentum > 0.5: return "TRANSITION"
        return "LOW_VOL"
    if range_10 > 20 and momentum > 0.4: return "TRENDING"
    if range_10 < 15 and momentum < 0.3: return "CHOPPY"
    return "TRANSITION"

def detect_breakout_pattern(prices):
    if len(prices) < 15: return False, "NONE"
    p_break, p_follow, p_curr = prices[-3], prices[-2], prices[-1]
    high_10, low_10 = max(prices[-13:-3]), min(prices[-13:-3])
    if p_break > high_10 and p_follow > p_break and p_curr >= high_10: return True, "CALL"
    if p_break < low_10 and p_follow < p_break and p_curr <= low_10: return True, "PUT"
    return False, "NONE"

def compute_score(metrics):
    direction = "CALL" if metrics.get("momentum", 0) > 0 else "PUT"
    score = 0
    bands = metrics.get("vwap_bands", {})
    momentum = abs(metrics.get("momentum", 0))
    price = metrics.get("price", 0)
    vwap = metrics.get("vwap", 0)
    regime = metrics.get("regime", "TRANSITION")

    if momentum > 0.6 and regime == "TRENDING": score += 35
    elif momentum > 0.3: score += 20
    elif momentum > 0.1: score += 10

    if direction == "CALL" and bands:
        if bands.get("upper1", 0) < price < bands.get("upper2", 0): score += 25
        elif vwap < price <= bands.get("upper1", 0): score += 10
        elif price >= bands.get("upper2", 0): score += 15  

    if direction == "PUT" and bands:
        if bands.get("lower2", 0) < price < bands.get("lower1", 0): score += 25
        elif bands.get("lower1", 0) <= price < vwap: score += 10
        elif price <= bands.get("lower2", 0): score += 15  

    vol_ratio = metrics.get("vol_ratio", 1.0)
    if vol_ratio > 1.5: score += 20
    elif vol_ratio > 1.2: score += 10

    pcr, pcr_delta, oi_bias = metrics.get("pcr", 1.0), metrics.get("pcr_delta", 0.0), metrics.get("oi_bias", "NONE")

    if direction == "PUT":
        if pcr > 1.1: score += 15
        elif oi_bias == "PUT": score += 8
        if pcr_delta > 0.05: score += 10

    if direction == "CALL":
        if pcr < 0.75: score += 15
        elif oi_bias == "CALL": score += 8
        if pcr_delta < -0.05: score += 10

    breakout, bo_dir = detect_breakout_pattern(metrics.get("prices", []))
    if breakout and bo_dir == direction:
        score += 10
        direction = bo_dir

    accel = metrics.get("accel", 0.0)
    if direction == "CALL" and accel > 0.3: score += 5
    elif direction == "PUT" and accel < -0.3: score += 5

    return score, direction

def get_trade_decision(metrics, regime, state, current_time):
    score, direction = compute_score(metrics)
    t_min = current_time.hour * 60 + current_time.minute

    if state.get("daily_pnl", 0.0) <= MAX_DAILY_DRAWDOWN:
        return "NO_TRADE", score, "CIRCUIT_BREAKER"

    if t_min < 570: return "NO_TRADE", score, "PRE_TRADE_WINDOW"
    if t_min >= 885: return "NO_TRADE", score, "POST_CUTOFF"

    # 1. DYNAMIC THRESHOLD: Adaptive barrier based on current market behavior
    base_threshold = 65 if regime == "TRENDING" else 75
    if state.get("daily_trades", 0) >= 2: base_threshold += 10
    
    # 2. TREND-FOLLOWING OVERRIDE (Replaces previous Bias Conflict Gridlock)
    struct_bias = metrics.get("structural_bias", "NONE")
    tact_bias = metrics.get("tactical_bias", "NONE")
    momentum = metrics.get("momentum", 0)
    
    # Is the market physically trending in the direction of the Tactical Bias?
    momentum_aligned = (momentum * (1 if direction == "CALL" else -1) > 0.5)
    tactical_aligned = (tact_bias == ("CALL_BIAS" if direction == "CALL" else "PUT_BIAS"))
    is_trend_following = (regime == "TRENDING" and momentum_aligned and tactical_aligned)
    
    if not is_trend_following:
        # Strict structural bias check ONLY applies when NOT already in a verified trending drop/rip
        if direction == "CALL" and struct_bias == "PUT_BIAS": return "NO_TRADE", score, "BIAS_CONFLICT"
        if direction == "PUT" and struct_bias == "CALL_BIAS": return "NO_TRADE", score, "BIAS_CONFLICT"

    # Read-Only Cooldown Checks (Decrements handled at the very top of the loop)
    is_cooldown, cooldown_reason = False, ""
    if state.get("spike_cooldown", 0) > 0: is_cooldown, cooldown_reason = True, "SPIKE_COOLDOWN"
    elif state.get("exit_cooldown", 0) > 0: is_cooldown, cooldown_reason = True, "EXIT_COOLDOWN"
    elif t_min - state.get("last_trade_time", 0) < 15: is_cooldown, cooldown_reason = True, "MIN_GAP"

    if is_cooldown:
        last_dir = state.get("last_trade_dir", "NONE")
        # 3. REVERSAL BYPASS: Shred the timeout blindfold if an elite setup appears
        if last_dir != "NONE" and last_dir != direction and score >= 85: pass
        else: return "NO_TRADE", score, cooldown_reason

    vwap_diff_pct = (metrics.get("price", 0) - metrics.get("vwap", 0)) / metrics.get("vwap", 1) if metrics.get("vwap", 0) > 0 else 0
    if direction == "CALL" and vwap_diff_pct < -0.001: return "NO_TRADE", score, "WRONG_VWAP_SIDE"
    if direction == "PUT" and vwap_diff_pct > 0.001: return "NO_TRADE", score, "WRONG_VWAP_SIDE"

    # 4. EXECUTION
    if score < base_threshold: return "NO_TRADE", score, "SCORE_INSUFFICIENT"
    return direction, score, "SCORING_MODEL"

def should_hold(state, metrics):
    if state.get("min_hold", 0) > 0:
        state["min_hold"] -= 1
        return True
    if state.get("trend_lock", 0) > 0:
        state["trend_lock"] -= 1
        return True
    trade = state.get("active_trade")
    price, vwap = metrics.get("price", 0), metrics.get("vwap", 0)
    if trade == "CALL" and price > vwap: return True
    if trade == "PUT" and price < vwap: return True
    return False

def should_force_exit(state, pnl, metrics):
    trade = state.get("active_trade")
    medium_atr_opt = metrics.get("medium_atr", 15.0) * DELTA_ATM
    # Medium ATR Macro Stop - Absolute mathematical floor of 15 Option Points
    hard_stop_distance = max(medium_atr_opt * 2.0, 15.0) 
    
    if pnl < -hard_stop_distance: return True, "MACRO_STOP_HIT"

    bars = state.get("bars_in_trade", 0)
    if bars >= 15: # Given 15 mins to breathe before VWAP exits activate
        vwap_diff_pct = (metrics.get("price", 0) - metrics.get("vwap", 0)) / metrics.get("vwap", 1) if metrics.get("vwap", 0) > 0 else 0
        if trade == "CALL" and vwap_diff_pct < -0.0015: return True, "VWAP_BREAK"
        if trade == "PUT" and vwap_diff_pct > 0.0015: return True, "VWAP_BREAK"

    return False, ""

def should_exit_structural(metrics, state, persistence):
    trade = state.get("active_trade", "NO_TRADE")
    price, vwap, momentum = metrics.get("price", 0), metrics.get("vwap", 0), metrics.get("momentum", 0)
    if trade == "CALL" and price < vwap and momentum < 0 and persistence < 2: return True, "STRUCTURAL_EXIT"
    if trade == "PUT" and price > vwap and momentum > 0 and persistence < 2: return True, "STRUCTURAL_EXIT"
    return False, ""

def should_exit_theta_bleed(state, pnl, metrics):
    bars = state.get("bars_in_trade", 0)
    # The Patience Window: Will not check for bleed until 45 mins have passed
    if bars < 45: return False, ""

    prices = metrics.get("prices", [])
    if len(prices) >= 20:
        recent_range = max(prices[-20:]) - min(prices[-20:])
        if recent_range < (metrics.get("medium_atr", 15.0) * 1.5):
            if pnl < (metrics.get("medium_atr", 15.0) * DELTA_ATM * 1.0):
                return True, "THETA_BLEED_EXHAUSTION"
    return False, ""

def should_trail_profit(state, pnl, metrics):
    medium_atr_opt = metrics.get("medium_atr", 15.0) * DELTA_ATM
    max_profit = max(state.get("max_profit", 0.0), pnl)
    state["max_profit"] = max_profit

    # Never trail inside the noise floor
    if max_profit < (medium_atr_opt * 1.0): return False, ""

    # Proportional trails (allow trend to breathe through 2nd/3rd impulse legs)
    if max_profit >= 30.0: trail_level = max_profit * 0.60
    elif max_profit >= 15.0: trail_level = max_profit * 0.50
    else: trail_level = max_profit - (medium_atr_opt * 2.0)

    if pnl < trail_level: return True, "MACRO_TRAIL_HIT"
    return False, ""

def risk_block_active(state, metrics):
    if state.get("consecutive_losses", 0) >= 3:
        # Read-only here; decrement happens strictly in process_engine_step
        if state.get("loss_cooldown_bars", 0) > 0:
            if abs(metrics.get("momentum", 0)) > 1.5 and metrics.get("vol_spike", False) and metrics.get("range_10", 0) > 25: return False
            return True
        else:
            state["consecutive_losses"] = 0
    return False

def detect_and_set_spike_cooldown(prices, state, fast_atr):
    if len(prices) < 2: return
    if abs(prices[-1] - prices[-2]) > fast_atr * 3:
        state["spike_cooldown"] = 4

def select_strike(spot, expected_move, direction):
    atm = round(spot / 50) * 50
    if expected_move > 50: offset = 100
    elif expected_move > 25: offset = 50
    else: offset = 0
    return atm + offset if direction == "CALL" else atm - offset

def reset_trade(state, pnl, current_time):
    state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl
    state["last_trade_dir"] = state.get("active_trade", "NONE")
    state["active_trade"] = "NO_TRADE"
    state["bars_in_trade"] = 0
    state["max_profit"] = 0.0
    state["entry_spot"] = 0.0
    state["trend_lock"] = 0
    state["min_hold"] = 0
    state["exit_cooldown"] = 5
    
    if pnl < 0:
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
        state["loss_cooldown_bars"] = 30
    elif pnl >= 5.0:
        state["consecutive_losses"] = 0

def process_engine_step(metrics, state, current_time, force_exit_only=False):
    # =========================================================================
    # 1. UNCONDITIONAL CLOCK TICKS (THE BUG FIX)
    # Cooldowns are decremented at the absolute top of the loop so they never freeze 
    # when a BIAS_CONFLICT or LOW_VOL triggers an early return.
    # =========================================================================
    if state.get("spike_cooldown", 0) > 0:
        state["spike_cooldown"] -= 1
    if state.get("exit_cooldown", 0) > 0:
        state["exit_cooldown"] -= 1
    if state.get("loss_cooldown_bars", 0) > 0:
        state["loss_cooldown_bars"] -= 1

    price, momentum = metrics.get("price", 0), metrics.get("momentum", 0)
    t_min = current_time.hour * 60 + current_time.minute
    
    if t_min >= 915 and state.get("active_trade", "NO_TRADE") != "NO_TRADE":
        pnl = compute_pnl(state.get("entry_spot", price), price, state["active_trade"])
        reset_trade(state, pnl, current_time)
        return {"action": "EXIT", "signal": "NO_TRADE", "reason": "EOD_SQUARE_OFF", "score": 0}
        
    hist = state.get("momentum_history", [])
    hist.append(momentum)
    if len(hist) > 10: hist.pop(0)
    state["momentum_history"] = hist
    persistence = count_same_sign(hist)

    detect_and_set_spike_cooldown(metrics.get("prices", []), state, metrics.get("fast_atr", 15.0))
    regime = detect_regime(metrics)
    metrics["regime"] = regime

    active_trade = state.get("active_trade", "NO_TRADE")

    if regime == "LOW_VOL" and active_trade == "NO_TRADE":
        return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "LOW_VOL", "score": 0}

    if active_trade != "NO_TRADE":
        entry_price = state.get("entry_spot", price)
        pnl = compute_pnl(entry_price, price, active_trade)
        
        exit_triggered, exit_reason = should_force_exit(state, pnl, metrics)
        if exit_triggered:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": exit_reason, "score": 0}

        exit_triggered, exit_reason = should_exit_structural(metrics, state, persistence)
        if exit_triggered:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": exit_reason, "score": 0}

        exit_triggered, exit_reason = should_exit_theta_bleed(state, pnl, metrics)
        if exit_triggered:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": exit_reason, "score": 0}

        exit_triggered, exit_reason = should_trail_profit(state, pnl, metrics)
        if exit_triggered:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": exit_reason, "score": 0}

        oi_bias = metrics.get("oi_bias", "NONE")
        if active_trade == "PUT" and oi_bias == "PUT": state["trend_lock"] = max(state.get("trend_lock", 0), 2)
        if active_trade == "CALL" and oi_bias == "CALL": state["trend_lock"] = max(state.get("trend_lock", 0), 2)

        if not should_hold(state, metrics) and state.get("min_hold", 0) <= 0 and state.get("trend_lock", 0) <= 0:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": "HOLD_FAILED", "score": 0}
            
        state["bars_in_trade"] = state.get("bars_in_trade", 0) + 1
        return {"action": "HOLD", "signal": active_trade, "reason": "HOLDING", "score": 0}

    else:
        if force_exit_only: return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "POST_CUTOFF", "score": 0}
        if risk_block_active(state, metrics): return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "RISK_BLOCK", "score": 0}

        decision, score, reason = get_trade_decision(metrics, regime, state, current_time)

        if decision != "NO_TRADE":
            state["active_trade"] = decision
            state["entry_spot"] = price
            state["max_profit"] = 0.0
            state["bars_in_trade"] = 0
            state["min_hold"] = 2
            state["trend_lock"] = 3
            state["daily_trades"] = state.get("daily_trades", 0) + 1
            state["last_trade_time"] = current_time.hour * 60 + current_time.minute
            
            # Note: We reset exit_cooldown on entry, but we don't decrement it here anymore.
            state["exit_cooldown"] = 0
            
            strike = select_strike(price, metrics.get("expected_move", 0), decision)
            return {"action": "ENTRY", "signal": decision, "reason": reason, "strike": strike, "score": score}

    return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": reason, "score": score}