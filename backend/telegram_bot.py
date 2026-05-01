"""Telegram alert sender for configured minimum grade signals."""

import asyncio
import logging
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MIN_GRADE

log = logging.getLogger(__name__)

try:
    from aiogram import Bot
    from aiogram.enums import ParseMode
    _HAS_AIOGRAM = True
except ImportError:
    _HAS_AIOGRAM = False


def _grade_emoji(grade: str) -> str:
    return {"ELITE": "👑", "HIGH": "✅", "MODERATE": "⚡"}.get(grade, "")


def _dir_emoji(direction: str) -> str:
    return "🟢" if direction == "GREEN" else "🔴"


_MODEL_LABELS: dict[str, str] = {
    "lstm": "LSTM", "cnn": "CNN", "attn": "ATTN", "tcn": "TCN", "nbeats": "N-BEATS",
    "bayes": "BAYES", "rule": "RULE",
    "xgboost": "XGB", "lightgbm": "LGB", "catboost": "CAT",
    "randomforest": "RF", "extratrees": "ET", "histgb": "HGB",
}


def _format_message(signal: dict[str, object], pair: str) -> str:
    d          = signal.get("signal", "?")
    conf       = float(signal.get("confidence", 0)) * 100
    grade_str  = str(signal.get("grade", "?"))
    mtf        = signal.get("mtf_context", {})
    smc        = signal.get("smc_context", {})
    react      = signal.get("candle_reaction", {})
    models     = signal.get("ai_models", {})
    open_ts    = signal.get("candle_open_time")
    close_ts   = signal.get("candle_close_time")
    is_green   = d == "GREEN"

    pair_clean = pair.replace("frx", "").replace("USDT", "/USDT")

    # Candle time range e.g. "14:23:00-14:24:00"
    time_line = ""
    try:
        if open_ts and close_ts:
            o_dt = datetime.fromtimestamp(int(open_ts),  tz=timezone.utc)
            c_dt = datetime.fromtimestamp(int(close_ts), tz=timezone.utc)
            time_line = f"{o_dt.strftime('%H:%M:%S')}-{c_dt.strftime('%H:%M:%S')}"
    except Exception:
        pass

    tf_line = ""
    if isinstance(mtf, dict):
        tfs = mtf.get("tfs", {})
        if isinstance(tfs, dict):
            parts = []
            for tf, label in [("1h", "1H"), ("15m", "15M"), ("5m", "5M"), ("2m", "2M")]:
                v = tfs.get(tf, {})
                b = v.get("bias", "?") if isinstance(v, dict) else "?"
                arrow = "↑" if b == "bull" else ("↓" if b == "bear" else "→")
                parts.append(f"{label}{arrow}")
            tf_line = " ".join(parts)
        kz = mtf.get("killzone", "none")
        if kz and kz != "none":
            tf_line += f"  ⚡️ {kz.title()} KZ"

    smc_line = ""
    if isinstance(smc, dict):
        checks = []
        if is_green and smc.get("price_at_bullish_ob"):
            checks.append("OB✓")
        elif not is_green and smc.get("price_at_bearish_ob"):
            checks.append("OB✓")
        if is_green and smc.get("price_in_bullish_fvg"):
            checks.append("FVG✓")
        elif not is_green and smc.get("price_in_bearish_fvg"):
            checks.append("FVG✓")
        if is_green and smc.get("bullish_bos"):
            checks.append("BOS✓")
        elif not is_green and smc.get("bearish_bos"):
            checks.append("BOS✓")
        if is_green and smc.get("in_ote_zone_bull"):
            checks.append("OTE✓")
        elif not is_green and smc.get("in_ote_zone_bear"):
            checks.append("OTE✓")
        elif smc.get("in_ote_zone"):
            checks.append("OTE✓")
        if smc.get("liquidity_swept"):
            checks.append("LQ✓")
        smc_line = " ".join(checks)

    react_line = ""
    if isinstance(react, dict):
        net = float(react.get("net_score", 0))
        aligned_net = net if is_green else -net
        pin = "PIN✓" if (is_green and react.get("pin_bull")) or (not is_green and react.get("pin_bear")) else ""
        eng = "ENG✓" if (is_green and react.get("bull_engulf")) or (not is_green and react.get("bear_engulf")) else ""
        flags = " ".join(f for f in [pin, eng] if f)
        react_line = f"Net {int(aligned_net):+d}" + (f"  {flags}" if flags else "")

    top_models = ""
    if isinstance(models, dict):
        aligned = [
            (_MODEL_LABELS.get(k.lower(), k.upper()), (v if is_green else 1.0 - v))
            for k, v in models.items()
        ]
        aligned.sort(key=lambda x: x[1], reverse=True)
        top_models = " | ".join(f"{lbl} {int(v*100)}%" for lbl, v in aligned[:3])

    # Build HTML message — layout matches user spec
    action = "CALL / BUY" if is_green else "PUT / SELL"
    msg  = f"{_dir_emoji(d)} <b>{pair_clean}</b> — {d} ({action})\n"
    msg += f"Grade: {_grade_emoji(grade_str)} {grade_str}  |  Confidence: {conf:.1f}%\n"
    if time_line:
        msg += f"\n(Time {time_line})\n"
    msg += f"Trade: Time Candle 1M  |  Entry: IMMEDIATE\n"
    if tf_line:
        msg += f"\n📊 MTF: {tf_line}\n"
    if smc_line:
        msg += f"🏛 SMC: {smc_line}\n"
    if react_line:
        msg += f"⚡️ Reaction: {react_line}\n"
    if top_models:
        msg += f"🤖 Top Models: {top_models}\n"
    msg += f"\n⏰ Max delay: 5 seconds!"
    msg += f"\n#PlybitAI #{pair_clean.replace('/', '')}"

    return msg


async def send_signal_alert(signal: dict[str, object], pair: str) -> None:
    """Send Telegram alert for signals at or above TELEGRAM_MIN_GRADE."""
    grade_str = str(signal.get("grade", "SKIP"))
    grade_rank = {"SKIP": 0, "MODERATE": 1, "HIGH": 2, "ELITE": 3}
    min_rank = grade_rank.get(TELEGRAM_MIN_GRADE, grade_rank["MODERATE"])
    if grade_rank.get(grade_str, 0) < min_rank:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if not _HAS_AIOGRAM:
        log.warning("aiogram not installed — Telegram alerts disabled")
        return

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        msg = _format_message(signal, pair)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode=ParseMode.HTML,
        )
        await bot.session.close()
    except Exception as e:
        log.error("Telegram send failed: %s", e)
