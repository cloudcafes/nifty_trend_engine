import os
from zoneinfo import ZoneInfo

# Version 22.4 - Patient Structural Trend Follower & API Fallbacks
IST = ZoneInfo('Asia/Kolkata')
DB_NAME = "nifty.db"
FETCH_INTERVAL_MINUTES = 1

# ==========================================
# SYSTEM THRESHOLDS
# ==========================================
SLIPPAGE = 3.0            # 3 points of slippage on the OPTION premium
DELTA_ATM = 0.50          # Approximate delta of an ATM Nifty option
MAX_DAILY_DRAWDOWN = -50.0 # Circuit breaker limit
ATR_SEED = 15.0           

# ==========================================
# MARKET HOLIDAYS (2026)
# ==========================================
NSE_HOLIDAYS = set([
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26",
    "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
])

DIR = os.path.dirname(os.path.abspath(__file__))
RECENT_SNAP_FILE  = os.path.join(DIR, "recent-snap.txt")
CONTEXT_SNAP_FILE = os.path.join(DIR, "context-snap.txt")
AI_ANALYSIS_FILE  = os.path.join(DIR, "ai-analysis.txt")

