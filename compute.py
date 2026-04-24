import numpy as np
from config import ATR_SEED


def sanitize_prices(raw_prices):
    """Outlier filter: clamps any bar with >2% change to the previous price."""
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
    if len(y) < 2:
        return 0.0
    x = np.arange(len(y))
    return np.polyfit(x, y, 1)[0]


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
        return {
            "fast":   state.get("fast_atr", ATR_SEED),
            "medium": state.get("medium_atr", ATR_SEED),
            "slow":   state.get("slow_atr", ATR_SEED),
        }
    tr = abs(prices[-1] - prices[-2])
    fast   = 0.80  * state.get("fast_atr", ATR_SEED)   + 0.20  * tr
    medium = 0.929 * state.get("medium_atr", ATR_SEED) + 0.071 * tr
    slow   = 0.95  * state.get("slow_atr", ATR_SEED)   + 0.05  * tr
    state["fast_atr"], state["medium_atr"], state["slow_atr"] = fast, medium, slow
    return {"fast": fast, "medium": medium, "slow": slow}


def compute_momentum(prices, volumes, atr, state):
    if len(prices) < 7:
        return 0.0
    slope_7 = linear_regression_slope(prices[-7:])
    norm_slope = slope_7 / prices[-1] if prices[-1] > 0 else 0.0
    roc_3 = (prices[-1] - prices[-4]) / prices[-4] if len(prices) >= 4 and prices[-4] > 0 else 0.0

    if len(volumes) >= 5:
        avg_vol = np.mean(volumes[-5:-1])
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        vol_weight = max(0.5, min(vol_ratio, 2.0))
    else:
        vol_weight = 1.0

    raw = (0.40 * norm_slope + 0.60 * roc_3) * vol_weight
    vol_adj = max(atr / prices[-1] if prices[-1] > 0 else 0.00005, 0.00005)

    momentum = max(min(raw / vol_adj, 3.0), -3.0)
    prev_momentum = state.get("prev_momentum", momentum)
    momentum = 0.70 * prev_momentum + 0.30 * momentum
    state["prev_momentum"] = momentum
    return momentum


def get_dual_bias(prices):
    """
    Returns (structural_bias, tactical_bias).
    Structural lookback kept at 20-50 bars for intraday responsiveness.
    """
    if len(prices) < 15:
        return "NONE", "NONE"

    if len(prices) >= 2:
        last_bar_move = abs(prices[-1] - prices[-2]) / prices[-2] if prices[-2] > 0 else 0
        if last_bar_move > 0.003:
            return "NONE", "NONE"

    # Structural Bias: 20-50 bar linear regression
    struct_lookback = max(20, min(len(prices), 50))
    struct_slope = linear_regression_slope(prices[-struct_lookback:])
    if struct_slope > 0.05:
        struct_bias = "CALL_BIAS"
    elif struct_slope < -0.05:
        struct_bias = "PUT_BIAS"
    else:
        struct_bias = "NONE"

    # Tactical Bias: 9-EMA for fast intraday reversal detection
    ema_9 = compute_ema(prices[-15:], 9)
    current_price = prices[-1]
    if current_price > ema_9:
        tact_bias = "CALL_BIAS"
    elif current_price < ema_9:
        tact_bias = "PUT_BIAS"
    else:
        tact_bias = "NONE"

    return struct_bias, tact_bias


def compute_vwap_bands(prices, vwap, period=10):
    if len(prices) < period:
        return {"upper1": vwap, "lower1": vwap, "upper2": vwap, "lower2": vwap, "std": 0}
    deviations = [abs(p - vwap) for p in prices[-period:]]
    std = np.std(deviations) if len(deviations) > 0 else 0
    return {
        "upper1": vwap + std,
        "lower1": vwap - std,
        "upper2": vwap + (2 * std),
        "lower2": vwap - (2 * std),
        "std": std,
    }


