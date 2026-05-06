"""Telegram alert sender — best signal per minute, Bangladesh Standard Time."""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MIN_GRADE

log = logging.getLogger(__name__)

BST = timezone(timedelta(hours=6))  # Bangladesh Standard Time = UTC+6

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    from aiogram import Bot
    from aiogram.enums import ParseMode
    _HAS_AIOGRAM = True
except ImportError:
    _HAS_AIOGRAM = False

# ── Per-minute best-signal state ─────────────────────────────────────────────

_last_telegram_time: float       = 0.0
_best_signal: Optional[dict]     = None
_best_confidence: float          = 0.0
TELEGRAM_INTERVAL_SEC: float     = 60.0

_GRADE_RANK = {"SKIP": 0, "MODERATE": 1, "HIGH": 2, "ELITE": 3}

_MODEL_LABELS: dict[str, str] = {
    "xgb":      "XGB",
    "lgbm":     "LGBM",
    "catboost": "CAT",
    "rf":       "RF",
    "extra":    "ET",
    "histgb":   "HGB",
    "lstm":     "LSTM",
    "cnn":      "CNN",
    "attn":     "ATTN",
    "tcn":      "TCN",
    "nbeats":   "N-BEATS",
    "rule":     "RULE",
    "bayes":    "BAYES",
    "stacking": "STACK",
}


def _meets_grade(signal: dict) -> bool:
    rank = _GRADE_RANK.get(str(signal.get("grade", "")), 0)
    min_rank = _GRADE_RANK.get(TELEGRAM_MIN_GRADE, 1)
    return rank >= min_rank


def update_best_signal(signal: dict, pair: str) -> None:
    """Called on every emitted signal — keeps the highest-confidence one for this minute."""
    global _best_signal, _best_confidence
    if not _meets_grade(signal):
        return
    conf = float(signal.get("confidence", 0))
    if conf > _best_confidence:
        _best_confidence = conf
        _best_signal = {**signal, "_pair": pair}


async def try_send_minute_signal() -> None:
    """Called every ~5s — sends best signal once per minute window."""
    global _last_telegram_time, _best_signal, _best_confidence

    now = time.monotonic()
    if now - _last_telegram_time < TELEGRAM_INTERVAL_SEC:
        return

    sig = _best_signal
    _last_telegram_time = now
    _best_signal        = None
    _best_confidence    = 0.0

    if sig is None:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    pair = str(sig.pop("_pair", "UNKNOWN"))
    msg  = _format_message(sig, pair)
    await _send(msg)


async def send_signal_alert(signal: dict, pair: str) -> None:
    """Legacy direct-send hook (kept for compatibility). Routes through best-signal tracker."""
    update_best_signal(signal, pair)


# ── Formatting ───────────────────────────────────────────────────────────────

