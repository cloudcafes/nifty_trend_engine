"""
classify.py — Nifty Intraday OI-Primary Trend Follower v30.0
==============================================================
Philosophy:
  OI flow is the PRIMARY signal. Price is only a timing filter.
  
  The smart money's footprint shows up in Open Interest BEFORE
  price reflects it. We follow the institutional commitment,
  not the lagging price action.

ENTRY TRIGGERS (either one fires):
  A) PCR SHOCK: PCR moves ≥ +0.20 over ≤2 bars  → enter PUT
                PCR moves ≤ -0.20 over ≤2 bars  → enter CALL
  
  B) OI PERSISTENCE: OI_BIAS same direction for ≥ 4 of last 5 bars
                     AND 5-bar PCR move ≥ +0.10 (PUT) or ≤ -0.10 (CALL)

PRICE FILTER (applied to both triggers):
  Reject entry if current bar's spot move > 0.15% AGAINST the trade.
  (Don't enter a PUT on a bar where spot just ripped +35 pts up.)

HOLD PHILOSOPHY:
  Trust the OI. No tiered trails. No ratchets. No profit giveback rules.
  The trend continues until institutions unwind.

EXIT TRIGGERS (any one fires):
  1) OI COLLAPSE: PCR moves ≥ 0.30 AGAINST position over ≤3 bars
                  AND collapsed level sustained for ≥ 2 consecutive bars
  2) CATASTROPHIC STOP: session extreme + 1 ATR buffer (wide, structural)
  3) EOD: 15:15 forced square-off

NO POST-EXIT COOLDOWN:
  If OI re-signals in same direction immediately, re-enter.
  Whipsaws are avoided by the SUSTAIN requirement on both entry and exit.
"""

from config import SLIPPAGE, DELTA_ATM, MAX_DAILY_DRAWDOWN


# ==================== PARAMETERS ====================

# PCR-based entry triggers
PCR_SHOCK_THRESHOLD    = 0.20   # PCR move over ≤2 bars that qualifies as shock
PCR_MOVE_5BAR_MIN      = 0.10   # min 5-bar PCR drift for persistence entry
OI_BIAS_PERSISTENCE    = 4      # need bias matching in this many of last 5 bars

# Price filter
MAX_OPPOSING_BAR_MOVE  = 0.0015  # 0.15% — reject entry if current bar moved this much against

# OI-based exit
PCR_COLLAPSE_THRESHOLD = 0.30   # PCR collapse over ≤3 bars triggers exit
PCR_COLLAPSE_SUSTAIN   = 2      # collapsed level must hold this many bars

# Catastrophic stop
STOP_SESSION_ATR_BUFFER = 1.0
STOP_MIN_POINTS         = 20.0
STOP_MAX_POINTS         = 80.0

# EOD
EOD_SQUAREOFF_MIN       = 15 * 60 + 15


# ==================== PnL ====================

def compute_pnl(entry, current, trade_type):
    if trade_type == "CALL":   diff = current - entry
    elif trade_type == "PUT":  diff = entry - current
    else:                      return 0.0
    return (diff * DELTA_ATM) - SLIPPAGE


# ==================== REGIME (diagnostic) ====================

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
    price = metrics.get("price", 0.0)
    prices = metrics.get("prices", [])
    if state.get("session_ref") is None and prices:
        state["session_ref"]  = prices[0]
        state["session_high"] = prices[0]
        state["session_low"]  = prices[0]
    state["session_high"] = max(state.get("session_high", price), price)
    state["session_low"]  = min(state.get("session_low", price), price)


# ==================== PCR HISTORY TRACKING ====================

def update_pcr_history(state, pcr_now):
    """Maintain a rolling history of PCR values for collapse-sustain checks."""
    hist = state.get("pcr_history", [])
    hist.append(float(pcr_now))
    if len(hist) > 10:
        hist = hist[-10:]
    state["pcr_history"] = hist
    return hist


# ==================== ENTRY EVALUATION ====================

