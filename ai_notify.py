import requests
import urllib3
import time
import datetime
from google import genai
from config import *

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_BOT_TOKEN: return False
    clean = text.replace('**', '*').replace('##', '').replace('`', "'").replace('_', '-')
    max_len = 4000
    if len(clean) <= max_len: return _send_chunk(clean)
    
    lines = clean.split('\n')
    current, part, success = "", 1, True
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current and not _send_chunk(f"Part {part}:\n\n{current}"): success = False
            part += 1; current = line
        else: current = (current + "\n" + line) if current else line
    if current and not _send_chunk(f"Part {part}:\n\n{current}"): success = False
    return success

def _send_chunk(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=payload, verify=False, timeout=15)
        return resp.status_code == 200
    except: return False

def _read_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f: return f.read()
    except: return ""

def validate_gemini_model_on_startup():
    if not GEMINI_API_KEY or "YOUR_" in GEMINI_API_KEY: return
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=["Reply with: MODEL OK"])
        if "MODEL OK" in response.text: print(f"Gemini model validated: {GEMINI_MODEL}")
        else: print(f"WARNING: Gemini model response unexpected: {response.text}")
    except Exception as e:
        print(f"CRITICAL: Gemini model {GEMINI_MODEL} not responding. Check API key and model name.")

def build_fallback_signal_message(classification, metrics, ts_str, reason=""):
    action = classification.get('action', 'NO_TRADE')
    signal = classification.get('signal', 'NO_TRADE')
    score = classification.get('score', 0)
    strike = classification.get('strike', 'N/A')
    creason = classification.get('reason', 'N/A')
    
    spot = metrics['price'] if metrics else 0
    mom = metrics['momentum'] if metrics else 0
    pcr = metrics['pcr'] if metrics else 0
    pcr_delta = metrics['pcr_delta'] if metrics else 0
    regime = metrics['regime'] if metrics else "UNKNOWN"
    sbias = metrics['structural_bias'] if metrics else "NONE"
    
    if action == "EXIT":
        return f"🚨 **SYSTEM EXIT ALERT** 🚨\n\n**Time:** {ts_str}\n**Action:** EXIT {signal}\n**Reason:** {creason}\n**Spot:** {spot:.1f}\n\n*(Direct Output - Exits Bypass AI to preserve quota)*"
        
    return f"🚨 **SYSTEM ENTRY ALERT** 🚨\n\n**Time:** {ts_str}\n**Action:** {action} {signal}\n**Reason:** {creason}\n**Score:** {score}\n**Target Strike:** {strike}\n**Spot:** {spot:.1f}\n**MOM:** {mom:.2f} | **PCR:** {pcr:.2f} ({pcr_delta:+.2f})\n**Regime:** {regime} | **Bias:** {sbias}\n\n*[AI ANALYSIS UNAVAILABLE - {reason}]*"

