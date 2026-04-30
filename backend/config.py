import os

# ── API Credentials ────────────────────────────────────────────
DERIVE_APP_ID = os.getenv("DERIVE_APP_ID", "114229")
DERIVE_TOKEN  = os.getenv("DERIVE_TOKEN",  "e2hm1s0aGzXO83I")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://localhost:6379")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Pairs (8 Forex majors + Gold) ────────────────────────────────
FOREX_PAIRS = [
    "frxXAUUSD",   # XAU/USD (Gold)
    "frxEURUSD",   # EUR/USD
    "frxGBPUSD",   # GBP/USD
    "frxUSDJPY",   # USD/JPY
    "frxAUDUSD",   # AUD/USD
    "frxUSDCAD",   # USD/CAD
    "frxNZDUSD",   # NZD/USD
    "frxUSDCHF",   # USD/CHF
]

# No index or crypto pairs — keeps training cycles focused per pair
ALL_PAIRS = list(FOREX_PAIRS)

# ── Candle Count ────────────────────────────────────────────────────
CANDLE_COUNT = {
    60:    1000,
    120:   500,
    300:   500,
    900:   300,
    3600:  300,
    14400: 200,
}
HISTORY_COUNT = 10000

# ── AI Training ──────────────────────────────────────────────────
MIN_CANDLES   = 150
GOOD_CANDLES  = 300
BEST_CANDLES  = 500
RETRAIN_EVERY = 20

# ── Signal Delivery ──────────────────────────────────────────────
SIGNAL_DELIVERY_TF   = 60
SIGNAL_EXPIRY_TYPE   = "time_candle"
SIGNAL_EXPIRY_BARS   = 1
SIGNAL_MAX_DELAY_SEC = 5

# ── Pre-Close Analysis ─────────────────────────────────────────────
PRE_CLOSE_WINDOW_SEC  = 10
PRE_CLOSE_MAX_SAMPLES = 20
PRE_CLOSE_INTERVAL    = 0.5

# ── 5M Window ────────────────────────────────────────────────────────────
WINDOW_SIZE_SEC        = 300
MAX_SIGNALS_PER_WINDOW = int(os.getenv("MAX_SIGNALS_PER_WINDOW", "3"))
MIN_WINDOW_POSITION    = int(os.getenv("MIN_WINDOW_POSITION", "0"))

# ── MTF Analysis ─────────────────────────────────────────────────────
MTF_ANALYSIS_TFS  = [3600, 900, 300, 120]
MTF_SIGNAL_TF     = 60
MTF_MIN_AGREEMENT = 0.60   # plan §9: < 60% → block
MTF_WEIGHTS       = {"1h": 0.35, "15m": 0.28, "5m": 0.22, "2m": 0.15}

# ── App Timeframes ────────────────────────────────────────────────────
APP_TF_PRIMARY   = [60, 300, 900]
APP_TF_SECONDARY = [120, 3600, 14400]

# ── Signal Quality ─────────────────────────────────────────────────────
GRADE_ELITE    = float(os.getenv("GRADE_ELITE", "0.82"))  # 👑
GRADE_HIGH     = float(os.getenv("GRADE_HIGH", "0.75"))   # ✅
GRADE_MODERATE = float(os.getenv("GRADE_MODERATE", "0.60"))  # ⚡
TELEGRAM_MIN_GRADE      = os.getenv("TELEGRAM_MIN_GRADE", "MODERATE").upper()
SIGNAL_THRESHOLD        = float(os.getenv("SIGNAL_THRESHOLD", str(GRADE_MODERATE)))
SIGNAL_CONFIRM_REQUIRED = 8
SIGNAL_MIN_EMIT_CONFIRM = 6

# ── Gold ───────────────────────────────────────────────────────────────────
GOLD_ATR_MULTIPLIER = 1.5
GOLD_COMEX_HOUR_UTC = 13
GOLD_COMEX_MIN_UTC  = 30

# ── Server ────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = int(os.getenv("PORT", "8003"))
