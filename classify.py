from config import SLIPPAGE, DELTA_ATM, MAX_DAILY_DRAWDOWN

# =========================
# BASIC HELPERS
# =========================

def count_same_sign(hist):
    """Count consecutive bars of same sign momentum from the end."""
    if not hist:
        return 0
    last = hist[-1]
    if last == 0:
        return 0
    sign = 1 if last > 0 else -1
    count = 0
    for val in reversed(hist):
        if val == 0:
            break
        if (val > 0 and sign > 0) or (val < 0 and sign < 0):
            count += 1
        else:
            break
    return count


def compute_pnl(entry, exit_price, trade_type):
    """Compute option-equivalent PnL from spot movement."""
    if trade_type == "CALL":
        raw_spot_diff = exit_price - entry
    elif trade_type == "PUT":
        raw_spot_diff = entry - exit_price
    else:
        return 0.0
    return (raw_spot_diff * DELTA_ATM) - SLIPPAGE


def update_session_tracking(state, price):
    if state.get("session_open") in (None, 0):
        state["session_open"] = price
    if state.get("session_high") in (None, 0) or price > state["session_high"]:
        state["session_high"] = price
    if state.get("session_low") in (None, 0) or price < state["session_low"]:
        state["session_low"] = price


def select_strike(spot, expected_move, direction):
    atm = round(spot / 50) * 50
    if expected_move > 50:
        offset = 100
    elif expected_move > 25:
        offset = 50
    else:
        offset = 0
    return atm + offset if direction == "CALL" else atm - offset


def reset_trade(state, pnl, current_time):
    state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl
    state["last_trade_dir"] = state.get("active_trade", "NONE")
    state["last_trade_pnl"] = pnl
    state["active_trade"] = "NO_TRADE"
    state["bars_in_trade"] = 0
    state["max_profit"] = 0.0
    state["entry_spot"] = 0.0
    state["entry_reason"] = ""
    state["trade_peak_spot"] = 0.0

    # Post-exit cooldown: longer after losses to prevent churn
    if pnl < -5:
        state["exit_cooldown"] = 15
    elif pnl < 0:
        state["exit_cooldown"] = 8
    else:
        state["exit_cooldown"] = 5

    if pnl < 0:
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    else:
        state["consecutive_losses"] = 0


# =========================
# REGIME DETECTION
# =========================

def detect_regime(metrics):
    atr_pct = metrics.get("atr_pct", 0.0)
    range_10 = metrics.get("range_10", 0.0)
    momentum = abs(metrics.get("momentum", 0.0))
    vol_ratio = metrics.get("vol_ratio", 1.0)

    if atr_pct < 0.00015 and range_10 < 12:
        return "LOW_VOL"
    if range_10 >= 20 and momentum >= 0.45:
        return "TRENDING"
    if range_10 <= 14 and momentum <= 0.25 and vol_ratio < 1.15:
        return "CHOPPY"
    return "TRANSITION"


# =========================
# TREND CLASSIFICATION
# =========================

