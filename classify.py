import numpy as np
from config import *

def calculate_pnl(entry_price, current_price, direction):
    is_call = "CALL" in direction
    is_put = "PUT" in direction
    
    if is_call: diff = current_price - entry_price
    elif is_put: diff = entry_price - current_price
    else: return 0.0
    
    return (diff * DELTA_ATM) - SLIPPAGE

def update_state(metrics, state):
    oi_bias = metrics.get('oi_bias', 'NEUTRAL')
    active_trade = state.get('active_trade', 'NO_TRADE')
    pcr = metrics.get('pcr_now', 1.0) 

    oi_history = state.get('oi_bias_history', [])
    oi_history.append(oi_bias)
    if len(oi_history) > 10:
        oi_history.pop(0)
    state['oi_bias_history'] = oi_history

    # Persistence Counters
    state['call_bias_bars'] = state.get('call_bias_bars', 0) + 1 if oi_bias == "CALL" else 0
    state['put_bias_bars'] = state.get('put_bias_bars', 0) + 1 if oi_bias == "PUT" else 0
    state['pcr_strong_call_bars'] = state.get('pcr_strong_call_bars', 0) + 1 if pcr > PCR_BULLISH_THRESH else 0
    state['pcr_strong_put_bars']  = state.get('pcr_strong_put_bars', 0)  + 1 if pcr < PCR_BEARISH_THRESH else 0

    consec_opp = state.get('consecutive_opposite_bias', 0)
    if active_trade != 'NO_TRADE':
        if "CALL" in active_trade:
            consec_opp = consec_opp + 1 if oi_bias == "PUT" else 0
        elif "PUT" in active_trade:
            consec_opp = consec_opp + 1 if oi_bias == "CALL" else 0
    else:
        consec_opp = 0
        
    state['consecutive_opposite_bias'] = consec_opp

    return state

def check_entry_signal(metrics, state):
    if not metrics.get('oi_reliable', False):
        return {"action": "NO_TRADE", "reason": "OI_UNRELIABLE", "score": 0}
    if metrics['oi_bias'] == "NEUTRAL":
        return {"action": "NO_TRADE", "reason": "OI_BIAS_NEUTRAL", "score": 0}

    pcr, prev_pcr = metrics['pcr_now'], metrics['prev_pcr']
    mom = metrics['momentum']
    oi_bias = metrics['oi_bias']
    price, prev_price = metrics['price'], metrics['prev_price']
    
    str_tac_call = (metrics['str_bias'] == "CALL_BIAS" or metrics['tac_bias'] == "CALL_BIAS")
    str_tac_put = (metrics['str_bias'] == "PUT_BIAS" or metrics['tac_bias'] == "PUT_BIAS")

    # === PUT SIGNALS (BEARISH) ===
    if pcr < PCR_BEARISH_THRESH and oi_bias == "PUT" and str_tac_put and mom < -MIN_MOMENTUM_STRONG:
        return {"action": "ENTRY", "signal": "PUT", "reason": "STRONG_PUT", "score": 90}
    
    if state.get('pcr_strong_put_bars', 0) >= 2 and state.get('put_bias_bars', 0) >= 2 and (str_tac_put or metrics['str_bias'] == "PUT_BIAS") and mom < -MIN_MOMENTUM_STRONG:
        return {"action": "ENTRY", "signal": "PUT", "reason": "PERSISTENT_PUT", "score": 90}

    # === CALL SIGNALS (BULLISH) ===
    if pcr > PCR_BULLISH_THRESH and oi_bias == "CALL" and str_tac_call:
        if price > prev_price and mom > MIN_MOMENTUM_BREAKOUT:
            return {"action": "ENTRY", "signal": "CALL", "reason": "BREAKOUT_CALL", "score": 85}

    if state.get('pcr_strong_call_bars', 0) >= 2 and state.get('call_bias_bars', 0) >= 2 and str_tac_call and mom > MIN_MOMENTUM_STRONG:
        return {"action": "ENTRY", "signal": "CALL", "reason": "PERSISTENT_CALL", "score": 90}

    return {"action": "NO_TRADE", "reason": "NO_CONDITIONS_MET", "score": 0}

