import requests
import urllib3
import time
import datetime
import traceback
from google import genai
from config import *

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_BOT_TOKEN: 
        print("⚠️ [Telegram] Token missing or default. Skipping message.")
        return False
        
    clean = text.replace('**', '*').replace('##', '').replace('`', "'").replace('_', '-')
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Telegram max message length is 4096 characters. 
    # We will chunk at 4000 to be safe.
    max_length = 4000
    chunks = [clean[i:i+max_length] for i in range(0, len(clean), max_length)]
    
    overall_success = True
    
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        try:
            resp = requests.post(url, json=payload, verify=False, timeout=15)
            if resp.status_code != 200:
                print(f"\n❌ [Telegram] ERROR {resp.status_code} on chunk {i+1}: {resp.text}\n")
                overall_success = False
            else:
                print(f"✅ [Telegram] Message chunk {i+1}/{len(chunks)} sent successfully.")
        except Exception as e: 
            print(f"\n❌ [Telegram] EXCEPTION on chunk {i+1}: {e}\n")
            overall_success = False
            
        # Brief pause between chunks to avoid Telegram rate limits
        if len(chunks) > 1:
            time.sleep(1)

    return overall_success

def _read_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: 
        print(f"⚠️ [AI Thread] Could not read file {path}: {e}")
        return ""

def validate_gemini_model_on_startup():
    if not GEMINI_API_KEY or "YOUR_" in GEMINI_API_KEY: 
        print("⚠️ Gemini API Key missing or default.")
        return
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=["Reply with: MODEL OK"])
        if "MODEL OK" in response.text: print(f"✅ Gemini model validated: {GEMINI_MODEL}")
    except Exception as e:
        print(f"❌ CRITICAL: Gemini model {GEMINI_MODEL} not responding. Error: {e}")