def build_ai_prompt(classification, prompt_type):
    context_content = _read_file(CONTEXT_SNAP_FILE).strip()
    
    base_instructions = """
SYSTEM INSTRUCTION:
You are an expert quantitative analyst evaluating intraday market data for Nifty index options. 
Your task is to evaluate the rolling 15-bar market snapshot below and determine the independent trade potential.

CRITICAL OVERRIDE - INDEPENDENT JUDGMENT REQUIRED:
You MUST completely IGNORE the algorithmic engine's `ACTION`, `SIGNAL`, `SCORE`, and `ENTRY/EXIT REASON` fields. Do not let the engine's current state or searching status bias your analysis. Formulate your own independent judgment based strictly on the raw price, momentum, volatility, and order flow metrics provided.

DATA DICTIONARY (Parameter Guide):
- TIME / SPOT: Current time (IST) and Nifty index spot price.
- REGIME: Current market volatility state (TRENDING, TRANSITION, CHOPPY, or LOW_VOL).
- PNL / DD: Current open trade profit/loss and drawdown (Ignore if evaluating a new setup).
- MOM: Momentum score normalized by volatility. (>0 is bullish, <0 is bearish. Magnitude > 0.6 is strong).
- ACCEL: Acceleration (the rate of change of momentum).
- FAST_ATR / ATR %: Fast Average True Range and its percentage of the spot price (Current micro-volatility).
- EXP_MOVE: The expected statistical move based on slow baseline volatility.
- PROG: Move progress. (Ratio of recent directional range to expected move. > 1.0 warns of overextension).
- RNG_10: High-to-Low point range over the last 10 bars.
- BIAS: Longer-term structural trend alignment (CALL_BIAS, PUT_BIAS, or NONE).
- VWAP: Volume Weighted Average Price.
- VOL_SPIKE: Indicates if current volume is > 1.5x the recent average (YES/NO). Signifies institutional participation.
- PCR: Put-Call Ratio based on Open Interest.
- PCR_DELTA: Rate of change in PCR. (Positive = Put pressure building; Negative = Call pressure building).
- OI_BIAS: Short-term Open Interest flow direction (CALL or PUT).
- EXT_UP / EXT_DN: Flags indicating consecutive bar overextensions (YES/NO).

EVALUATION CONSIDERATIONS:
- Momentum strength and consistency across the rolling bars.
- Alignment of structural BIAS with short-term MOM.
- PCR levels and the direction of PCR_DELTA.
- Regime stability (TRENDING preferred for high conviction).
- Time of day quality (avoid first 15 mins and last 45 mins).
- Price position relative to VWAP.

OUTPUT FORMAT:
1. Explain market perspective,direction,context, verdict and why?
2. Explain Key levels to watch and why?
3. Intraday trade recommendation with entry zone, Stop Loss (spot), and Target (spot) & why?
4. If there are no trend/momentum in the market then analyze and tell which strategy among Bull Call Spread, Bear Put Spread, Calendar Spread, Diagonal Spread, Ratio Spread, Straddle, Strangle, Short Straddle, Short Strangle can reap profit with highest probability and why and how.
5. Top risk factors right now and why? 
6. Do not include additional commentary and just stick to answer.

You MUST end your response with EXACTLY one of these lines:
POTENTIAL: LOW
POTENTIAL: MEDIUM
POTENTIAL: HIGH
POTENTIAL: VERY HIGH

POTENTIAL: HIGH criteria:
- Strong momentum (magnitude > 0.6) sustained for 3+ bars in the proposed direction.
- Structural BIAS clearly aligned with the momentum direction.
- PCR confirms the direction.
- TRENDING regime active.
- Time of day is between 09:30 and 14:30.
- Not heavily overextended (PROG < 1.5, EXT flags are NO).

POTENTIAL: VERY HIGH criteria:
- All HIGH criteria are met PLUS:
- PCR_DELTA confirms pressure is actively building in the trade direction.
- OI_BIAS confirms direction.
- VOL_SPIKE is YES (confirming participation).
"""

    if prompt_type == "TRADE_SIGNAL" and classification:
        trade_context = f"""
\nSIGNAL TYPE: {classification.get('action')} {classification.get('signal')}
SCORE: {classification.get('score')}
ENTRY REASON: {classification.get('reason')}
SUGGESTED STRIKE: {classification.get('strike', 'N/A')}
"""
    else:
        trade_context = "\nSCHEDULED MARKET PERSPECTIVE - No active signal\n"

    return base_instructions + trade_context + "\n\n[MARKET DATA 15-BAR CONTEXT]:\n" + context_content

def trigger_ai_and_telegram(prompt_type="TRADE_SIGNAL", classification=None, ts_str=None, metrics=None):
    
    # Save API limits by instantly broadcasting EXITS directly via Telegram without AI processing
    if prompt_type == "TRADE_SIGNAL" and classification and classification.get("action") == "EXIT":
        fallback_msg = build_fallback_signal_message(classification, metrics, ts_str, reason="DIRECT_EXIT_ROUTING")
        send_telegram_message(fallback_msg)
        return

    prompt = build_ai_prompt(classification, prompt_type)
    ai_response_text = ""

    if GEMINI_API_KEY and "YOUR_" not in GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        for attempt in range(3):
            try:
                model_to_use = GEMINI_MODEL if attempt < 2 else GEMINI_FALLBACK_MODEL
                response = client.models.generate_content(model=model_to_use, contents=[prompt])
                ai_response_text = response.text
                break
            except Exception as e:
                # Direct API Rate Limit/Quota Fallback 
                if attempt == 2:
                    print(f"API Error Caught: {e}")
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        fallback_msg = build_fallback_signal_message(classification, metrics, ts_str, reason="API_QUOTA_EXHAUSTED")
                        send_telegram_message(fallback_msg)
                        return
                    else:
                        fallback_msg = build_fallback_signal_message(classification, metrics, ts_str, reason="API_TIMEOUT")
                        send_telegram_message(fallback_msg)
                        return
                time.sleep(2)
    else:
        fallback_msg = build_fallback_signal_message(classification, metrics, ts_str, reason="INVALID_API_KEY")
        send_telegram_message(fallback_msg)
        return

    with open(AI_ANALYSIS_FILE, 'w', encoding='utf-8') as f:
        f.write(ai_response_text)

    if prompt_type == "TRADE_SIGNAL" and classification:
        final_message = (
            f"🚨 **SYSTEM SIGNAL ALERT** 🚨\n\n"
            f"**Time:** {ts_str}\n"
            f"**Action:** {classification.get('action')} {classification.get('signal')}\n"
            f"**Reason:** {classification.get('reason')}\n"
            f"**Score:** {classification.get('score')}\n"
            f"**Target Strike:** {classification.get('strike', 'N/A')}\n\n"
            f"🧠 **AI INDEPENDENT EVALUATION** 🧠\n\n"
            f"{ai_response_text}"
        )
    else:
        final_message = (
            f"📊 **AI SCHEDULED MARKET PERSPECTIVE** 📊\n"
            f"**Time:** {ts_str}\n\n"
            f"{ai_response_text}"
        )

    send_telegram_message(final_message)
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] TELEGRAM SENT: Signal + AI Analysis Combined.")