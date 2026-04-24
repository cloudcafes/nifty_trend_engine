"""
classify.py — Nifty Intraday Trend Follower v28.0 "TIERED-TRAIL"
==================================================================
Philosophy:
  1. Find the trend: price meaningfully separated from session extreme
  2. Enter the trend: on confirmed directional move
  3. HOLD the trend: wide catastrophic stop anchored to session structure
  4. Exit when trend reverses/exhausted: tiered giveback protection
     that only tightens as profit accumulates

ENTRY:
  - PUT when price ≥ ENTRY_ATR_MULT × medium_atr below session_high
         AND ≥ 3 of last 5 bars closed down
  - CALL mirror (above session_low, 3 of 5 up bars)
  - For reversal entries (after prior opposite trade), require ≥ 50%
    retracement of the previous trend span

CATASTROPHIC STOP:
  - PUT: session_high + 1 × medium_atr_at_entry (fixed at entry time)
  - CALL: session_low - 1 × medium_atr_at_entry
  - Very wide, anchored to session structure, not entry price

EXIT (tiered giveback from peak profit):
  peak < 15 pts option PnL  : no giveback protection (stop only)
  peak 15-24                : exit if profit < peak × 0.50
  peak 25-34                : exit if profit < peak × 0.60
  peak 35-44                : exit if profit < peak × 0.65
  peak ≥ 45                 : exit if profit < peak × 0.75
  (Thresholds are ratchet — only tighten, never loosen)

Universal: works symmetrically for up-trend and down-trend days.
"""

from config import SLIPPAGE, DELTA_ATM, MAX_DAILY_DRAWDOWN

# ==================== PARAMETERS ====================

# Entry thresholds
ENTRY_ATR_MULT           = 2.0   # price must be this far from session extreme
ENTRY_LOOKBACK_BARS      = 5     # check last N bars for directional consistency
ENTRY_MIN_DIRECTIONAL    = 3     # need at least this many in trade direction

# Reversal entry (after a prior opposite trade or during strong opposite session)
REVERSAL_RETRACE_MIN     = 0.50  # require ≥ 50% retrace of prior trend

# Exhaustion filter (block already-over-extended entries)
MAX_PROG_FOR_ENTRY       = 2.5

# Catastrophic stop (anchored to session extreme, not entry)
STOP_SESSION_ATR_BUFFER  = 1.0   # buffer beyond session extreme, in ATR
STOP_MIN_POINTS          = 15.0  # absolute minimum in spot points
STOP_MAX_POINTS          = 60.0  # absolute maximum cap

# Tiered giveback (peak_profit in option points → max giveback fraction)
TIERS = [
    # (peak_threshold, retain_fraction)
    (15.0, 0.50),   # peak ≥ 15 → keep 50% of peak
    (25.0, 0.60),   # peak ≥ 25 → keep 60%
    (35.0, 0.65),   # peak ≥ 35 → keep 65%
    (45.0, 0.75),   # peak ≥ 45 → keep 75%
]

# Cooldown between trades
POST_EXIT_COOLDOWN_BARS  = 3

# EOD
EOD_SQUAREOFF_MIN        = 15 * 60 + 15


# ==================== PnL ====================

def compute_pnl(entry, current, trade_type):
    if trade_type == "CALL":   diff = current - entry
    elif trade_type == "PUT":  diff = entry - current
    else:                      return 0.0
    return (diff * DELTA_ATM) - SLIPPAGE


# ==================== REGIME (informational only) ====================

def detect_regime(metrics):
    atr_pct  = metrics.get("atr_pct", 0.0)
    range_10 = metrics.get("range_10", 0.0)
    momentum = abs(metrics.get("momentum", 0.0))
    if atr_pct < 0.00015 and range_10 < 10:  return "LOW_VOL"
    if range_10 >= 18 and momentum >= 0.50:  return "TRENDING"
    if range_10 <= 12 and momentum <= 0.30:  return "CHOPPY"
    return "TRANSITION"