def classify_trend_state(metrics):
    """
    Classify the current trend direction and state.
    Must require genuine displacement from VWAP - not micro-moves.
    """
    price = metrics.get("price", 0.0)
    vwap = metrics.get("vwap", 0.0)
    momentum = metrics.get("momentum", 0.0)
    accel = metrics.get("accel", 0.0)
    range_10 = metrics.get("range_10", 0.0)
    vol_ratio = metrics.get("vol_ratio", 1.0)
    prices = metrics.get("prices", [])

    if len(prices) < 12 or vwap <= 0:
        return "NONE", "NEUTRAL", 0

    prev_10 = prices[-11:-1] if len(prices) >= 11 else prices[-10:]
    prev_high_10 = max(prev_10)
    prev_low_10 = min(prev_10)

    vwap_dist = price - vwap
    vwap_dist_abs = abs(vwap_dist)

    above_vwap = price > vwap
    below_vwap = price < vwap

    fresh_up_break = price >= prev_high_10
    fresh_down_break = price <= prev_low_10

    up_pressure = 0
    down_pressure = 0

    # VWAP side with distance weighting
    if above_vwap and vwap_dist_abs >= 10:
        up_pressure += 25
    elif above_vwap and vwap_dist_abs >= 5:
        up_pressure += 15
    elif above_vwap:
        up_pressure += 8

    if below_vwap and vwap_dist_abs >= 10:
        down_pressure += 25
    elif below_vwap and vwap_dist_abs >= 5:
        down_pressure += 15
    elif below_vwap:
        down_pressure += 8

    # Momentum
    abs_mom = abs(momentum)
    if momentum > 0:
        if abs_mom >= 1.0:
            up_pressure += 25
        elif abs_mom >= 0.5:
            up_pressure += 15
        elif abs_mom >= 0.25:
            up_pressure += 8
    elif momentum < 0:
        if abs_mom >= 1.0:
            down_pressure += 25
        elif abs_mom >= 0.5:
            down_pressure += 15
        elif abs_mom >= 0.25:
            down_pressure += 8

    # Range expansion
    if range_10 >= 25:
        if momentum > 0:
            up_pressure += 10
        elif momentum < 0:
            down_pressure += 10
    elif range_10 >= 18:
        if momentum > 0:
            up_pressure += 5
        elif momentum < 0:
            down_pressure += 5

    # Volume
    if vol_ratio > 1.2:
        if momentum > 0:
            up_pressure += 5
        elif momentum < 0:
            down_pressure += 5

    # Breakout with range confirmation
    if fresh_up_break and range_10 >= 18:
        up_pressure += 10
    if fresh_down_break and range_10 >= 18:
        down_pressure += 10

    # Acceleration
    if accel > 0.3 and momentum > 0:
        up_pressure += 5
    if accel < -0.3 and momentum < 0:
        down_pressure += 5

    # Require clear dominance
    if up_pressure >= down_pressure + 20:
        trend_dir = "CALL"
        strength = up_pressure
    elif down_pressure >= up_pressure + 20:
        trend_dir = "PUT"
        strength = down_pressure
    else:
        return "NONE", "NEUTRAL", max(up_pressure, down_pressure)

    # State classification
    if trend_dir == "CALL":
        if above_vwap and momentum > 0.8 and fresh_up_break and range_10 >= 20:
            return "CALL", "TREND", strength
        elif above_vwap and momentum > 0.4:
            return "CALL", "EMERGING", strength
        elif above_vwap:
            return "CALL", "PULLBACK", strength
    else:
        if below_vwap and momentum < -0.8 and fresh_down_break and range_10 >= 20:
            return "PUT", "TREND", strength
        elif below_vwap and momentum < -0.4:
            return "PUT", "EMERGING", strength
        elif below_vwap:
            return "PUT", "PULLBACK", strength

    return trend_dir, "EXHAUSTION", strength


def sentiment_boost(metrics, direction):
    """Soft contextual boost."""
    boost = 0
    pcr = metrics.get("pcr", 1.0)
    pcr_delta = metrics.get("pcr_delta", 0.0)
    oi_bias = metrics.get("oi_bias", "NONE")

    if direction == "CALL":
        if pcr < 0.85:
            boost += 5
        if pcr_delta < -0.05:
            boost += 5
        if oi_bias == "CALL":
            boost += 3
    elif direction == "PUT":
        if pcr > 1.05:
            boost += 5
        if pcr_delta > 0.05:
            boost += 5
        if oi_bias == "PUT":
            boost += 3

    return boost


# =========================
# ENTRY MODEL
# =========================

