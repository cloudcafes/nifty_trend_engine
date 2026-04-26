from config import RECENT_SNAP_FILE, CONTEXT_SNAP_FILE, SLIPPAGE, DELTA_ATM
import os

context_buffer = []

def compute_current_pnl(price, state):
    trade = state.get("active_trade", "NO_TRADE")
    if trade == "NO_TRADE": return 0.0
    entry = state.get("entry_spot", price)
    
    if trade == "CALL": raw_spot_diff = price - entry
    elif trade == "PUT": raw_spot_diff = entry - price
    else: return 0.0
    return (raw_spot_diff * DELTA_ATM) - SLIPPAGE

def format_summary_line(hh_mm, price, regime, action, momentum, bias, pcr, pnl):
    return f"{hh_mm} | P:{price:.0f} | {regime[:6]} | {action[:5]} | M:{momentum:.2f} | B:{bias[:4]} | PCR:{pcr:.2f} | PNL:{pnl:.1f}"

def process_output(ts_str, classification, metrics, state):
    global context_buffer
    
    price, momentum = metrics["price"], metrics["momentum"]
    regime, vwap = metrics.get("regime", "UNKNOWN"), metrics.get("vwap", price)
    sbias, tbias = metrics.get("structural_bias", "NONE"), metrics.get("tactical_bias", "NONE")

    action, signal = classification.get("action", "NO_TRADE"), classification.get("signal", "NO_TRADE")
    reason, strike, score = classification.get("reason", "NONE"), classification.get("strike", "N/A"), classification.get("score", 0)

    active_trade, daily_pnl = state.get("active_trade", "NO_TRADE"), state.get("daily_pnl", 0.0)

    if active_trade != "NO_TRADE":
        pnl, max_pnl = compute_current_pnl(price, state), state.get("max_profit", 0.0)
        dd = (max_pnl - pnl) / max_pnl if max_pnl > 5.0 else 0.0
    else: pnl, max_pnl, dd = 0.0, 0.0, 0.0

    hh_mm = ts_str.split(" ")[1][:5]
    columns_header = "TIME  | SPOT  | REGIME   | ACTION  | SIGNAL | SCORE  | OPT_PNL | DD"
    main_line = f"{hh_mm} | {price:.0f} | {regime:<8} | {action:<7} | {signal:<6} | {score:<6} | {pnl:+.1f}   | {dd:.0%}"

    diag_lines = [
        f"MOM:{momentum:.6f} | ACCEL:{metrics.get('accel',0):.6f} | FAST_ATR:{metrics.get('fast_atr',0):.1f} ({metrics.get('atr_pct',0):.4%})",
        f"EXP_MOVE:{metrics.get('expected_move',0):.1f} | PROG:{metrics.get('move_progress',0):.2f} | RNG_10:{metrics.get('range_10',0):.1f}",
        f"BIAS (STR/TAC): {sbias}/{tbias} | VWAP:{vwap:.1f} | VOL_SPIKE:{'YES' if metrics.get('vol_spike') else 'NO'}",
        f"PCR:{metrics.get('pcr',1):.2f} | PCR_DELTA:{metrics.get('pcr_delta',0):.3f} | OI_BIAS:{metrics.get('oi_bias','NONE')} | EXT_UP:{'YES' if metrics.get('ext_up') else 'NO'} | EXT_DN:{'YES' if metrics.get('ext_down') else 'NO'}",
        f"ENTRY/EXIT REASON: {reason} | DAILY_TRADES: {state.get('daily_trades', 0)}/UNCAPPED | DAILY_PNL: {daily_pnl:.1f}"
    ]
    
    if action == "ENTRY": diag_lines.append(f"SUGGESTED_STRIKE: {strike}")
    
    if active_trade != "NO_TRADE":
        diag_lines.append(f"TRADE:{active_trade} | BARS:{state.get('bars_in_trade', 0)} | MAX_PNL:{max_pnl:.1f} | CUR_PNL:{pnl:.1f} | LOCK:{state.get('trend_lock',0)} | HOLD:{state.get('min_hold',0)}")
    diag_lines.append("-" * 80)

    debug_block = "\n".join(diag_lines)
    trading_status = f"{signal}_{reason}" if action == "ENTRY" else (f"HOLD_{active_trade}" if active_trade != "NO_TRADE" else "NO_TRADE")

    with open(RECENT_SNAP_FILE, "w", encoding='utf-8') as f:
        f.write(columns_header + "\n" + main_line + "\n\n" + debug_block + "\n")

    summary = format_summary_line(hh_mm, price, regime, action, momentum, sbias, metrics.get("pcr", 1.0), pnl)
    context_buffer.append(summary)
    if len(context_buffer) > 15: context_buffer.pop(0)

    with open(CONTEXT_SNAP_FILE, "w", encoding='utf-8') as f: f.write("\n".join(context_buffer))

    print(columns_header)
    print(main_line)
    print(debug_block)

    return True, ("MAINTAINING_STATE" if action in ["HOLD", "NO_TRADE"] else ""), main_line, trading_status