import time
from config import RECENT_SNAP_FILE, AI_QUERY_FILE
from ai_notify import trigger_ai_and_telegram
import os

def run_dummy_test():
    print("🚀 Starting Dummy AI & Telegram Test...")

    # 1. Ensure ai-query.txt exists
    if not os.path.exists(AI_QUERY_FILE):
        print(f"⚠️ Warning: {AI_QUERY_FILE} not found. AI might not know what to do.")
    
    # 2. Write a fake, perfect trade setup to recent-snap.txt
    print("📝 Writing fake HIGH_PROB_CALL snapshot to recent-snap.txt...")
    dummy_data = """[DUMMY TEST DATA]
INPUT_SPOT:[23700.0, 23710.0, 23720.0, 23730.0, 23740.0, 23750.0]:last 15 rows
ATR:5.00:mean(abs diff last 5):volatility normalization
FAST_SLOPE:8.50:thr=2.00:short-term momentum
SLOW_SLOPE:4.20:thr=4.00:trend structure
RAW_TREND:STRONG_UP:fast+slow relation:initial direction
OI_LABEL:BULLISH_CONFIRM:price+OI relation:smart money confirmation
ACCEL:UP:prev=4.00:momentum change
FINAL_SIGNAL:STRONG UP:priority logic:final classification
------------------------------------------------------------
TIME  | SPOT  | SIGNAL          | TRADING_STATUS  | SESSION | FAST | SLOW | ACCEL    | OI LABEL        | VOL LABEL
15:45 | 23750 | STRONG UP       | HIGH_PROB_CALL  | +50     | +8.5 | +4.2 | UP       | BULLISH_CONFIRM | NORMAL
"""
    with open(RECENT_SNAP_FILE, "w", encoding="utf-8") as f:
        f.write(dummy_data)

    # 3. Fire the exact same function main.py uses
    print("🤖 Calling Gemini API and Telegram (Please wait ~5-15 seconds)...")
    trigger_ai_and_telegram()
    
    print("✅ Test Complete!")
    print("👉 Check your Telegram app.")
    print("👉 Check ai-analysis.txt to see the raw AI output.")

if __name__ == "__main__":
    run_dummy_test()