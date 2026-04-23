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
        else:
            current = (current + "\n" + line) if current else line
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

def trigger_ai_and_telegram(is_periodic_update=False):
    """ Runs entirely async in background. Does NOT block main loop. """
    query_content = _read_file(AI_QUERY_FILE)
    snap_content = _read_file(RECENT_SNAP_FILE)

    if not snap_content: return

    combined_prompt = f"{query_content.strip()}\n\n[MARKET SNAPSHOT DATA]\n{snap_content.strip()}"

    if is_periodic_update:
        combined_prompt += "\n\nSYSTEM INSTRUCTION: This is a scheduled 30-minute market update. Ignore trade entry rules. Provide a brief, high-level market perspective, trend analysis, and key OI/Momentum observations based on the current data."

    if not GEMINI_API_KEY or "YOUR_" in GEMINI_API_KEY: return

    client = genai.Client(api_key=GEMINI_API_KEY)
    ai_response_text = ""
    
    for attempt in range(3):
        try:
            model_to_use = GEMINI_MODEL if attempt < 2 else GEMINI_FALLBACK_MODEL
            response = client.models.generate_content(model=model_to_use, contents=[combined_prompt])
            ai_response_text = response.text
            break
        except Exception as e:
            if attempt == 2:
                print(f"AI API Error after 3 attempts: {e}")
                return
            time.sleep(2)  # Wait before retry

    with open(AI_ANALYSIS_FILE, 'w', encoding='utf-8') as f:
        f.write(ai_response_text)

    if is_periodic_update or "POTENTIAL: HIGH" in ai_response_text or "POTENTIAL: VERY HIGH" in ai_response_text:
        send_telegram_message(ai_response_text)
    else:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Telegram Suppressed: AI did not output HIGH/VERY HIGH potential.")