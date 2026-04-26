import numpy as np
from config import ATR_SEED


# ============================================================
# Price utilities (kept minimal — price is now a filter, not a driver)
# ============================================================

def sanitize_prices(raw_prices):
    """Clamp any bar with >2% change to the previous price."""
    clean = []
    for i, p in enumerate(raw_prices):
        if i == 0:
            clean.append(max(p, 1))
            continue
        prev = clean[-1]
        change_pct = abs(p - prev) / prev if prev > 0 else 0
        clean.append(prev if change_pct > 0.02 else p)
    return clean


def compute_ema(prices, period=10):
    if not prices:
        return 0
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * k) + (ema * (1 - k))
    return ema


def update_vwap(price, total_vol, state):
    last_cum_vol = state.get("last_cum_vol", 0.0)
    inc_vol = max(0.0, total_vol - last_cum_vol) if last_cum_vol > 0 else total_vol
    state["last_cum_vol"] = total_vol
    state["vwap_num"] = state.get("vwap_num", 0.0) + (price * inc_vol)
    state["vwap_den"] = state.get("vwap_den", 0.0) + inc_vol
    if state["vwap_den"] > 0:
        return state["vwap_num"] / state["vwap_den"]
    return price


def compute_atr_suite(prices, state):
    if len(prices) < 2:
        return {"fast": state.get("fast_atr", ATR_SEED),
                "medium": state.get("medium_atr", ATR_SEED),
                "slow": state.get("slow_atr", ATR_SEED)}
    tr = abs(prices[-1] - prices[-2])
    fast   = 0.80  * state.get("fast_atr", ATR_SEED)   + 0.20  * tr
    medium = 0.929 * state.get("medium_atr", ATR_SEED) + 0.071 * tr
    slow   = 0.95  * state.get("slow_atr", ATR_SEED)   + 0.05  * tr
    state["fast_atr"], state["medium_atr"], state["slow_atr"] = fast, medium, slow
    return {"fast": fast, "medium": medium, "slow": slow}


def compute_momentum_simple(prices):
    """Simple momentum: normalized 5-bar rate of change.
    Used only as diagnostic, NOT as a primary signal."""
    if len(prices) < 6:
        return 0.0
    roc = (prices[-1] - prices[-5]) / prices[-5] if prices[-5] > 0 else 0.0
    # Scale to roughly [-3, 3] range
    return np.tanh(roc * 300) * 2.0


# ============================================================
# OI INTELLIGENCE — THE PRIMARY DECISION LAYER
# ============================================================

def compute_oi_intelligence(snapshots):
    """
    Analyzes OI and volume to produce the primary trend signals.
    
    Returns a dict containing:
      - pcr_now:            current PCR (pe_oi / ce_oi at ATM±100)
      - pcr_1bar_ago, pcr_2bar_ago, pcr_3bar_ago, pcr_5bar_ago
      - pcr_shock_2bar:     max PCR change over last 2 bars
      - pcr_move_5bar:      PCR change over last 5 bars
      - oi_bias_now:        'PUT' or 'CALL' (pe_delta vs ce_delta over 5 bars)
      - oi_bias_persistence_put:   count of 'PUT' in last 5 bars' oi_bias
      - oi_bias_persistence_call:  count of 'CALL' in last 5 bars' oi_bias
      - vpcr:               pe_volume / ce_volume (today's conviction)
      - oi_reliable:        False if OI frozen or zero
    """
    if len(snapshots) < 6:
        return _neutral_oi()

    ce_ois  = [s['ce_oi']  for s in snapshots]
    pe_ois  = [s['pe_oi']  for s in snapshots]
    ce_vols = [s['ce_vol'] for s in snapshots]
    pe_vols = [s['pe_vol'] for s in snapshots]

    # ---- Data reliability check --------------------------------
    # If last 4 bars show identical OI, NSE feed is stale
    last4_ce_uniq = len(set(ce_ois[-4:]))
    last4_pe_uniq = len(set(pe_ois[-4:]))
    if (last4_ce_uniq == 1 and last4_pe_uniq == 1) or ce_ois[-1] == 0 or pe_ois[-1] == 0:
        return _neutral_oi()

    # ---- PCR (point-in-time put-call OI ratio) ----------------
    def _pcr(i):
        ce = ce_ois[i] if ce_ois[i] > 0 else 1
        pe = pe_ois[i] if pe_ois[i] > 0 else 1
        return pe / ce

    pcr_now       = _pcr(-1)
    pcr_1bar_ago  = _pcr(-2) if len(snapshots) >= 2 else pcr_now
    pcr_2bar_ago  = _pcr(-3) if len(snapshots) >= 3 else pcr_now
    pcr_3bar_ago  = _pcr(-4) if len(snapshots) >= 4 else pcr_now
    pcr_5bar_ago  = _pcr(-6) if len(snapshots) >= 6 else pcr_now

    # ---- PCR shock: max absolute change over last 2 bars ------
    shock_1bar = pcr_now - pcr_1bar_ago
    shock_2bar_a = pcr_now - pcr_2bar_ago
    # Whichever is larger in magnitude, with appropriate sign
    if abs(shock_2bar_a) > abs(shock_1bar):
        pcr_shock_2bar = shock_2bar_a
    else:
        pcr_shock_2bar = shock_1bar

    # ---- PCR move over 5 bars ---------------------------------
    pcr_move_5bar = pcr_now - pcr_5bar_ago

    # ---- OI_BIAS per bar: compare 5-bar CE vs PE OI deltas ----
    # Compute oi_bias for each of the last 6 bars (so we have 5-bar history)
    def _oi_bias_at(idx):
        """Return 'PUT' or 'CALL' for bar idx (negative index from end)."""
        if len(snapshots) + idx < 5:
            return 'NEUTRAL'
        # 5-bar delta ending at idx
        ref_idx = idx - 4  # 4 bars before current
        if abs(ref_idx) > len(snapshots):
            return 'NEUTRAL'
        ce_d = ce_ois[idx] - ce_ois[ref_idx]
        pe_d = pe_ois[idx] - pe_ois[ref_idx]
        if pe_d > ce_d:
            return 'PUT'
        elif ce_d > pe_d:
            return 'CALL'
        return 'NEUTRAL'

    # Last 5 bars' oi_bias (indices -5 to -1)
    biases = [_oi_bias_at(i) for i in [-5, -4, -3, -2, -1]]
    oi_bias_now = biases[-1]
    oi_bias_persistence_put  = sum(1 for b in biases if b == 'PUT')
    oi_bias_persistence_call = sum(1 for b in biases if b == 'CALL')

    # ---- Volume ratio ----------------------------------------
    ce_v = max(ce_vols[-1], 1)
    pe_v = max(pe_vols[-1], 1)
    vpcr = pe_v / ce_v

    return {
        "pcr_now":                     pcr_now,
        "pcr_1bar_ago":                pcr_1bar_ago,
        "pcr_2bar_ago":                pcr_2bar_ago,
        "pcr_3bar_ago":                pcr_3bar_ago,
        "pcr_5bar_ago":                pcr_5bar_ago,
        "pcr_shock_2bar":              pcr_shock_2bar,
        "pcr_move_5bar":               pcr_move_5bar,
        "oi_bias_now":                 oi_bias_now,
        "oi_bias_persistence_put":     oi_bias_persistence_put,
        "oi_bias_persistence_call":    oi_bias_persistence_call,
        "oi_bias_history":             biases,
        "vpcr":                        vpcr,
        "oi_reliable":                 True,
    }