def process_engine_step(metrics, state, current_time, force_exit_only=False):
    state = update_state(metrics, state)
    active_trade = state.get('active_trade', 'NO_TRADE')

    # ------------------------------------------------------------
    # EXIT LOGIC – trailing stop + combined OI/PCR trend exit
    # ------------------------------------------------------------
    if active_trade != "NO_TRADE":
        pnl = calculate_pnl(state.get('entry_spot', 0.0), metrics['price'], active_trade)
        state['max_profit'] = max(state.get('max_profit', 0.0), pnl)
        pcr = metrics['pcr_now']
        oi_bias = metrics['oi_bias']

        should_exit = False
        reason = "HOLD"

        if state['max_profit'] >= TRAIL_ACTIVATE:
            if state['max_profit'] - pnl >= TRAIL_DISTANCE:
                should_exit = True
                reason = "TRAILING_STOP"

        exit_bars = state.get('exit_confirm_bars', 0)
        if active_trade == "CALL":
            # Exit CALL if OI becomes Bearish (PUT) and PCR drops < 0.85
            if oi_bias == "PUT" and pcr <= EXIT_PCR_CALL_THRESH:
                exit_bars += 1
            else:
                exit_bars = 0
        elif active_trade == "PUT":
            # Exit PUT if OI becomes Bullish (CALL) and PCR rises > 0.95
            if oi_bias == "CALL" and pcr >= EXIT_PCR_PUT_THRESH:
                exit_bars += 1
            else:
                exit_bars = 0
        state['exit_confirm_bars'] = exit_bars

        if exit_bars >= EXIT_OPPOSITE_BARS:
            should_exit = True
            reason = "TREND_EXHAUST"

        if force_exit_only:
            should_exit = True
            reason = "END_OF_DAY_EXIT"

        if should_exit:
            state['daily_pnl'] = state.get('daily_pnl', 0.0) + pnl
            state['active_trade'] = "NO_TRADE"
            state['exit_confirm_bars'] = 0
            return {"action": "EXIT", "signal": "NO_TRADE",
                    "reason": reason, "score": 0, "pnl": pnl,
                    "trend": "UPTREND" if pcr > 0.95 else "DOWNTREND" if pcr < 0.85 else "NEUTRAL"}

        return {"action": "HOLD", "signal": active_trade,
                "reason": "HOLDING_POSITION", "score": 50, "pnl": pnl,
                "trend": "UPTREND" if pcr > 0.95 else "DOWNTREND" if pcr < 0.85 else "NEUTRAL"}

    # ------------------------------------------------------------
    # ENTRY GATE – based on PCR regime and OI/signal alignment
    # ------------------------------------------------------------
    if force_exit_only:
        return {"action": "NO_TRADE", "signal": "NO_TRADE",
                "reason": "FORCE_EXIT_ONLY", "score": 0, "trend": "NEUTRAL"}

    raw_signal = check_entry_signal(metrics, state)

    if raw_signal['action'] == "ENTRY":
        proposed_dir = raw_signal['signal']
        pcr = metrics['pcr_now']
        oi = metrics['oi_bias']
        momentum = metrics['momentum']

        # -------- CALL ENTRY GATE (BULLISH) --------
        if proposed_dir == "CALL":
            if pcr > 0.95 and oi == "CALL":
                pass
            elif momentum > 1.0 and oi == "CALL" and pcr > 0.85:
                pass
            else:
                return {"action": "NO_TRADE", "reason": f"CALL_GATE_PCR_{pcr:.2f}", "score": 0, "trend": "NEUTRAL"}

        # -------- PUT ENTRY GATE (BEARISH) --------
        elif proposed_dir == "PUT":
            if pcr < 0.85 and oi == "PUT":
                pass
            elif momentum < -1.0 and oi == "PUT" and pcr < 0.95:
                pass
            else:
                return {"action": "NO_TRADE", "reason": f"PUT_GATE_PCR_{pcr:.2f}", "score": 0, "trend": "NEUTRAL"}

        state['active_trade'] = proposed_dir
        state['entry_spot'] = metrics['price']
        state['max_profit'] = 0.0
        state['consecutive_opposite_bias'] = 0
        state['exit_confirm_bars'] = 0
        raw_signal['trend'] = "UPTREND" if proposed_dir == "CALL" else "DOWNTREND"
        raw_signal['strike'] = round(metrics['price'] / 50) * 50
        
        return raw_signal

    trend_label = "UPTREND" if metrics['pcr_now'] > 0.95 else "DOWNTREND" if metrics['pcr_now'] < 0.85 else "NEUTRAL"
    raw_signal['trend'] = trend_label
    return raw_signal