def compute_metrics(snapshots, state):
    if len(snapshots) < 8:
        return None

    raw_spots = [r['spot'] for r in snapshots]
    prices = sanitize_prices(raw_spots)
    price = prices[-1]

    pe_ois = [r['pe_oi'] for r in snapshots]
    ce_ois = [r['ce_oi'] for r in snapshots]
    vols = [r['ce_vol'] + r['pe_vol'] for r in snapshots]

    atrs = compute_atr_suite(prices, state)
    momentum = compute_momentum(prices, vols, atrs['medium'], state)

    prev_m_accel = state.get("prev_momentum_for_accel", momentum)
    accel = momentum - prev_m_accel
    state["prev_momentum_for_accel"] = momentum

    vwap = update_vwap(price, vols[-1], state)
    vwap_bands = compute_vwap_bands(prices, vwap)

    avg_vol_5bars = np.mean(vols[-6:-1]) if len(vols) >= 6 else 1.0
    vol_ratio = vols[-1] / avg_vol_5bars if avg_vol_5bars > 0 else 1.0
    vol_spike = vol_ratio > 1.5

    range_10 = (max(prices[-10:]) - min(prices[-10:])) if len(prices) >= 10 else 0
    expected_move = atrs['slow'] * 2

    range_5_dir = abs(price - prices[-5]) if len(prices) >= 5 else 0
    move_progress = range_5_dir / expected_move if expected_move > 0 else 0.0

    ema_10 = compute_ema(prices, 10)

    # Extension threshold: filters 1-minute bid/ask noise using medium ATR
    ext_distance = atrs['medium'] * 1.5
    overextended_up = (price > ema_10 + ext_distance)
    overextended_down = (price < ema_10 - ext_distance)

    recent_ce = ce_ois[-1] if ce_ois[-1] > 0 else 1
    recent_pe = pe_ois[-1] if pe_ois[-1] > 0 else 1
    pcr = recent_pe / recent_ce

    pcr_delta = 0.0
    if len(snapshots) >= 5:
        past_ce = ce_ois[-5] if ce_ois[-5] > 0 else 1
        past_pe = pe_ois[-5] if pe_ois[-5] > 0 else 1
        past_pcr = past_pe / past_ce
        pcr_delta = pcr - past_pcr

    ce_oi_delta = ce_ois[-1] - ce_ois[-5] if len(ce_ois) >= 5 else 0
    pe_oi_delta = pe_ois[-1] - pe_ois[-5] if len(pe_ois) >= 5 else 0
    oi_bias = "PUT" if pe_oi_delta > ce_oi_delta else "CALL"

    struct_bias, tact_bias = get_dual_bias(prices)

    # ========== v28 DIAGNOSTIC METRICS (kept for AI context) ==========
    # Raw per-bar direction (last 5 bars)
    bar_dirs = []
    for i in range(max(1, len(prices) - 5), len(prices)):
        diff = prices[i] - prices[i - 1]
        bar_dirs.append(1 if diff > 0 else (-1 if diff < 0 else 0))

    # Donchian 15-bar channels (excluding current bar)
    if len(prices) >= 16:
        don_window = prices[-16:-1]
        donchian_high_15 = max(don_window)
        donchian_low_15 = min(don_window)
    else:
        donchian_high_15 = max(prices)
        donchian_low_15 = min(prices)

    # Counter-pullback magnitudes (last 10 bars)
    if len(prices) >= 10:
        win10 = prices[-10:]
        pullback_if_put = max(win10) - price
        pullback_if_call = price - min(win10)
    else:
        pullback_if_put = 0.0
        pullback_if_call = 0.0

    return {
        # Core price & momentum
        "price": price,
        "prices": prices,
        "momentum": momentum,
        "accel": accel,
        # ATR suite
        "fast_atr": atrs['fast'],
        "medium_atr": atrs['medium'],
        "slow_atr": atrs['slow'],
        "atr_pct": atrs['medium'] / price if price > 0 else 0.0,
        # Move / range
        "expected_move": expected_move,
        "move_progress": move_progress,
        "range_10": range_10,
        # Volume
        "vol_ratio": vol_ratio,
        "vol_spike": vol_spike,
        # VWAP
        "vwap": vwap,
        "vwap_bands": vwap_bands,
        # Bias
        "structural_bias": struct_bias,
        "tactical_bias": tact_bias,
        "bias": struct_bias,  # legacy alias
        # Extensions
        "ext_up": overextended_up,
        "ext_down": overextended_down,
        # Options flow
        "pcr": pcr,
        "pcr_delta": pcr_delta,
        "oi_bias": oi_bias,
        # v28 diagnostic additions (used by AI context, not by classify.py)
        "bar_dirs": bar_dirs,
        "donchian_high_15": donchian_high_15,
        "donchian_low_15": donchian_low_15,
        "pullback_if_put": pullback_if_put,
        "pullback_if_call": pullback_if_call,
    }