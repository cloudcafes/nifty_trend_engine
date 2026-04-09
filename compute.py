import numpy as np

def compute_metrics(snapshots, state):
    if len(snapshots) < 8: return None
    
    raw_spots = [r['spot'] for r in snapshots]
    pe_ois = [r['pe_oi'] for r in snapshots]
    ce_ois = [r['ce_oi'] for r in snapshots]
    vols = [r['ce_vol'] + r['pe_vol'] for r in snapshots]
    
    # Step 1: ATR
    recent_spots = raw_spots[-6:]
    diffs = [abs(recent_spots[i] - recent_spots[i-1]) for i in range(1, len(recent_spots))]
    atr = np.mean(diffs) if diffs else 1.0
    if atr == 0: atr = 1.0

    # Step 2: Noise Filter
    spots = raw_spots.copy()
    min_delta = max(1.0, atr * 0.05)
    for i in range(1, len(spots)):
        if abs(spots[i] - spots[i-1]) < min_delta:
            spots[i] = spots[i-1]

    # Step 3: Transition
    net_move = abs(spots[-3] - spots[-1])
    threshold = max(atr * 0.25, 8.0)
    transition = None
    if spots[-3] >= spots[-2] >= spots[-1] and spots[-3] > spots[-1] and net_move > threshold:
        transition = "TRANSITION_DOWN"
    elif spots[-3] <= spots[-2] <= spots[-1] and spots[-3] < spots[-1] and net_move > threshold:
        transition = "TRANSITION_UP"

    # Step 4 & 5: Slopes
    fast_slope = np.polyfit([0, 1, 2], spots[-3:], 1)[0]
    fast_threshold = max(atr * 0.15, 2.0)
    
    slow_slope = np.polyfit([0, 1, 2, 3, 4, 5], spots[-6:], 1)[0]
    slow_threshold = max(atr * 0.30, 4.0)

    # Step 6: Raw Trend
    fast_down = fast_slope < -fast_threshold
    fast_up = fast_slope > fast_threshold
    slow_down = slow_slope < -slow_threshold
    slow_up = slow_slope > slow_threshold

    raw_trend = "SIDEWAYS"
    if fast_down and slow_down: raw_trend = "STRONG_DOWN"
    elif fast_up and slow_up: raw_trend = "STRONG_UP"
    elif fast_down: raw_trend = "EARLY_DOWN"
    elif fast_up: raw_trend = "EARLY_UP"

    # Step 7: Persistence
    prev_raw = state.get('prev_raw_trend', 'SIDEWAYS')
    if raw_trend == "SIDEWAYS" and prev_raw != "SIDEWAYS" and abs(fast_slope) > fast_threshold * 0.5:
        trend = prev_raw
    else:
        trend = raw_trend

    # Step 8: Fading
    fading = None
    if fast_slope > -fast_threshold * 0.5 and slow_slope < -slow_threshold:
        fading = "FADING_DOWN"
    elif fast_slope < fast_threshold * 0.5 and slow_slope > slow_threshold:
        fading = "FADING_UP"

    # Step 9: Labels
    price_down = spots[-1] < spots[-2]
    price_up = spots[-1] > spots[-2]
    pe_oi_up = pe_ois[-1] > pe_ois[-2]
    ce_oi_up = ce_ois[-1] > ce_ois[-2]

    oi_label = "NEUTRAL"
    if (price_down and pe_oi_up and ce_oi_up) or (price_up and ce_oi_up and pe_oi_up):
        oi_label = "TRAP"
    elif price_down and pe_oi_up: oi_label = "BEARISH_CONFIRM"
    elif price_up and ce_oi_up: oi_label = "BULLISH_CONFIRM"

    avg_vol_5bars = np.mean(vols[-6:-1]) if len(vols) >= 6 else 1.0
    vol_label = "HIGH_RISK" if vols[-1] > avg_vol_5bars * 1.5 else "NORMAL"

    prev_fast_slope = state['slope_history'][-1] if state['slope_history'] else 0
    if fast_slope * prev_fast_slope > 0:
        if abs(fast_slope) > abs(prev_fast_slope): accel = "UP"
        else: accel = "DOWN"
    else:
        accel = "REVERSAL"

    if state['session_open_spot'] is None:
        state['session_open_spot'] = spots[-1]
    
    session_move = spots[-1] - state['session_open_spot']
    session_strength = round(session_move / atr, 1)

    return {
        "spot": spots[-1], "raw_spots": raw_spots, "filtered_spots": spots,
        "ce_ois": ce_ois, "pe_ois": pe_ois, "vols": vols,
        "atr": atr, "min_delta": min_delta, "transition": transition,
        "net_move": net_move, "threshold": threshold, "fading": fading,
        "trend": trend, "raw_trend": raw_trend, "fast_slope": fast_slope,
        "fast_threshold": fast_threshold, "slow_slope": slow_slope,
        "slow_threshold": slow_threshold, "oi_label": oi_label,
        "vol_label": vol_label, "current_vol": vols[-1], "avg_vol": avg_vol_5bars,
        "accel": accel, "prev_fast_slope": prev_fast_slope,
        "session_move": session_move, "session_strength": session_strength
    }