def get_trade_decision(metrics, regime, state, current_time):
    t_min = current_time.hour * 60 + current_time.minute
    price = metrics.get("price", 0.0)
    vwap = metrics.get("vwap", 0.0)
    momentum = metrics.get("momentum", 0.0)
    prices = metrics.get("prices", [])
    vol_ratio = metrics.get("vol_ratio", 1.0)
    range_10 = metrics.get("range_10", 0.0)

    # Hard blocks
    if state.get("daily_pnl", 0.0) <= MAX_DAILY_DRAWDOWN:
        return "NO_TRADE", 0, "CIRCUIT_BREAKER"
    if t_min < 570:
        return "NO_TRADE", 0, "PRE_TRADE_WINDOW"
    if t_min >= 885:
        return "NO_TRADE", 0, "POST_CUTOFF"
    if regime == "LOW_VOL":
        return "NO_TRADE", 0, "LOW_VOL"

    # Max trades per day - trend following should be 1-3 trades
    if state.get("daily_trades", 0) >= 3:
        return "NO_TRADE", 0, "MAX_TRADES_REACHED"

    # Consecutive loss block - after 2 consecutive losses, require very strong signal
    consec = state.get("consecutive_losses", 0)
    if consec >= 2:
        if state.get("exit_cooldown", 0) > 0:
            return "NO_TRADE", 0, "LOSS_COOLDOWN"

    # Exit cooldown
    if state.get("exit_cooldown", 0) > 0:
        return "NO_TRADE", 0, "EXIT_COOLDOWN"

    # Spike cooldown
    if state.get("spike_cooldown", 0) > 0:
        return "NO_TRADE", 0, "SPIKE_COOLDOWN"

    # Minimum gap between trades
    last_trade_time = state.get("last_trade_time", 0)
    if last_trade_time > 0 and (t_min - last_trade_time) < 15:
        return "NO_TRADE", 0, "MIN_GAP"

    # Get trend classification
    trend_dir, trend_state, trend_strength = classify_trend_state(metrics)
    if trend_dir == "NONE":
        return "NO_TRADE", trend_strength, "NO_CLEAR_TREND"

    score = trend_strength + sentiment_boost(metrics, trend_dir)

    # Tactical bias boost
    tact_bias = metrics.get("tactical_bias", "NONE")
    if trend_dir == "CALL" and tact_bias == "CALL_BIAS":
        score += 5
    elif trend_dir == "PUT" and tact_bias == "PUT_BIAS":
        score += 5

    # VWAP side check
    if trend_dir == "CALL" and price <= vwap:
        return "NO_TRADE", score, "WRONG_VWAP_SIDE"
    if trend_dir == "PUT" and price >= vwap:
        return "NO_TRADE", score, "WRONG_VWAP_SIDE"

    # Minimum VWAP displacement - require real trend, not noise
    vwap_dist = abs(price - vwap)
    if vwap_dist < 10:
        return "NO_TRADE", score, "INSUFFICIENT_DISPLACEMENT"

    # Extension filter
    ext_up = metrics.get("ext_up", False)
    ext_down = metrics.get("ext_down", False)
    if trend_dir == "CALL" and ext_up and abs(momentum) > 1.8 and vol_ratio < 1.1:
        return "NO_TRADE", score, "OVEREXTENDED_UP"
    if trend_dir == "PUT" and ext_down and abs(momentum) > 1.8 and vol_ratio < 1.1:
        return "NO_TRADE", score, "OVEREXTENDED_DOWN"

    # Same-direction re-entry: require fresh breakout
    last_dir = state.get("last_trade_dir", "NONE")
    if last_dir == trend_dir and len(prices) >= 10:
        recent_high = max(prices[-10:])
        recent_low = min(prices[-10:])
        if trend_dir == "CALL" and price < recent_high:
            return "NO_TRADE", score, "NO_FRESH_CONTINUATION"
        if trend_dir == "PUT" and price > recent_low:
            return "NO_TRADE", score, "NO_FRESH_CONTINUATION"

    # Threshold based on regime
    if regime == "TRENDING":
        threshold = 65
    elif regime == "TRANSITION":
        threshold = 72
    else:
        threshold = 78

    # Adjustments
    if state.get("daily_pnl", 0.0) < -15:
        threshold += 8
    if consec >= 1:
        threshold += 5

    if trend_state == "TREND":
        threshold -= 3
    elif trend_state == "EMERGING":
        threshold += 0
    elif trend_state == "PULLBACK":
        threshold += 8
    else:
        threshold += 10

    if score < threshold:
        return "NO_TRADE", score, "SCORE_INSUFFICIENT"

    return trend_dir, score, f"{trend_state}_ENTRY"


# =========================
# EXIT MODEL - TREND FOLLOWING
# =========================
# Philosophy:
# 1. Give the trade MAXIMUM room to develop
# 2. Only exit on genuine trend reversal signals
# 3. For mature profits, protect against catastrophic giveback
#    but use ABSOLUTE points, not percentages of small numbers
# 4. Never exit just because profit pulled back from a small peak

def catastrophic_stop_hit(state, pnl, metrics):
    """
    Hard stop - absolute maximum loss per trade.
    This is the only tight stop. It prevents catastrophic single-trade loss.
    """
    medium_atr_opt = metrics.get("medium_atr", 15.0) * DELTA_ATM
    hard_stop = max(medium_atr_opt * 2.5, 15.0)
    return pnl < -hard_stop


