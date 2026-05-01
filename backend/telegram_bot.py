"""Telegram alert sender for configured minimum grade signals."""

import asyncio
import logging
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


def _format_message(signal: dict[str, object], pair: str) -> str:
    d          = signal.get("signal", "?")
    conf       = float(signal.get("confidence", 0)) * 100
    grade_str  = str(signal.get("grade", "?"))
    mtf        = signal.get("mtf_context", {})
    smc        = signal.get("smc_context", {})
    react      = signal.get("candle_reaction", {})
    models     = signal.get("ai_models", {})
    patterns   = signal.get("patterns", [])
    is_green   = d == "GREEN"

    pair_clean = pair.replace("frx", "").replace("USDT", "/USDT")

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
            tf_line += f"\n⚡ KZ: {kz.title()} Open Active"

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
        pin  = "PIN✓" if (is_green and react.get("pin_bull")) or (not is_green and react.get("pin_bear")) else ""
        eng  = "ENG✓" if (is_green and react.get("bull_engulf")) or (not is_green and react.get("bear_engulf")) else ""
        flags = " ".join(f for f in [pin, eng] if f)
        react_line = f"Net {int(aligned_net):+d}  {flags}".strip()

    top_models = ""
    if isinstance(models, dict):
        aligned = [(k, (v if is_green else 1.0 - v)) for k, v in models.items()]
        aligned.sort(key=lambda x: x[1], reverse=True)
        top_models = " | ".join(f"{k.upper()} {int(v*100)}%" for k, v in aligned[:3])

    pattern_line = ", ".join(str(p) for p in patterns[:3]) if patterns else ""

    # HTML format — more robust than MarkdownV2 with special chars
    action = "CALL / BUY" if is_green else "PUT / SELL"
    msg  = f"{_dir_emoji(d)} <b>{pair_clean}</b> — {d} ({action})\n"
    msg += f"Grade: {_grade_emoji(grade_str)} {grade_str}  |  Confidence: {conf:.1f}%\n"
    msg += f"Trade: Time Candle 1M  |  Entry: IMMEDIATE\n"
    if tf_line:
        msg += f"\n📊 <b>MTF:</b> {tf_line}\n"
    if smc_line:
        msg += f"🏛️ <b>SMC:</b> {smc_line}\n"
    if react_line:
        msg += f"⚡ <b>Reaction:</b> {react_line}\n"
    if top_models:
        msg += f"🤖 <b>Top Models:</b> {top_models}\n"
    if pattern_line:
        msg += f"📐 <b>Patterns:</b> {pattern_line}\n"
    msg += f"\n⏰ Max delay: 5 seconds!"
    msg += f"\n#PlaybitAI #{pair_clean.replace('/', '')}"

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
