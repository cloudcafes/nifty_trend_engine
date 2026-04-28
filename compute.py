import numpy as np
from config import MIN_MOMENTUM_WEAK

def compute_metrics(snapshots, state):
    if len(snapshots) < 6: 
        return None

    raw_spots = [r['spot'] for r in snapshots]
    price = raw_spots[-1]
    prev_price = raw_spots[-2]

    # Momentum
    momentum = 0.0
    if len(raw_spots) >= 4:
        roc = (raw_spots[-1] - raw_spots[-4]) / raw_spots[-4]
        momentum = np.tanh(roc * 300) * 2.0

    ce_ois = [s.get('ce_oi', 1) for s in snapshots]
    pe_ois = [s.get('pe_oi', 1) for s in snapshots]
    
    current_ce_oi = ce_ois[-1] if ce_ois[-1] > 0 else 1
    current_pe_oi = pe_ois[-1] if pe_ois[-1] > 0 else 1
    pcr_now = current_pe_oi / current_ce_oi
    
    prev_ce_oi = ce_ois[-2] if ce_ois[-2] > 0 else 1
    prev_pe_oi = pe_ois[-2] if pe_ois[-2] > 0 else 1
    prev_pcr = prev_pe_oi / prev_ce_oi

    # Volume extraction
    ce_vols = [s.get('ce_vol', 1) for s in snapshots]
    pe_vols = [s.get('pe_vol', 1) for s in snapshots]
    current_ce_vol = ce_vols[-1] if ce_vols[-1] > 0 else 1
    current_pe_vol = pe_vols[-1] if pe_vols[-1] > 0 else 1

    # Immediate OI Bias (Aligned to Option Writers)
    oi_bias_now = "NEUTRAL"
    if len(ce_ois) >= 2:
        ce_delta = ce_ois[-1] - ce_ois[-2]
        pe_delta = pe_ois[-1] - pe_ois[-2]
        # Put OI growing faster = Support = Bullish (Trade Direction: CALL)
        if pe_delta > ce_delta: oi_bias_now = "CALL"
        # Call OI growing faster = Resistance = Bearish (Trade Direction: PUT)
        elif ce_delta > pe_delta: oi_bias_now = "PUT"

    # Structural Bias (4-Bar Accumulation)
    str_bias = "NONE"
    if len(ce_ois) >= 5:
        ce_4bar_acc = ce_ois[-1] - ce_ois[-5]
        pe_4bar_acc = pe_ois[-1] - pe_ois[-5]
        if pe_4bar_acc > (ce_4bar_acc * 1.5): str_bias = "CALL_BIAS"
        elif ce_4bar_acc > (pe_4bar_acc * 1.5): str_bias = "PUT_BIAS"

    # Tactical Bias (Immediate Alignment)
    tac_bias = "NONE"
    if oi_bias_now == "CALL" and momentum > 0.2: tac_bias = "CALL_BIAS"
    if oi_bias_now == "PUT" and momentum < -0.2: tac_bias = "PUT_BIAS"

    return {
        "price": price,
        "prev_price": prev_price,
        "momentum": momentum,
        "pcr_now": pcr_now,
        "prev_pcr": prev_pcr,
        "ce_vol": current_ce_vol,
        "pe_vol": current_pe_vol,
        "oi_bias": oi_bias_now,
        "str_bias": str_bias,
        "tac_bias": tac_bias,
        "oi_reliable": True if current_ce_oi > 1 else False
    }