# ==================== SESSION TRACKING ====================

def update_session(state, metrics):
    """
    Track session extremes (high/low reached since market open).
    These define the structural reference for entry gating and catastrophic stops.
    """
    price = metrics.get("price", 0.0)
    prices = metrics.get("prices", [])

    # Session reference: first sanitized price we ever see
    if state.get("session_ref") is None and prices:
        state["session_ref"] = prices[0]
        state["session_high"] = prices[0]
        state["session_low"]  = prices[0]

    # Update rolling extremes
    cur_high = state.get("session_high", price)
    cur_low  = state.get("session_low", price)
    state["session_high"] = max(cur_high, price)
    state["session_low"]  = min(cur_low, price)


# ==================== ENTRY ====================

def count_directional_bars(prices, direction, lookback):
    """Count bars in trade direction within last `lookback` bars."""
    if len(prices) < lookback + 1:
        return 0
    count = 0
    for i in range(len(prices) - lookback, len(prices)):
        diff = prices[i] - prices[i - 1]
        if direction == "CALL" and diff > 0:
            count += 1
        elif direction == "PUT" and diff < 0:
            count += 1
    return count


def evaluate_entry(metrics, state):
    """
    Returns (direction, reason, score).
    direction ∈ {'CALL', 'PUT', 'NONE'}.
    """
    # Hard gates
    if state.get("daily_pnl", 0.0) <= MAX_DAILY_DRAWDOWN:
        return "NONE", "CIRCUIT_BREAKER", 0
    if state.get("exit_cooldown", 0) > 0:
        return "NONE", f"COOLDOWN_{state['exit_cooldown']}", 0

    prog = metrics.get("move_progress", 0.0)
    if prog > MAX_PROG_FOR_ENTRY:
        return "NONE", f"EXHAUSTED_{prog:.2f}", 0

    price = metrics.get("price", 0.0)
    medium_atr = max(metrics.get("medium_atr", 1.0), 1.0)
    prices = metrics.get("prices", [])

    if len(prices) < ENTRY_LOOKBACK_BARS + 1:
        return "NONE", "INSUFFICIENT_HIST", 0

    session_high = state.get("session_high", price)
    session_low  = state.get("session_low", price)

    # Distances in ATR units
    dist_below_high_atr = (session_high - price) / medium_atr
    dist_above_low_atr  = (price - session_low) / medium_atr

    # Directional bar counts
    down_bars = count_directional_bars(prices, "PUT", ENTRY_LOOKBACK_BARS)
    up_bars   = count_directional_bars(prices, "CALL", ENTRY_LOOKBACK_BARS)

    # PUT candidate
    put_structurally_ok = (dist_below_high_atr >= ENTRY_ATR_MULT
                           and down_bars >= ENTRY_MIN_DIRECTIONAL)
    # CALL candidate
    call_structurally_ok = (dist_above_low_atr >= ENTRY_ATR_MULT
                            and up_bars >= ENTRY_MIN_DIRECTIONAL)

    last_dir = state.get("last_trade_dir", "NONE")
    session_span = max(session_high - session_low, 1.0)

    # Reversal check: if last trade was same direction as session slope,
    # a new entry in the OPPOSITE direction requires a meaningful retrace
    def reversal_ok(candidate):
        # Candidate is the NEW direction we want to enter
        if last_dir == "NONE":
            return True  # first trade of day
        if last_dir == candidate:
            return True  # same direction as before — continuation is OK
        # Opposite direction — need retrace
        if candidate == "CALL":
            # Coming from a PUT trade. Need price to have bounced 50% up from low.
            retrace = (price - session_low) / session_span
        else:  # candidate == "PUT"
            # Coming from a CALL trade. Need price to have dropped 50% from high.
            retrace = (session_high - price) / session_span
        return retrace >= REVERSAL_RETRACE_MIN

    # Decide (PUT takes priority only if both qualify — shouldn't happen)
    if put_structurally_ok:
        if reversal_ok("PUT"):
            return "PUT", f"ENTRY_PUT_{dist_below_high_atr:.1f}ATR", _score(metrics, "PUT")
        else:
            return "NONE", "NO_REVERSAL_RETRACE", 0

    if call_structurally_ok:
        if reversal_ok("CALL"):
            return "CALL", f"ENTRY_CALL_{dist_above_low_atr:.1f}ATR", _score(metrics, "CALL")
        else:
            return "NONE", "NO_REVERSAL_RETRACE", 0

    return "NONE", f"NO_SETUP_H{dist_below_high_atr:.1f}_L{dist_above_low_atr:.1f}", 0