def trend_reversal_exit(state, metrics):
    """
    The PRIMARY exit mechanism for trend-following.

    Exit when the trend has ACTUALLY reversed, not on normal pullbacks.

    A trend reversal requires MULTIPLE confirming signals:
    1. Price has crossed VWAP (the anchor has shifted)
    2. Momentum is sustained against the position (not just a spike)
    3. The adverse move has persistence (multiple bars)

    This is deliberately loose for small-profit trades because
    the whole point is to let winners run.
    """
    trade = state.get("active_trade", "NO_TRADE")
    price = metrics.get("price", 0.0)
    vwap = metrics.get("vwap", 0.0)
    momentum = metrics.get("momentum", 0.0)
    bars = state.get("bars_in_trade", 0)

    if bars < 3:
        return False, "TOO_EARLY"

    # Count consecutive adverse momentum bars
    mom_hist = state.get("momentum_history", [])
    adverse_bars = 0
    if trade == "CALL":
        for m in reversed(mom_hist):
            if m < -0.1:
                adverse_bars += 1
            else:
                break
    elif trade == "PUT":
        for m in reversed(mom_hist):
            if m > 0.1:
                adverse_bars += 1
            else:
                break

    # Has price crossed VWAP?
    crossed_vwap = False
    if trade == "CALL" and price < vwap:
        crossed_vwap = True
    elif trade == "PUT" and price > vwap:
        crossed_vwap = True

    # REVERSAL CONDITION: Price crossed VWAP + sustained adverse momentum
    # This is the core trend-following exit signal
    if crossed_vwap and adverse_bars >= 3:
        return True, "TREND_REVERSED_VWAP_CROSS"

    # STRONG REVERSAL: Even without VWAP cross, if momentum is very strong
    # against us for many bars, the trend structure has broken
    if adverse_bars >= 5 and abs(momentum) > 0.8:
        return True, "TREND_REVERSED_MOMENTUM"

    return False, "TREND_INTACT"


def mature_profit_protection(state, pnl, metrics):
    """
    For trades that have built SIGNIFICANT profit (>15 points option PnL),
    protect against giving back too much.

    Key principle: Use ABSOLUTE point giveback, not percentage.
    A trade at +30 giving back to +15 is still a great trade.
    A trade at +30 giving back to -5 is a disaster.

    Tiers:
    - Profit 15-25: Allow up to 15 points giveback from peak
    - Profit 25-35: Allow up to 12 points giveback from peak
    - Profit 35+:   Allow up to 10 points giveback from peak

    These are generous limits that only trigger on genuine reversals,
    not normal trend pullbacks.
    """
    max_profit = state.get("max_profit", 0.0)
    bars = state.get("bars_in_trade", 0)

    if max_profit < 15.0:
        # Not mature enough for profit protection
        # Let the trend reversal exit handle it
        return False, "NOT_MATURE"

    giveback = max_profit - pnl

    # Also check: never let a mature trade go negative
    if max_profit >= 15.0 and pnl <= 0:
        return True, "MATURE_GONE_NEGATIVE"

    if max_profit >= 35.0:
        if giveback >= 15.0:
            return True, "TIER3_GIVEBACK_15"
    elif max_profit >= 25.0:
        if giveback >= 18.0:
            return True, "TIER2_GIVEBACK_18"
    elif max_profit >= 15.0:
        if giveback >= 20.0:
            return True, "TIER1_GIVEBACK_20"

    return False, "PROFIT_OK"


def dead_trade_exit(state, pnl, metrics):
    """
    Exit trades that go nowhere after sufficient time.
    But be patient - trend trades can consolidate before moving.
    """
    bars = state.get("bars_in_trade", 0)
    max_profit = state.get("max_profit", 0.0)
    trade = state.get("active_trade", "NO_TRADE")
    momentum = metrics.get("momentum", 0.0)

    # After 10 bars with no profit and adverse momentum
    if bars >= 10 and max_profit < 1.0:
        if trade == "CALL" and momentum < -0.3:
            return True
        if trade == "PUT" and momentum > 0.3:
            return True

    # After 15 bars with no meaningful profit at all
    if bars >= 15 and max_profit < 2.0:
        return True

    return False


# =========================
# MAIN ENGINE
# =========================

