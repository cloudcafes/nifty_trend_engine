from config import RECENT_SNAP_FILE

def check_put_entry(metrics, signal, prev_signal):
    if (prev_signal == "TRANSITION DOWN" and signal in ["EARLY DOWN", "STRONG DOWN"]
        and metrics['fast_slope'] < 0 and metrics['slow_slope'] < 0
        and metrics['accel'] == "UP" and metrics['oi_label'] == "BEARISH_CONFIRM"
        and metrics['vol_label'] == "NORMAL" and metrics['oi_label'] != "TRAP"
        and abs(metrics['session_move']) < 60):
        return True
    return False

def check_call_entry(metrics, signal, prev_signal):
    if (prev_signal == "TRANSITION UP" and signal in ["EARLY UP", "STRONG UP"]
        and metrics['fast_slope'] > 0 and metrics['slow_slope'] > 0
        and metrics['accel'] == "UP" and metrics['oi_label'] == "BULLISH_CONFIRM"
        and metrics['vol_label'] == "NORMAL" and metrics['oi_label'] != "TRAP"
        and abs(metrics['session_move']) < 60):
        return True
    return False

def get_trading_status(metrics, signal, prev_signal):
    if check_put_entry(metrics, signal, prev_signal): return "HIGH_PROB_PUT"
    elif check_call_entry(metrics, signal, prev_signal): return "HIGH_PROB_CALL"
    else: return "NO_TRADE"

def process_output(ts_str, signal, metrics, state):
    prev_signal = state['last_trend']
    trading_status = get_trading_status(metrics, signal, prev_signal)

    # 1. Build the full debug block as a string list
    trans_val = metrics['transition'] if metrics['transition'] else "None"
    fade_val = metrics['fading'] if metrics['fading'] else "None"
    
    debug_lines = [
        f"INPUT_SPOT:{metrics['raw_spots']}:last 15 rows:price series for trend detection",
        f"INPUT_CE_OI:{metrics['ce_ois']}:last 15 rows:call positioning data",
        f"INPUT_PE_OI:{metrics['pe_ois']}:last 15 rows:put positioning data",
        f"INPUT_VOLUME:{metrics['vols']}:ce_vol+pe_vol:activity level",
        f"ATR:{metrics['atr']:.2f}:mean(abs diff last 5):volatility normalization",
        f"NOISE_FILTER:{metrics['filtered_spots']}:delta<{metrics['min_delta']:.2f}:remove micro noise",
        f"TRANSITION:{trans_val}:move={metrics['net_move']:.2f},thr={metrics['threshold']:.2f}:early trend detection",
        f"FAST_SLOPE:{metrics['fast_slope']:.2f}:thr={metrics['fast_threshold']:.2f}:short-term momentum",
        f"SLOW_SLOPE:{metrics['slow_slope']:.2f}:thr={metrics['slow_threshold']:.2f}:trend structure",
        f"RAW_TREND:{metrics['raw_trend']}:fast+slow relation:initial direction",
        f"PERSISTENCE:{metrics['trend']}:prev={state.get('prev_raw_trend', 'SIDEWAYS')}:avoid signal flicker",
        f"FADING:{fade_val}:fast weakening vs slow trend:trend slowdown",
        f"OI_LABEL:{metrics['oi_label']}:price+OI relation:smart money confirmation",
        f"VOL_LABEL:{metrics['vol_label']}:curr={metrics['current_vol']:.2f},avg={metrics['avg_vol']:.2f}:risk level",
        f"ACCEL:{metrics['accel']}:prev={metrics['prev_fast_slope']:.2f}:momentum change",
        f"SESSION_MOVE:{metrics['session_move']:.2f}:spot-open:trend maturity",
        f"FINAL_SIGNAL:{signal}:priority logic:final classification",
        "-" * 60
    ]
    debug_block = "\n".join(debug_lines)

    # 2. Build the Snapshot Output
    hh_mm = ts_str.split(" ")[1][:5]
    columns_header = "TIME  | SPOT  | SIGNAL          | TRADING_STATUS  | SESSION | FAST | SLOW | ACCEL    | OI LABEL        | VOL LABEL"
    
    raw_output = (
        f"{hh_mm} | {metrics['spot']:.0f} | {signal:<15} | "
        f"Trading_Status:{trading_status:<14} | "
        f"session:{metrics['session_move']:+.0f} | fast:{metrics['fast_slope']:+.1f} | "
        f"slow:{metrics['slow_slope']:+.1f} | accel:{metrics['accel']:<8} | "
        f"oi:{metrics['oi_label']:<15} | vol:{metrics['vol_label']}"
    )

    # 3. Duplicate Suppression check
    is_duplicate = (
        signal == state['last_trend'] and metrics['accel'] == state['last_accel'] and
        metrics['oi_label'] == state['last_oi_label'] and metrics['vol_label'] == state['last_vol_label'] and
        trading_status == state.get('last_trading_status', 'NO_TRADE')
    )
    should_print = True
    reason = "DUPLICATE" if is_duplicate else ""

    # ==========================================
    # FILE WRITING (ALWAYS RUNS)
    # Overwrites recent-snap.txt with debug block + snapshot
    # ==========================================
    with open(RECENT_SNAP_FILE, "w", encoding='utf-8') as f:
        f.write(debug_block + "\n")
        f.write(columns_header + "\n")
        f.write(raw_output + "\n")

    # ==========================================
    # CONSOLE PRINTING
    # ==========================================
    print(debug_block)
    if should_print:
        print(columns_header)
        print(raw_output)

    return should_print, reason, raw_output, trading_status