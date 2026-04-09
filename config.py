import os
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
DB_NAME = "nifty.db"
FETCH_INTERVAL_MINUTES = 1

# ==========================================
# MARKET HOLIDAYS (2026)
# ==========================================
NSE_HOLIDAYS = set([
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26",
    "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
])

# ==========================================
# FILE PATHS & CREDENTIALS
# ==========================================
DIR = os.path.dirname(os.path.abspath(__file__))
RECENT_SNAP_FILE  = os.path.join(DIR, "recent-snap.txt")
AI_QUERY_FILE     = os.path.join(DIR, "ai-query.txt")
AI_ANALYSIS_FILE  = os.path.join(DIR, "ai-analysis.txt")