def process_engine_step(metrics, state, current_time, force_exit_only=False):
    # Decrement cooldowns
    if state.get("spike_cooldown", 0) > 0:
        state["spike_cooldown"] -= 1
    if state.get("exit_cooldown", 0) > 0:
        state["exit_cooldown"] -= 1

    price = metrics.get("price", 0)
    momentum = metrics.get("momentum", 0)
    t_min = current_time.hour * 60 + current_time.minute

    update_session_tracking(state, price)

    # EOD square-off
    if t_min >= 915 and state.get("active_trade", "NO_TRADE") != "NO_TRADE":
        pnl = compute_pnl(state.get("entry_spot", price), price, state["active_trade"])
        reset_trade(state, pnl, current_time)
        return {"action": "EXIT", "signal": "NO_TRADE", "reason": "EOD_SQUARE_OFF", "score": 0}

    # Update momentum history
    hist = state.get("momentum_history", [])
    hist.append(momentum)
    if len(hist) > 15:
        hist.pop(0)
    state["momentum_history"] = hist
    persistence = count_same_sign(hist)

    # Detect regime
    regime = detect_regime(metrics)
    metrics["regime"] = regime

    active_trade = state.get("active_trade", "NO_TRADE")

    # No-trade in low vol when flat
    if regime == "LOW_VOL" and active_trade == "NO_TRADE":
        return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "LOW_VOL", "score": 0}

    # ========== ACTIVE TRADE MANAGEMENT ==========
    if active_trade != "NO_TRADE":
        entry_price = state.get("entry_spot", price)
        pnl = compute_pnl(entry_price, price, active_trade)
        state["bars_in_trade"] = state.get("bars_in_trade", 0) + 1

        # Track max profit
        state["max_profit"] = max(state.get("max_profit", 0.0), pnl)
        max_profit = state["max_profit"]

        # Track peak spot
        if active_trade == "CALL":
            state["trade_peak_spot"] = max(state.get("trade_peak_spot", price), price)
        elif active_trade == "PUT":
            cur_low = state.get("trade_peak_spot", price)
            if cur_low == 0 or price < cur_low:
                state["trade_peak_spot"] = price

        # EXIT CHECK 1: Catastrophic hard stop (only for runaway losses)
        if catastrophic_stop_hit(state, pnl, metrics):
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": "HARD_STOP", "score": 0}

        # EXIT CHECK 2: Mature profit protection (only for large profits)
        should_exit, exit_reason = mature_profit_protection(state, pnl, metrics)
        if should_exit:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": exit_reason, "score": 0}

        # EXIT CHECK 3: Trend reversal (the primary exit)
        reversed_flag, rev_reason = trend_reversal_exit(state, metrics)
        if reversed_flag:
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": rev_reason, "score": 0}

        # EXIT CHECK 4: Dead trade (trade going nowhere)
        if dead_trade_exit(state, pnl, metrics):
            reset_trade(state, pnl, current_time)
            return {"action": "EXIT", "signal": "NO_TRADE", "reason": "DEAD_TRADE", "score": 0}

        # HOLD
        hold_reason = "HOLDING"
        if pnl > 20:
            hold_reason = "HOLDING_MATURE"
        elif pnl > 10:
            hold_reason = "HOLDING_STRONG"
        elif pnl > 3:
            hold_reason = "HOLDING_CONFIRMED"

        return {"action": "HOLD", "signal": active_trade, "reason": hold_reason, "score": 0}

    # ========== NEW ENTRY LOGIC ==========
    else:
        if force_exit_only:
            return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "POST_CUTOFF", "score": 0}

        decision, score, reason = get_trade_decision(metrics, regime, state, current_time)

        if decision != "NO_TRADE":
            state["active_trade"] = decision
            state["entry_spot"] = price
            state["max_profit"] = 0.0
            state["bars_in_trade"] = 0
            state["daily_trades"] = state.get("daily_trades", 0) + 1
            state["last_trade_time"] = t_min
            state["exit_cooldown"] = 0
            state["trade_peak_spot"] = price

            strike = select_strike(price, metrics.get("expected_move", 0), decision)
            return {
                "action": "ENTRY",
                "signal": decision,
                "reason": reason,
                "strike": strike,
                "score": score,
            }

    return {"action": "NO_TRADE", "signal": "NO_TRADE", "reason": "NO_TRADE", "score": 0}