def evaluate_entry(metrics, state):
    """
    Returns (direction, reason, score).
    OI-PRIMARY LOGIC: decisions driven by PCR shock + OI_BIAS persistence.
    """
    # Hard gates
    if state.get("daily_pnl", 0.0) <= MAX_DAILY_DRAWDOWN:
        return "NONE", "CIRCUIT_BREAKER", 0

    if not metrics.get("oi_reliable", False):
        return "NONE", "OI_UNRELIABLE", 0

    pcr_shock = metrics.get("pcr_shock_2bar", 0.0)
    pcr_5bar  = metrics.get("pcr_move_5bar", 0.0)
    bias_put  = metrics.get("oi_bias_persistence_put", 0)
    bias_call = metrics.get("oi_bias_persistence_call", 0)
    bar_move  = metrics.get("bar_move_pct", 0.0)

    put_signal  = False
    call_signal = False
    trigger_type = ""
    score = 0

    # --- Signal A: PCR SHOCK ----------------------------------------
    if pcr_shock >= PCR_SHOCK_THRESHOLD:
        put_signal = True
        trigger_type = f"SHOCK_PUT_{pcr_shock:+.2f}"
        score = 85 + min(15, int((pcr_shock - PCR_SHOCK_THRESHOLD) * 50))
    elif pcr_shock <= -PCR_SHOCK_THRESHOLD:
        call_signal = True
        trigger_type = f"SHOCK_CALL_{pcr_shock:+.2f}"
        score = 85 + min(15, int((abs(pcr_shock) - PCR_SHOCK_THRESHOLD) * 50))

    # --- Signal B: OI PERSISTENCE (if no shock) --------------------
    if not put_signal and not call_signal:
        if bias_put >= OI_BIAS_PERSISTENCE and pcr_5bar >= PCR_MOVE_5BAR_MIN:
            put_signal = True
            trigger_type = f"PERSIST_PUT_{bias_put}/5_{pcr_5bar:+.2f}"
            score = 70
        elif bias_call >= OI_BIAS_PERSISTENCE and pcr_5bar <= -PCR_MOVE_5BAR_MIN:
            call_signal = True
            trigger_type = f"PERSIST_CALL_{bias_call}/5_{pcr_5bar:+.2f}"
            score = 70

    if not (put_signal or call_signal):
        return "NONE", f"NO_OI_SIGNAL_shock{pcr_shock:+.2f}_5bar{pcr_5bar:+.2f}", 0

    # --- Price filter: reject if current bar strongly opposes ------
    if put_signal and bar_move > MAX_OPPOSING_BAR_MOVE:
        return "NONE", f"PUT_BLOCK_BARMOVE_{bar_move:+.4f}", 0
    if call_signal and bar_move < -MAX_OPPOSING_BAR_MOVE:
        return "NONE", f"CALL_BLOCK_BARMOVE_{bar_move:+.4f}", 0

    direction = "PUT" if put_signal else "CALL"
    return direction, trigger_type, score


# ==================== EXIT EVALUATION ====================

def check_oi_collapse_exit(state, metrics):
    """
    Exit when PCR has collapsed ≥ 0.30 against position over ≤3 bars
    AND the collapsed level is sustained for ≥ 2 bars.
    
    The SUSTAIN check protects against single-bar OI reporting glitches
    (e.g., 13:23 and 13:30 on 2026-04-24 where PCR dipped then bounced back).
    """
    active = state.get("active_trade", "NO_TRADE")
    if active == "NO_TRADE":
        return False, ""

    hist = state.get("pcr_history", [])
    if len(hist) < 4:
        return False, ""  # need at least 4 bars of PCR data

    # Look at the last 4 bars of PCR
    # We need: a "peak" (favorable level) within last 3 bars ago,
    # then a collapse, then sustain for ≥ 2 bars
    current = hist[-1]
    recent = hist[-4:-1]  # 3 bars ago, 2 bars ago, 1 bar ago

    if active == "PUT":
        # PCR favorable = high. Collapse = PCR dropping.
        favorable_peak = max(recent)
        collapse = favorable_peak - current   # positive if PCR dropped
        if collapse < PCR_COLLAPSE_THRESHOLD:
            return False, ""
        # Sustain check: the PREVIOUS bar must also show collapsed PCR
        # (i.e., PCR stayed low for at least the last 2 bars)
        prev_pcr = hist[-2]
        prev_collapse = favorable_peak - prev_pcr
        if prev_collapse < PCR_COLLAPSE_THRESHOLD * 0.8:
            # Previous bar wasn't collapsed → this is a single-bar dip, not sustained
            return False, ""
        return True, f"OI_COLLAPSE_PUT_-{collapse:.2f}"

    else:  # CALL
        # PCR favorable = low. Collapse = PCR rising.
        favorable_trough = min(recent)
        collapse = current - favorable_trough
        if collapse < PCR_COLLAPSE_THRESHOLD:
            return False, ""
        prev_pcr = hist[-2]
        prev_collapse = prev_pcr - favorable_trough
        if prev_collapse < PCR_COLLAPSE_THRESHOLD * 0.8:
            return False, ""
        return True, f"OI_COLLAPSE_CALL_+{collapse:.2f}"