def _neutral_oi():
    return {
        "pcr_now": 1.0, "pcr_1bar_ago": 1.0, "pcr_2bar_ago": 1.0,
        "pcr_3bar_ago": 1.0, "pcr_5bar_ago": 1.0,
        "pcr_shock_2bar": 0.0, "pcr_move_5bar": 0.0,
        "oi_bias_now": "NEUTRAL",
        "oi_bias_persistence_put": 0, "oi_bias_persistence_call": 0,
        "oi_bias_history": ["NEUTRAL"] * 5,
        "vpcr": 1.0,
        "oi_reliable": False,
    }


# ============================================================
# MAIN METRICS AGGREGATOR
# ============================================================

def compute_metrics(snapshots, state):
    if len(snapshots) < 8:
        return None

    raw_spots = [r['spot'] for r in snapshots]
    prices = sanitize_prices(raw_spots)
    price = prices[-1]
    prev_price = prices[-2] if len(prices) >= 2 else price

    vols = [r['ce_vol'] + r['pe_vol'] for r in snapshots]

    atrs = compute_atr_suite(prices, state)
    momentum = compute_momentum_simple(prices)  # diagnostic only

    vwap = update_vwap(price, vols[-1], state)

    # Price filter metric: current bar % move
    bar_move_pct = (price - prev_price) / prev_price if prev_price > 0 else 0.0

    # OI INTELLIGENCE - PRIMARY SIGNAL SOURCE
    oi = compute_oi_intelligence(snapshots)

    # Session range (for catastrophic stop calculation in classify)
    range_10 = (max(prices[-10:]) - min(prices[-10:])) if len(prices) >= 10 else 0

    return {
        "price": price,
        "prev_price": prev_price,
        "bar_move_pct": bar_move_pct,
        "prices": prices,
        "momentum": momentum,          # diagnostic only
        "fast_atr": atrs['fast'],
        "medium_atr": atrs['medium'],
        "slow_atr": atrs['slow'],
        "atr_pct": atrs['medium'] / price if price > 0 else 0.0,
        "vwap": vwap,
        "range_10": range_10,
        # Legacy fields maintained for AI/output compatibility
        "pcr": oi['pcr_now'],
        "pcr_delta": oi['pcr_shock_2bar'],
        "oi_bias": oi['oi_bias_now'],
        "expected_move": atrs['slow'] * 2,
        "move_progress": 0.0,
        "vol_ratio": 1.0,
        "vol_spike": False,
        "structural_bias": "NONE",
        "tactical_bias": "NONE",
        "bias": "NONE",
        "ext_up": False,
        "ext_down": False,
        "vwap_bands": {"upper1": vwap, "lower1": vwap, "upper2": vwap,
                       "lower2": vwap, "std": 0},
        "accel": 0.0,
        # OI INTELLIGENCE (PRIMARY)
        "oi": oi,
        "pcr_now":                  oi['pcr_now'],
        "pcr_shock_2bar":           oi['pcr_shock_2bar'],
        "pcr_move_5bar":            oi['pcr_move_5bar'],
        "oi_bias_now":              oi['oi_bias_now'],
        "oi_bias_persistence_put":  oi['oi_bias_persistence_put'],
        "oi_bias_persistence_call": oi['oi_bias_persistence_call'],
        "oi_bias_history":          oi['oi_bias_history'],
        "vpcr":                     oi['vpcr'],
        "oi_reliable":              oi['oi_reliable'],
    }