def _format_message(signal: dict, pair: str) -> str:
    d         = str(signal.get("signal", "?"))
    conf      = float(signal.get("confidence", 0)) * 100
    grade_str = str(signal.get("grade", "?"))
    is_green  = d == "GREEN"

    pair_clean = (
        pair
        .replace("frx", "")
        .replace("USDT", "/USDT")
    )

    now_utc  = datetime.now(timezone.utc)
    now_bst  = now_utc.astimezone(BST)
    time_str = now_bst.strftime("%I:%M:%S %p BST")
    utc_str  = now_utc.strftime("%H:%M UTC")
    date_str = now_bst.strftime("%d %b %Y, %A")

    grade_emoji = {"ELITE": "👑", "HIGH": "✅", "MODERATE": "⚡"}.get(grade_str, "")
    dir_emoji   = "🟢" if is_green else "🔴"
    call_put    = "CALL/BUY" if is_green else "PUT/SELL"

    # Entry time
    open_ts  = signal.get("candle_open_time")
    close_ts = signal.get("candle_close_time")
    time_line = ""
    try:
        if open_ts and close_ts:
            entry = datetime.fromtimestamp(int(close_ts),      tz=timezone.utc)
            exit_ = datetime.fromtimestamp(int(close_ts) + 60, tz=timezone.utc)
            time_line = f"Entry {entry.strftime('%H:%M:%S')}–{exit_.strftime('%H:%M:%S')} UTC"
        elif open_ts:
            entry = datetime.fromtimestamp(int(open_ts) + 60,  tz=timezone.utc)
            exit_ = datetime.fromtimestamp(int(open_ts) + 120, tz=timezone.utc)
            time_line = f"Entry {entry.strftime('%H:%M:%S')}–{exit_.strftime('%H:%M:%S')} UTC"
    except Exception:
        pass

    # MTF
    mtf = signal.get("mtf_context", {}) or {}
    tf_line = ""
    kz_line = ""
    if isinstance(mtf, dict):
        tfs = mtf.get("tfs", {})
        if isinstance(tfs, dict):
            parts = []
            for tf_key, lbl in [("1h","1H"),("15m","15M"),("5m","5M"),("2m","2M")]:
                v = tfs.get(tf_key, {})
                b = v.get("bias", "?") if isinstance(v, dict) else "?"
                arrow = "↑" if b == "bull" else ("↓" if b == "bear" else "→")
                parts.append(f"{lbl}{arrow}")
            tf_line = " ".join(parts)
        kz = str(mtf.get("killzone", ""))
        _kz_labels = {
            "london":    "🇬🇧 London",
            "newyork":   "🇺🇸 New York",
            "asian":     "🇯🇵 Asian",
            "overlap":   "⚡ London+NY",
            "gold_comex":"🔥 COMEX Gold",
        }
        kz_line = _kz_labels.get(kz, "")

    # SMC
    smc = signal.get("smc_context", {}) or {}
    smc_parts = []
    if isinstance(smc, dict):
        if is_green and smc.get("price_at_bullish_ob"):    smc_parts.append("OB✓")
        elif not is_green and smc.get("price_at_bearish_ob"): smc_parts.append("OB✓")
        if is_green and smc.get("price_in_bullish_fvg"):   smc_parts.append("FVG✓")
        elif not is_green and smc.get("price_in_bearish_fvg"): smc_parts.append("FVG✓")
        if is_green and smc.get("bullish_bos"):            smc_parts.append("BOS✓")
        elif not is_green and smc.get("bearish_bos"):      smc_parts.append("BOS✓")
        if is_green and smc.get("in_ote_zone_bull"):       smc_parts.append("OTE✓")
        elif not is_green and smc.get("in_ote_zone_bear"): smc_parts.append("OTE✓")
        elif smc.get("in_ote_zone"):                       smc_parts.append("OTE✓")
        if smc.get("liquidity_swept"):                     smc_parts.append("LiqSweep✓")
        if is_green and smc.get("liq_sweep_bull"):         smc_parts.append("SSLSwept✓")
        elif not is_green and smc.get("liq_sweep_bear"):   smc_parts.append("BSLSwept✓")

    # Candle reaction
    react = signal.get("candle_reaction", {}) or {}
    react_line = ""
    if isinstance(react, dict):
        bull_s = int(react.get("bull_score", 0))
        bear_s = int(react.get("bear_score", 0))
        net    = float(react.get("net_score", 0))
        aligned_net = net if is_green else -net
        pin    = "PIN✓"    if (is_green and react.get("pin_bull"))    or (not is_green and react.get("pin_bear"))    else ""
        eng    = "ENGULF✓" if (is_green and react.get("bull_engulf")) or (not is_green and react.get("bear_engulf")) else ""
        flags  = " ".join(f for f in [pin, eng] if f)
        react_line = f"Bull:{bull_s} Bear:{bear_s} Net{int(aligned_net):+d}" + (f" {flags}" if flags else "")

    # Top models
    models = signal.get("ai_models", {}) or {}
    top3 = ""
    if isinstance(models, dict) and models:
        aligned = [
            (_MODEL_LABELS.get(k.lower(), k.upper()), (v if is_green else 1.0 - v))
            for k, v in models.items()
        ]
        aligned.sort(key=lambda x: x[1], reverse=True)
        top3 = " | ".join(f"{lbl} {int(v*100)}%" for lbl, v in aligned[:3])

    # Patterns
    patterns = signal.get("patterns", []) or []
    pattern_str = ", ".join(str(p).replace("_", " ") for p in patterns[:3])

    # Build message
    msg  = f"{dir_emoji} *{pair_clean}* — {d} ({call_put})\n"
    msg += f"Grade: {grade_emoji} {grade_str}  |  Confidence: {conf:.1f}%\n"
    msg += f"Trade: Time Candle 1M  |  Entry: IMMEDIATE\n"
    msg += f"\n"
    msg += f"📅 {date_str}\n"
    msg += f"🕐 {time_str}  ({utc_str})\n"
    if kz_line:
        msg += f"🌐 Session: {kz_line} Session\n"
    if time_line:
        msg += f"⏰ {time_line}\n"
    msg += f"\n"
    if tf_line:
        msg += f"📊 MTF: {tf_line}\n"
    if smc_parts:
        msg += f"🏛 SMC: {' '.join(smc_parts)}\n"
    if react_line:
        msg += f"⚡ Reaction: {react_line}\n"
    if top3:
        msg += f"🤖 Models: {top3}\n"
    if pattern_str:
        msg += f"📐 Pattern: {pattern_str}\n"
    msg += f"\n"
    msg += f"⚠️ Trade within 5 seconds!\n"
    msg += f"#PlybitAiSignals #{pair_clean.replace('/', '')}"

    return msg


# ── Transport ─────────────────────────────────────────────────────────────────

async def _send(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Prefer aiogram; fall back to raw httpx
    if _HAS_AIOGRAM:
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            await bot.session.close()
            return
        except Exception as e:
            log.warning("aiogram send failed, falling back to httpx: %s", e)

    if _HAS_HTTPX:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id":    TELEGRAM_CHAT_ID,
                        "text":       msg,
                        "parse_mode": "Markdown",
                    },
                )
                resp.raise_for_status()
        except Exception as e:
            log.error("Telegram httpx send failed: %s", e)
    else:
        log.error("No Telegram transport available (install aiogram or httpx)")
