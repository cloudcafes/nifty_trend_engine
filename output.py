from config import RECENT_SNAP_FILE, CONTEXT_SNAP_FILE, EXIT_OPPOSITE_BARS

context_buffer = []

def process_output(ts_str, classification, metrics, state):
    global context_buffer
    
    price = metrics["price"]
    momentum = metrics["momentum"]
    
    # Correct Nifty PCR Logic (High = Bullish, Low = Bearish)
    pcr = metrics['pcr_now']
    if pcr > 0.95:
        regime = "UPTREND"
    elif pcr < 0.85:
        regime = "DOWNTREND"
    else:
        regime = "NEUTRAL"
        
    sbias = metrics["str_bias"]
    tbias = metrics["tac_bias"]

    action = classification.get("action", "NO_TRADE")
    signal = classification.get("signal", "NO_TRADE")
    reason = classification.get("reason", "NONE")
    strike = classification.get("strike", "N/A")
    score = classification.get("score", 0)

    active_trade = state.get("active_trade", "NO_TRADE")
    daily_pnl = state.get("daily_pnl", 0.0)
    pnl = classification.get("pnl", 0.0)

    hh_mm = ts_str.split(" ")[1][:5]
    columns_header = "TIME  | SPOT  | REGIME       | ACTION  | SIGNAL | PNL  | SCORE"
    main_line = f"{hh_mm} | {price:.0f} | {regime:<12} | {action:<7} | {signal:<6} | {pnl:+.1f} | {score}"

    diag_lines = [
        f"MOMENTUM: {momentum:.4f} | STR_BIAS: {sbias} | TAC_BIAS: {tbias} | TREND: {regime}",
        f"PCR: {pcr:.2f} | OI_BIAS: {metrics['oi_bias']}",
        f"PERSISTENCE: Call({state.get('call_bias_bars',0)}) Put({state.get('put_bias_bars',0)}) | PCR>0.95({state.get('pcr_strong_call_bars',0)}) PCR<0.85({state.get('pcr_strong_put_bars',0)})",
        f"REASON: {reason} | DAILY PNL: {daily_pnl:.1f} | CONSEC_OPP_OI: {state.get('consecutive_opposite_bias', 0)} bars"
    ]
    
    if action == "ENTRY": diag_lines.append(f"SUGGESTED_STRIKE: {strike}")
    if active_trade != "NO_TRADE":
        diag_lines.append(f"TRADE: {active_trade} | MAX PROFIT: {state.get('max_profit', 0.0):.1f} | EXIT BARS: {state.get('exit_confirm_bars', 0)}/{EXIT_OPPOSITE_BARS}")
    
    diag_lines.append("-" * 80)

    debug_block = "\n".join(diag_lines)
    trading_status = f"{signal}_{reason}" if action == "ENTRY" else (f"HOLD_{active_trade}" if active_trade != "NO_TRADE" else "NO_TRADE")

    with open(RECENT_SNAP_FILE, "w", encoding='utf-8') as f:
        f.write(columns_header + "\n" + main_line + "\n\n" + debug_block + "\n")

    summary = f"{hh_mm} | P:{price:.0f} | {regime[:6]} | {action[:5]} | M:{momentum:.2f} | B:{sbias[:4]} | PCR:{pcr:.2f} | PNL:{pnl:.1f}"
    context_buffer.append(summary)
    if len(context_buffer) > 15: context_buffer.pop(0)

    with open(CONTEXT_SNAP_FILE, "w", encoding='utf-8') as f: 
        f.write("\n".join(context_buffer))

    print(columns_header)
    print(main_line)
    print(debug_block)

    return (action in ["ENTRY", "EXIT"]), trading_status