def _score(metrics, direction):
    s = 60
    mom = metrics.get("momentum", 0.0)
    if direction == "CALL" and mom > 0.5: s += 15
    if direction == "PUT" and mom < -0.5: s += 15
    range_10 = metrics.get("range_10", 0.0)
    if range_10 >= 20: s += 10
    if range_10 >= 30: s += 5
    return min(100, s)


# ==================== EXIT ====================

def check_catastrophic_stop(state, price):
    trade = state.get("active_trade", "NO_TRADE")
    stop = state.get("stop_level", None)
    if trade == "NO_TRADE" or stop is None:
        return False
    if trade == "CALL" and price <= stop: return True
    if trade == "PUT"  and price >= stop: return True
    return False


def compute_tier_threshold(peak_profit):
    """
    Return the minimum profit we must remain above, based on peak.
    Returns None if no protection active (peak too small).
    """
    active_retain = None
    for tier_peak, retain in TIERS:
        if peak_profit >= tier_peak:
            active_retain = retain
    if active_retain is None:
        return None
    return peak_profit * active_retain


def check_tiered_trail(state, current_pnl):
    """
    Exit if current profit has dropped below the tier threshold.
    Threshold only tightens (via higher peak_profit) — never loosens.
    """
    peak = state.get("max_profit", 0.0)
    threshold = compute_tier_threshold(peak)
    if threshold is None:
        return False, ""

    # Also track the locked-in threshold so it's monotonic
    stored_threshold = state.get("tier_threshold", -1e9)
    if threshold > stored_threshold:
        state["tier_threshold"] = threshold
    else:
        threshold = stored_threshold  # use the locked-in (higher) threshold

    if current_pnl < threshold:
        return True, f"TIER_TRAIL_{threshold:.1f}"
    return False, ""


# ==================== TRADE LIFECYCLE ====================

def open_trade(state, direction, price, metrics):
    medium_atr = max(metrics.get("medium_atr", 1.0), 1.0)

    # Catastrophic stop anchored to session extreme
    if direction == "PUT":
        session_high = state.get("session_high", price)
        stop_dist = min(max(STOP_SESSION_ATR_BUFFER * medium_atr + (session_high - price),
                            STOP_MIN_POINTS),
                        STOP_MAX_POINTS)
        stop_level = price + stop_dist
    else:  # CALL
        session_low = state.get("session_low", price)
        stop_dist = min(max(STOP_SESSION_ATR_BUFFER * medium_atr + (price - session_low),
                            STOP_MIN_POINTS),
                        STOP_MAX_POINTS)
        stop_level = price - stop_dist

    state["active_trade"]         = direction
    state["entry_spot"]           = price
    state["medium_atr_at_entry"]  = medium_atr
    state["max_profit"]           = 0.0
    state["bars_in_trade"]        = 0
    state["favorable_extreme"]    = price
    state["daily_trades"]         = state.get("daily_trades", 0) + 1
    state["initial_stop_dist"]    = stop_dist
    state["stop_level"]           = stop_level
    state["tier_threshold"]       = -1e9  # no lock yet


