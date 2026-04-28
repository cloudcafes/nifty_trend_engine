import os
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
DB_NAME = "nifty.db"
FETCH_INTERVAL_MINUTES = 3 

# ==========================================
# ENTRY THRESHOLDS (Corrected for Nifty Writers)
# ==========================================
PCR_BULLISH_THRESH = 0.95      # High PCR = Put Support = Bullish
PCR_BEARISH_THRESH = 0.85      # Low PCR = Call Resistance = Bearish

MIN_MOMENTUM_BREAKOUT = 0.30   
MIN_MOMENTUM_STRONG = 0.15     
MIN_MOMENTUM_WEAK = 0.10       

# ==========================================
# TREND & EXIT THRESHOLDS
# ==========================================
TREND_OI_MATCH_REQUIRED = 7    
EXIT_OPPOSITE_BARS = 3         

# Trailing stop (points of PnL give-back from the trade's peak)
TRAIL_ACTIVATE = 15.0    
TRAIL_DISTANCE = 8.0     

# Trend-exhaustion exit
EXIT_PCR_CALL_THRESH = 0.85   # Exit a CALL if PCR drops below 0.85 (becomes bearish)
EXIT_PCR_PUT_THRESH  = 0.95   # Exit a PUT if PCR rises above 0.95 (becomes bullish)

SLIPPAGE = 3.0
DELTA_ATM = 0.60               

# ==========================================
# SYSTEM / API CONFIG
# ==========================================
DIR = os.path.dirname(os.path.abspath(__file__))
RECENT_SNAP_FILE  = os.path.join(DIR, "recent-snap.txt")
CONTEXT_SNAP_FILE = os.path.join(DIR, "context-snap.txt")
AI_ANALYSIS_FILE  = os.path.join(DIR, "ai-analysis.txt")  



NSE_HOLIDAYS = set([
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26",
    "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
])