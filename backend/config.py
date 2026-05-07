import os

# ── API Credentials ────────────────────────────────────────────
DERIVE_APP_ID = os.getenv("DERIVE_APP_ID", "114229")
DERIVE_TOKEN  = os.getenv("DERIVE_TOKEN",  "")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://localhost:6379")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
TELEGRAM_MIN_GRADE = os.getenv("TELEGRAM_MIN_GRADE",  "MODERATE")

# ── Pairs (8 majors + Gold) ──────────────────────────────────────
FOREX_PAIRS = [
    "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxUSDCHF",
    "frxUSDCAD", "frxAUDUSD", "frxNZDUSD", "frxXAUUSD",
]
ALL_PAIRS = FOREX_PAIRS

# ── Candle Count ────────────────────────────────────────────────────
CANDLE_COUNT = {
    60:    500,     # 1M  — ~8 hours
    120:   300,     # 2M  — ~10 hours
    300:   1000,    # 5M  — ~3.5 days
    900:   2000,    # 15M — ~21 days
    3600:  6000,    # 1H  — ~250 days
    14400: 9000,    # 4H  — ~5 years
    86400: 3650,    # 1D  — 10 years
}
CHART_DISPLAY_COUNT = {
    60: 300, 120: 200, 300: 500,
    900: 1000, 3600: 3000, 14400: 5000, 86400: 3650,
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
MAX_SIGNALS_PER_WINDOW = 3
MIN_WINDOW_POSITION    = 1

# ── MTF Analysis ─────────────────────────────────────────────────────
MTF_ANALYSIS_TFS  = [3600, 900, 300, 120]
MTF_SIGNAL_TF     = 60
MTF_MIN_AGREEMENT = 0.42       # Minimum weighted TF consensus for signal
MTF_WEIGHTS       = {"1h": 0.35, "15m": 0.28, "5m": 0.22, "2m": 0.15}
MTF_H1_15M_ALIGN  = True       # 1H and 15M must NOT be opposite (neutral OK)
MTF_PTS_MIN       = 18         # At least 2 TFs aligned with signal direction

# ── App Timeframes ────────────────────────────────────────────────────
APP_TF_PRIMARY   = [60, 300, 900]
APP_TF_SECONDARY = [120, 3600, 14400, 86400]

# ── Signal Quality ─────────────────────────────────────────────────────
GRADE_ELITE    = 0.82
GRADE_HIGH     = 0.75
GRADE_MODERATE = 0.52          # Plan: minimum 52% for any signal output
SIGNAL_THRESHOLD        = 0.75
SIGNAL_CONFIRM_REQUIRED = 8
SIGNAL_MIN_EMIT_CONFIRM = 3

# ── Per-Layer Minimums (Plan requirements) ─────────────────────────────
SMC_PTS_MIN         = 12       # Plan: SMC layer must score 12+/30
AI_MIN_MODELS_AGREE = 8        # Plan: 8/13+ models must agree with direction
AI_MIN_AVG_CONF     = 0.59     # Plan: average model confidence must be 59%+

# ── Gold ────────────────────────────────────────────────────────────────
GOLD_ATR_MULTIPLIER = 1.5
GOLD_COMEX_HOUR_UTC = 13
GOLD_COMEX_MIN_UTC  = 30

# ── Server ────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = int(os.getenv("PORT", "8003"))