def trigger_ai_and_telegram(prompt_type="TRADE_SIGNAL", classification=None, ts_str=None, metrics=None):
    try:
        print(f"\n⏳ [AI Thread] Starting analysis for {ts_str}...")
        action = classification.get('action', 'NO_TRADE') if classification else 'NO_TRADE'
        
        # Fast-lane routing for Exits (bypass AI quota constraints)
        if action == "EXIT":
            msg = f"🚨 **SYSTEM EXIT ALERT** 🚨\n\n**Time:** {ts_str}\n**Action:** EXIT\n**Reason:** {classification.get('reason')}\n**Spot:** {metrics['price']:.1f}\n**PnL Booked:** {classification.get('pnl', 0.0):.1f}"
            send_telegram_message(msg)
            return

        # Trigger LLM for Entries or Scheduled updates
        context_content = _read_file(CONTEXT_SNAP_FILE).strip()
        
        prompt = f"""SYSTEM INSTRUCTION:
You are an expert quantitative analyst evaluating intraday market data for Nifty index options. 
Your task is to evaluate the rolling 15-bar market snapshot below and determine the independent trade potential.

CRITICAL OVERRIDE - INDEPENDENT JUDGMENT REQUIRED:
You MUST completely IGNORE the algorithmic engine's `ACTION`, `SIGNAL`, `SCORE`, and `REASON` fields. Do not let the engine's current state bias your analysis. Formulate your own independent judgment based strictly on the raw price, momentum, Put-Call Ratio (PCR), and Open Interest (OI) flow metrics provided.

DATA DICTIONARY (Parameter Guide & Abbreviations in Data):
- TIME / P: Current time (IST) and Nifty index spot price.
- REGIME / TREND (e.g., UPTREND, DOWNTR, NEUTRA): The macro trend defined purely by the PCR. (UPTREND = PCR > 0.95; DOWNTREND = PCR < 0.85; NEUTRAL = PCR 0.85 - 0.95).
- ACTION / SIGNAL: The engine's current execution status (Ignore for your independent analysis).
- M (Momentum): 3-bar price rate of change normalized (-2.0 to +2.0). >0 is bullish, <0 is bearish. Magnitude > 0.30 indicates strong momentum.
- B (Structural Bias / STR_BIAS): 4-bar Open Interest accumulation trend (CALL_BIAS, PUT_BIAS, or NONE). Shows what institutional writers are doing over the last 12+ minutes.
- TAC_BIAS: Tactical Bias. Shows immediate bar-to-bar alignment of momentum and OI.
- PCR: Put-Call Ratio. The ultimate anchor of the trend. > 0.95 means heavy Put writing support (Bullish for market). < 0.85 means heavy Call writing resistance (Bearish for market).
- OI_BIAS: Short-term Open Interest flow direction on the current bar (CALL or PUT).
- PNL: Current open trade profit/loss (Ignore if evaluating a new setup).

EVALUATION CONSIDERATIONS:
- PCR Regime Dominance: Is the PCR clearly > 0.95 (Bullish) or < 0.85 (Bearish), or is it stuck in No Man's Land (0.85 - 0.95)?
- Alignment of Structural Bias (B) with short-term Momentum (M).
- Consistency of OI_BIAS in the 15-bar context (Are we seeing consecutive bars of the same bias?).
- Time of day quality (avoid first 15 mins and last 45 mins).

OUTPUT FORMAT:
1. Explain briefly market perspective.
2. Explain in short Key levels to watch.
3. Intraday trade recommendation with entry zone, Stop Loss (spot), and Target (spot) & why?
4. Top risk factors right now. 
5. Do not include additional commentary and just stick to the answer.
You MUST end your response with EXACTLY one of these lines:
POTENTIAL: LOW
POTENTIAL: MEDIUM
POTENTIAL: HIGH
POTENTIAL: VERY HIGH

POTENTIAL: HIGH criteria:
- PCR confirms the macro direction (> 0.95 for Long/Call, < 0.85 for Short/Put).
- Structural BIAS (B) is clearly aligned with the momentum direction.
- Momentum (M) magnitude is > 0.30.
- Time of day is between 09:30 and 14:30.

POTENTIAL: VERY HIGH criteria:
- All HIGH criteria are met PLUS:
- PCR is extreme (> 1.00 for Long/Call, < 0.70 for Short/Put).
- Momentum is highly aggressive (Magnitude > 0.60).
- 15-bar context shows continuous, uninterrupted OI flow in the trade direction without choppy reversals.

[MARKET DATA 15-BAR CONTEXT]:
{context_content}
"""
        
        ai_response_text = ""
        if GEMINI_API_KEY and "YOUR_" not in GEMINI_API_KEY:
            try:
                print("⏳ [AI Thread] Requesting analysis from Gemini...")
                client = genai.Client(api_key=GEMINI_API_KEY)
                response = client.models.generate_content(model=GEMINI_MODEL, contents=[prompt])
                ai_response_text = response.text
                print("✅ [AI Thread] Gemini analysis successfully received.")
                
                # Save the AI response to the file
                try:
                    with open(AI_ANALYSIS_FILE, 'w', encoding='utf-8') as f:
                        f.write(ai_response_text)
                    print(f"✅ [AI Thread] Saved AI analysis to {AI_ANALYSIS_FILE}")
                except Exception as e:
                    print(f"⚠️ [AI Thread] Could not save to {AI_ANALYSIS_FILE}: {e}")
                    
            except Exception as e:
                ai_response_text = f"*[AI UNAVAILABLE]*"
                print(f"❌ [AI Thread] Gemini API Error: {e}")
        else:
            print("⚠️ [AI Thread] Gemini API Key missing, skipping AI analysis.")

        if action == "ENTRY":
            strike_val = classification.get('strike', 'N/A')
            final_message = f"🚨 **SYSTEM ENTRY ALERT** 🚨\n\n**Time:** {ts_str}\n**Action:** ENTRY {classification.get('signal')}\n**Reason:** {classification.get('reason')}\n**Target Strike:** {strike_val}\n\n🧠 **AI EVALUATION** 🧠\n{ai_response_text}"
        else:
            final_message = f"📊 **MARKET PERSPECTIVE** 📊\n**Time:** {ts_str}\n\n{ai_response_text}"

        send_telegram_message(final_message)
        
    except Exception as e:
        print(f"\n❌ [AI Thread] FATAL CRASH in background thread:\n{traceback.format_exc()}\n")