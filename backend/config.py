import os
from pathlib import Path

# Load .env from project root (parent of backend/) before reading env vars.
# Without this, os.getenv returns empty when running via `uvicorn backend.main`
# from a shell that hasn't exported them.
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=True)
except ImportError:
    # Fallback: manual .env parser (no python-dotenv installed)
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            os.environ.setdefault(_k, _v)

# ── API Credentials ────────────────────────────────────────────
DERIV_TOKEN  = os.getenv("DERIV_TOKEN",  "")
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "")
if not DERIV_TOKEN or not DERIV_APP_ID:
    import logging as _l
    _l.getLogger(__name__).warning(
        "DERIV_TOKEN or DERIV_APP_ID not set — "
        "add them to .env or the live listener will fail."
    )

# Quotex credentials — consumed by backend/quotex.py. Used only when main.py
# is switched to `import quotex as quotex_client`. Either email+password OR a
# pre-issued session token can be supplied; the connector prefers the token.
QUOTEX_EMAIL         = os.getenv("QUOTEX_EMAIL",         "")
QUOTEX_PASSWORD      = os.getenv("QUOTEX_PASSWORD",      "")
QUOTEX_SESSION_TOKEN = os.getenv("QUOTEX_SESSION_TOKEN", "")

REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Pairs (8 Forex majors + Gold) — Quotex naming ───────────────
FOREX_PAIRS = [
    "XAUUSD",   # XAU/USD (Gold)
    "EURUSD",   # EUR/USD
    "GBPUSD",   # GBP/USD
    "USDJPY",   # USD/JPY
    "AUDUSD",   # AUD/USD
    "USDCAD",   # USD/CAD
    "NZDUSD",   # NZD/USD
    "USDCHF",   # USD/CHF
]

# No index or crypto pairs — keeps training cycles focused per pair
ALL_PAIRS = list(FOREX_PAIRS)

# ── Candle Counts ───────────────────────────────────────────────────

# 5000 candles per pair per timeframe (stored in SQLite + in-memory)
CHART_CANDLE_COUNT = {
    60:    5000,
    120:   5000,
    300:   5000,
    900:   5000,
    3600:  5000,
    14400: 5000,
}

# How many candles the SIGNAL PIPELINE uses for fast analysis
ANALYSIS_CANDLE_COUNT = {
    60:    500,
    120:   300,
    300:   300,
    900:   200,
    3600:  200,
    14400: 150,
}

# Legacy alias
CANDLE_COUNT = CHART_CANDLE_COUNT

# In-memory ring buffer per pair/granularity
HISTORY_COUNT = 6000

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
MTF_MIN_AGREEMENT = 0.40   # plan §9: < 40% → soft penalty
MTF_WEIGHTS       = {"1h": 0.35, "15m": 0.28, "5m": 0.22, "2m": 0.15}

# ── App Timeframes ────────────────────────────────────────────────────
APP_TF_PRIMARY   = [60, 300, 900]
APP_TF_SECONDARY = [120, 3600, 14400]

# ── Signal Quality ─────────────────────────────────────────────────────
GRADE_ELITE    = float(os.getenv("GRADE_ELITE", "0.82"))  # 👑
GRADE_HIGH     = float(os.getenv("GRADE_HIGH", "0.75"))   # ✅
GRADE_MODERATE = float(os.getenv("GRADE_MODERATE", "0.62"))  # ⚡
TELEGRAM_MIN_GRADE      = os.getenv("TELEGRAM_MIN_GRADE", "HIGH").upper()
SIGNAL_THRESHOLD        = float(os.getenv("SIGNAL_THRESHOLD", str(GRADE_MODERATE)))
SIGNAL_CONFIRM_REQUIRED = 8
SIGNAL_MIN_EMIT_CONFIRM = int(os.getenv("SIGNAL_MIN_EMIT_CONFIRM", "3"))

# ── Gold ───────────────────────────────────────────────────────────────────
GOLD_ATR_MULTIPLIER = 1.5
GOLD_COMEX_HOUR_UTC = 13
GOLD_COMEX_MIN_UTC  = 30

# ── Server ────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = int(os.getenv("PORT", "8003"))