def check_catastrophic_stop(state, price):
    trade = state.get("active_trade", "NO_TRADE")
    stop = state.get("stop_level", None)
    if trade == "NO_TRADE" or stop is None:
        return False
    if trade == "CALL" and price <= stop: return True
    if trade == "PUT"  and price >= stop: return True
    return False


# ==================== TRADE LIFECYCLE ====================

def open_trade(state, direction, price, metrics):
    medium_atr = max(metrics.get("medium_atr", 1.0), 1.0)

    if direction == "PUT":
        session_high = state.get("session_high", price)
        raw_dist = STOP_SESSION_ATR_BUFFER * medium_atr + (session_high - price)
        stop_dist = min(max(raw_dist, STOP_MIN_POINTS), STOP_MAX_POINTS)
        stop_level = price + stop_dist
    else:  # CALL
        session_low = state.get("session_low", price)
        raw_dist = STOP_SESSION_ATR_BUFFER * medium_atr + (price - session_low)
        stop_dist = min(max(raw_dist, STOP_MIN_POINTS), STOP_MAX_POINTS)
        stop_level = price - stop_dist

    state["active_trade"]        = direction
    state["entry_spot"]          = price
    state["medium_atr_at_entry"] = medium_atr
    state["max_profit"]          = 0.0
    state["bars_in_trade"]       = 0
    state["favorable_extreme"]   = price
    state["daily_trades"]        = state.get("daily_trades", 0) + 1
    state["initial_stop_dist"]   = stop_dist
    state["stop_level"]          = stop_level
    state["tier_threshold"]      = -1e9

    # Snapshot the entry-time PCR (for diagnostic)
    state["entry_pcr"]           = metrics.get("pcr_now", 1.0)


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
    # NO post-exit cooldown — OI-primary logic can re-enter immediately
    state["exit_cooldown"]       = 0
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

    metrics["regime"] = detect_regime(metrics)

    # Update session tracking
    update_session(state, metrics)

    # Maintain PCR history for collapse-sustain check
    update_pcr_history(state, metrics.get("pcr_now", 1.0))

    # Momentum history (diagnostic only — not used for decisions)
    hist = state.get("momentum_history", [])
    hist.append(metrics.get("momentum", 0.0))
    if len(hist) > 15: hist.pop(0)
    state["momentum_history"] = hist

    active = state.get("active_trade", "NO_TRADE")

    # -------- EOD forced exit ----------
    if t_min >= EOD_SQUAREOFF_MIN and active != "NO_TRADE":
        pnl = compute_pnl(state.get("entry_spot", price), price, active)
        close_trade(state, pnl)
        return {"action": "EXIT", "signal": "NO_TRADE",
                "reason": "EOD_SQUAREOFF", "score": 0}

    # -------- Active trade management ----------
    if active != "NO_TRADE":
        entry_price = state.get("entry_spot", price)
        pnl = compute_pnl(entry_price, price, active)
        state["bars_in_trade"] = state.get("bars_in_trade", 0) + 1
        state["max_profit"]    = max(state.get("max_profit", 0.0), pnl)

        if active == "CALL":
            state["favorable_extreme"] = max(state.get("favorable_extreme", price), price)
        else:
            state["favorable_extreme"] = min(state.get("favorable_extreme", price), price)

        # Exit 1: Catastrophic stop (session-anchored)
        if check_catastrophic_stop(state, price):
            close_trade(state, pnl)
            return {"action": "EXIT", "signal": "NO_TRADE",
                    "reason": "CATASTROPHIC_STOP", "score": 0}

        # Exit 2: OI COLLAPSE (the primary exit)
        collapse_exit, collapse_reason = check_oi_collapse_exit(state, metrics)
        if collapse_exit:
            close_trade(state, pnl)
            return {"action": "EXIT", "signal": "NO_TRADE",
                    "reason": collapse_reason, "score": 0}

        # HOLD — trust the OI
        tag = "HOLD"
        if pnl > 30:   tag = "HOLD_MATURE"
        elif pnl > 20: tag = "HOLD_STRONG"
        elif pnl > 10: tag = "HOLD_CONFIRMED"
        elif pnl > 3:  tag = "HOLD_BUILDING"
        return {"action": "HOLD", "signal": active,
                "reason": tag, "score": 0}

    # -------- New entry evaluation ----------
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