def close_trade(state, pnl):
    state["daily_pnl"]           = state.get("daily_pnl", 0.0) + pnl
    state["last_trade_dir"]      = state.get("active_trade", "NONE")
    state["active_trade"]        = "NO_TRADE"
    state["bars_in_trade"]       = 0
    state["max_profit"]          = 0.0
    state["entry_spot"]          = 0.0
    state["stop_level"]          = None
    state["favorable_extreme"]   = 0.0
    state["medium_atr_at_entry"] = 0.0
    state["initial_stop_dist"]   = 0.0
    state["tier_threshold"]      = -1e9
    state["exit_cooldown"]       = POST_EXIT_COOLDOWN_BARS
    if pnl < 0:
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    else:
        state["consecutive_losses"] = 0


def select_strike(spot, direction):
    return round(spot / 50) * 50


# ==================== MAIN ENGINE ====================

def process_engine_step(metrics, state, current_time, force_exit_only=False):
    t_min = current_time.hour * 60 + current_time.minute
    price = metrics.get("price", 0.0)

    # Cooldown decrement
    if state.get("exit_cooldown", 0) > 0:
        state["exit_cooldown"] -= 1

    metrics["regime"] = detect_regime(metrics)

    # Update session tracking every bar
    update_session(state, metrics)

    # Momentum history for diagnostics
    hist = state.get("momentum_history", [])
    hist.append(metrics.get("momentum", 0.0))
    if len(hist) > 15: hist.pop(0)
    state["momentum_history"] = hist

    active = state.get("active_trade", "NO_TRADE")

    # EOD forced exit
    if t_min >= EOD_SQUAREOFF_MIN and active != "NO_TRADE":
        pnl = compute_pnl(state.get("entry_spot", price), price, active)
        close_trade(state, pnl)
        return {"action": "EXIT", "signal": "NO_TRADE",
                "reason": "EOD_SQUAREOFF", "score": 0}

    # Active trade management
    if active != "NO_TRADE":
        entry_price = state.get("entry_spot", price)
        pnl = compute_pnl(entry_price, price, active)
        state["bars_in_trade"] = state.get("bars_in_trade", 0) + 1
        state["max_profit"]    = max(state.get("max_profit", 0.0), pnl)

        # Track favorable extreme (diagnostic only)
        if active == "CALL":
            state["favorable_extreme"] = max(state.get("favorable_extreme", price), price)
        else:
            state["favorable_extreme"] = min(state.get("favorable_extreme", price), price)

        # Exit 1: Catastrophic stop (session-anchored)
        if check_catastrophic_stop(state, price):
            close_trade(state, pnl)
            return {"action": "EXIT", "signal": "NO_TRADE",
                    "reason": "CATASTROPHIC_STOP", "score": 0}

        # Exit 2: Tiered trail (only after meaningful peak profit)
        trail_exit, trail_reason = check_tiered_trail(state, pnl)
        if trail_exit:
            close_trade(state, pnl)
            return {"action": "EXIT", "signal": "NO_TRADE",
                    "reason": trail_reason, "score": 0}

        # HOLD
        tag = "HOLD"
        if pnl > 30:   tag = "HOLD_MATURE"
        elif pnl > 20: tag = "HOLD_STRONG"
        elif pnl > 10: tag = "HOLD_CONFIRMED"
        elif pnl > 3:  tag = "HOLD_BUILDING"
        return {"action": "HOLD", "signal": active,
                "reason": tag, "score": 0}

    # New entry evaluation
    if force_exit_only:
        return {"action": "NO_TRADE", "signal": "NO_TRADE",
                "reason": "FORCE_EXIT_ONLY", "score": 0}

    direction, reason, score = evaluate_entry(metrics, state)
    if direction == "NONE":
        return {"action": "NO_TRADE", "signal": "NO_TRADE",
                "reason": reason, "score": score}

    open_trade(state, direction, price, metrics)
    strike = select_strike(price, direction)
    return {"action": "ENTRY", "signal": direction,
            "reason": reason, "strike": strike, "score": score}